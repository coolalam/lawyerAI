import pytest
from fastapi.testclient import TestClient
from backend import db as storage, main
from backend.security import token_hash
from tests.test_migration import mirror


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'admin.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    with TestClient(main.app) as c:
        c.post('/api/setup',json={'username':'admin','name':'管理员','password':'Test-password-2026'})
        c.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'})
        yield c


def seed_sources():
    with storage.db() as conn:
        mirror(conn,'t_department',[{'dp_office_id':'SH','dp_dept_id':'D1','dp_description':'建设工程'},
                                    {'dp_office_id':'BJ','dp_dept_id':'D1','dp_description':'知识产权'}])
        mirror(conn,'t_case_class',[{'cc_case_class':'C1','cc_class_name':'合同纠纷','cc_super_class':''}])
        storage.ensure_managed_references(conn)


def test_overlay_seed_late_import_idempotence_and_history(client):
    # Application states already exist when source dictionaries arrive later.
    seed_sources()
    departments=client.get('/api/admin/departments').json()['items']
    sh=next(x for x in departments if x['office']=='SH')
    cause=client.get('/api/admin/metadata?category=case_cause').json()['items'][0]
    with storage.db() as conn:
        record=storage.save_record(conn,'cases',{'title':'历史案','office':'SH','number':'OLD1','status':'active','amount':'0',
            'department':'建设工程','cause':'合同纠纷','legacy_table':'t_case',
            'legacy_data':{'ca_office_id':'SH','ca_dept_id':'D1','ca_reason':'C1'}},'migration',source_key='legacy:case1')
        original=conn.execute('SELECT * FROM src__t_department ORDER BY _migration_row_id').fetchall()
    assert client.patch('/api/admin/departments/'+sh['id'],json={'name':'工程业务部','active':False}).status_code==200
    assert client.patch('/api/admin/metadata/'+cause['id'],json={'name':'合同争议','active':False}).status_code==200
    with storage.db() as conn:
        storage.ensure_managed_references(conn)
        assert conn.execute('SELECT COUNT(*) FROM managed_departments').fetchone()[0]==2
        assert conn.execute('SELECT * FROM src__t_department ORDER BY _migration_row_id').fetchall()==original
    item=client.get('/api/records/cases/'+record['id']).json()
    assert item['department']=='工程业务部' and item['cause']=='合同争议'
    schema=client.get('/api/schema').json()
    assert next(f for f in schema['schemas']['cases']['fields'] if f['key']=='cause')['suggestions']==[]
    assert client.patch('/api/admin/metadata/'+cause['id'],json={'code':'new'}).status_code==422
    assert client.patch('/api/admin/departments/'+sh['id'],json={'office':'OTHER'}).status_code==422


def test_member_transfer_counts_version_and_source_preservation(client):
    seed_sources()
    departments=client.get('/api/admin/departments').json()['items']
    sh=next(x for x in departments if x['office']=='SH'); bj=next(x for x in departments if x['office']=='BJ')
    with storage.db() as conn:
        raw={'emp_empl_id':'EMP1','emp_office_id':'SH','emp_dept_id':'D1'}
        person=storage.save_record(conn,'lawyers',{'name':'甲律师','office':'SH','department':'建设工程','status':'active',
                    'legacy_table':'t_employee','legacy_data':raw},'migration',source_key='legacy:person1')
    user=client.post('/api/admin/users',json={'username':'EMP1','name':'甲律师','role':'lawyer','lawyer_id':person['id'],'reset_required':True})
    assert user.status_code==200
    members=client.get('/api/admin/members?q=EMP1').json()
    assert members['total']==1 and members['items'][0]['account']['needs_password_reset']
    response=client.patch('/api/admin/members/'+person['id'],json={'department_id':bj['id'],'version':1})
    assert response.status_code==200
    assert response.json()['legacy_data']==raw
    assert client.patch('/api/admin/members/'+person['id'],json={'name':'过期编辑','version':1}).status_code==409
    rows={x['id']:x for x in client.get('/api/admin/departments').json()['items']}
    assert rows[sh['id']]['employee_count']==0
    assert rows[bj['id']]['employee_count']==1 and rows[bj['id']]['account_count']==1
    assert client.get('/api/admin/members?department='+bj['id']).json()['total']==1
    assert client.get('/api/admin/members?department='+sh['id']).json()['total']==0
    assert client.patch('/api/admin/departments/'+bj['id'],json={'name':'新的知识产权部'}).status_code==200
    assert client.get('/api/records/lawyers/'+person['id']).json()['department']=='新的知识产权部'


def test_member_account_change_revokes_sessions_and_protects_admin(client):
    person=client.post('/api/records/lawyers',json={'name':'成员'}).json()
    uid=client.post('/api/admin/users',json={'username':'member','name':'成员','role':'lawyer','lawyer_id':person['id'],'password':'Test-password-2026'}).json()['id']
    second=TestClient(main.app)
    assert second.post('/api/auth/login',json={'username':'member','password':'Test-password-2026'}).status_code==200
    token=client.post(f'/api/admin/users/{uid}/password-reset-token',json={'verification_note':'已当面核验身份'}).json()['token']
    assert client.patch('/api/admin/members/'+person['id'],json={'version':1,'account':{'id':uid,'role':'clerk','active':False}}).status_code==200
    assert second.get('/api/auth/me').status_code==401
    with storage.db() as conn:
        assert not conn.execute('SELECT 1 FROM password_reset_tokens WHERE token_hash=?',(token_hash(token),)).fetchone()
        admin=conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        conn.execute('UPDATE users SET lawyer_id=? WHERE id=?',(person['id'],admin))
    response=client.patch('/api/admin/members/'+person['id'],json={'version':2,'name':'不能保存','account':{'id':admin,'role':'lawyer','active':True}})
    assert response.status_code==422
    assert client.get('/api/records/lawyers/'+person['id']).json()['version']==2
    member=client.get('/api/admin/members').json()['items'][0]
    assert len(member['accounts'])==2 and 'password_hash' not in str(member)


def test_metadata_management_pagination_labels_and_authorization(client):
    for index in range(3):
        assert client.post('/api/admin/metadata',json={'category':'case_cause','code':f'C{index}','name':f'案由{index}'}).status_code==200
    result=client.get('/api/admin/metadata?category=case_cause&page_size=2&page=2').json()
    assert result['total']==3 and len(result['items'])==1
    assert client.post('/api/admin/metadata',json={'category':'case_cause','code':'C1','name':'重复'}).status_code==409
    state=next(x for x in client.get('/api/admin/metadata?category=case_status').json()['items'] if x['code']=='active')
    assert client.patch('/api/admin/metadata/'+state['id'],json={'name':'办理阶段'}).status_code==200
    schema=client.get('/api/schema').json()
    assert schema['states']['cases']['active']=='办理阶段'
    assert schema['transitions']['cases']['submitted']==['active','rejected']
    client.post('/api/admin/users',json={'username':'staff','name':'行政','role':'clerk','password':'Test-password-2026'})
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'staff','password':'Test-password-2026'})
    for path in ('departments','members','metadata','metadata/categories'):
        assert client.get('/api/admin/'+path).status_code==403
    assert client.post('/api/admin/metadata',json={}).status_code==403
    assert client.patch('/api/admin/members/unknown',json={}).status_code==403


def test_branches_resolve_by_area_preserve_history_and_transfer(client):
    with storage.db() as conn:
        mirror(conn,'t_suboffice_info',[
            {'soi_office_id':'O1056','soi_area':'TY','soi_name':'太原'},
            {'soi_office_id':'O1056','soi_area':'CD','soi_name':'成都'}])
        mirror(conn,'t_department',[
            {'dp_office_id':'O1056','dp_dept_id':'D1','dp_description':'业务部','dp_area':'TY'},
            {'dp_office_id':'O1056','dp_dept_id':'D2','dp_description':'业务部','dp_area':'CD'}])
        storage.ensure_managed_references(conn)
        source=[tuple(r) for r in conn.execute('SELECT * FROM src__t_department')]
        people=[]
        for number,area,dept,status in [('1','TY','D1','active'),('2','TY','D1','inactive'),('3','CD','D2','inactive'),('4','CD','D1','active'),('5','TY','','active')]:
            raw={'emp_empl_id':number,'emp_office_id':'O1056','emp_dept_id':dept,'emp_area':area,'emp_status':'X' if status=='inactive' else 'A'}
            people.append(storage.save_record(conn,'lawyers',{'name':'张晓玲','office':'O1056','department':dept,'status':'active','legacy_table':'t_employee','legacy_data':raw},'migration'))
    members=client.get('/api/admin/members?q=张晓玲').json()['items']
    assert len(members)==5
    assert {m['employee_id']:m['branch_name'] for m in members}=={'1':'太原','2':'太原','3':'成都','4':'成都','5':'太原'}
    assert all(m['office']=='O1056' for m in members)
    assert {m['employee_id'] for m in members if m['status']=='inactive'}=={'2','3'}
    assert client.get('/api/records/lawyers?status=inactive').json()['total']==2
    assert client.get('/api/records/lawyers?status=active').json()['total']==3
    assert client.patch('/api/admin/members/'+people[2]['id'],json={'version':1,'title':'历史成员'}).status_code==200
    assert client.get('/api/records/lawyers/'+people[2]['id']).json()['status']=='inactive'
    assert client.patch('/api/admin/members/'+people[1]['id'],json={'version':1,'status':'active'}).status_code==200
    assert client.get('/api/records/lawyers/'+people[1]['id']).json()['status']=='active'
    assert client.get('/api/admin/members?q=太原').json()['total']==3
    for m in members:
        detail=client.get('/api/records/lawyers/'+m['id']).json()
        assert detail['branch_code']==m['branch_code']
    schema=client.get('/api/schema').json()['schemas']['lawyers']
    assert {b['code']:b['name'] for b in schema['branches']}=={'TY':'太原','CD':'成都'}
    depts={d['code']:d for d in client.get('/api/admin/departments').json()['items']}
    assert depts['D1']['branch_name']=='太原'
    assert depts['D2']['branch_name']=='成都'
    new=client.post('/api/records/lawyers',json={'name':'新增成都成员','office':'O1056','department':'业务部','department_id':depts['D2']['id']}).json()
    assert client.get('/api/records/lawyers/'+new['id']).json()['branch_name']=='成都'
    assert client.patch('/api/admin/members/'+people[0]['id'],json={'version':1,'department_id':depts['D2']['id']}).status_code==200
    transferred=client.get('/api/records/lawyers/'+people[0]['id']).json()
    assert transferred['branch_name']=='成都' and transferred['legacy_data']['emp_area']=='TY'
    added=client.post('/api/admin/departments',json={'office':'O1056','branch_code':'TY','code':'D3','name':'新业务部'})
    assert added.status_code==200
    with storage.db() as conn:
        storage.ensure_managed_references(conn)
        assert [tuple(r) for r in conn.execute('SELECT * FROM src__t_department')]==source
    assert next(d for d in client.get('/api/admin/departments').json()['items'] if d['code']=='D3')['branch_name']=='太原'
