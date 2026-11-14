import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend import db as storage, main
from backend.security import token_hash
from migration.project import stable_id,case_id
from migration.repair import import_accounts,repair_conflicts
from migration.references import References
from tests.test_migration import mirror


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'test.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    with TestClient(main.app) as client:
        client.post('/api/setup',json={'username':'admin','name':'管理员','password':'Test-password-2026'})
        client.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'})
        yield client


def test_dictionary_office_scope_deepest_cause_and_manual_edit(client):
    with storage.db() as conn:
        mirror(conn,'t_department',[{'dp_office_id':'SH','dp_dept_id':'D1','dp_description':'建设工程'},
                                    {'dp_office_id':'BJ','dp_dept_id':'D1','dp_description':'知识产权'}])
        mirror(conn,'t_case_class',[{'cc_case_class':'MS4','cc_class_name':'债权纠纷'},
                                  {'cc_case_class':'M4_1','cc_class_name':'合同纠纷'},
                                  {'cc_case_class':'M4_1_25','cc_class_name':'建设工程合同纠纷'}])
        refs=References(conn)
        assert refs.department('BJ','D1')=='知识产权'
        assert refs.department('missing','D1')=='D1'
        raw={'ca_office_id':'SH','ca_dept_id':'D1','ca_reason':'MS4','ca_reason_type':'M4_1','ca_reason_type1':'M4_1_25','ca_reasonBC':''}
        record=storage.save_record(conn,'cases',{'title':'历史案件','number':'REF1','office':'SH','status':'active','amount':'0',
            'department':'D1','cause':'MS4','legacy_table':'t_case','legacy_data':raw},'migration',source_key='jw.test')
    detail=client.get('/api/records/cases/'+record['id']).json()
    assert detail['department']=='建设工程' and detail['cause']=='建设工程合同纠纷'
    assert detail['legacy_data']['ca_reason']=='MS4'
    listed=client.get('/api/records/cases').json()['items'][0]
    assert listed['department']=='建设工程'
    with storage.db() as conn:
        record=storage.get_record(conn,record['id'])
        record.update(department='手工部门',cause='手工案由')
        storage.save_record(conn,'cases',record,'admin',record_id=record['id'])
    detail=client.get('/api/records/cases/'+record['id']).json()
    assert detail['cause']=='手工案由' and detail['department']=='手工部门'


def test_account_import_no_password_no_privilege_escalation_idempotent(client):
    with storage.db() as conn:
        mirror(conn,'t_employee',[{'emp_empl_id':'OA1','emp_user_id':None,'emp_name':'甲','emp_status':'A','emp_leave_date':None},
                                  {'emp_empl_id':'OA2','emp_user_id':None,'emp_name':'乙','emp_status':'X','emp_leave_date':None}])
        for key in ('OA1','OA2'):
            storage.save_record(conn,'lawyers',{'name':key},'migration',record_id=stable_id('t_employee',[key]))
        mirror(conn,'t_user',[{'us_user_id':key,'us_is_group':group,'us_status':'A','us_empl_id':None,'us_user_name':'成员','us_description':''} for key,group in [('OA1','0'),('OA2','0'),('other','0'),('group','1')]])
        result=import_accounts(conn)
        assert result['created']==3 and result['active']==1 and result['groups_skipped']==1
        assert import_accounts(conn)['created']==0
        user=conn.execute("SELECT * FROM users WHERE username='OA1'").fetchone()
        assert user['role']=='lawyer' and user['password_hash']=='!'
        assert user['lawyer_id']==stable_id('t_employee',['OA1'])
        assert conn.execute("SELECT active FROM users WHERE username='OA2'").fetchone()[0]==0
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'username':'OA1','password':'whatever'}).status_code==401


def test_password_recovery_single_use_expiry_and_revocation(client):
    member=client.post('/api/admin/users',json={'username':'member','name':'成员','role':'clerk','password':'Old-password-2026'}).json()['id']
    client.post('/api/auth/logout')
    unknown=client.post('/api/auth/forgot-password',json={'username':'unknown'}).json()
    known=client.post('/api/auth/forgot-password',json={'username':'member'}).json()
    assert unknown==known and 'token' not in known
    assert client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).status_code==401
    client.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'})
    assert client.get('/api/admin/password-reset-requests').json()[0]['user_id']==member
    first=client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).json()['token']
    second=client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).json()['token']
    with storage.db() as conn:
        stored=conn.execute('SELECT token_hash FROM password_reset_tokens').fetchone()[0]
        assert stored==token_hash(second) and stored!=second
    client.post('/api/auth/logout')
    assert client.post('/api/auth/reset-password',json={'token':first,'password':'New-password-2026'}).status_code==422
    assert client.post('/api/auth/login',json={'username':'member','password':'Old-password-2026'}).status_code==200
    assert client.post('/api/auth/reset-password',json={'token':second,'password':'New-password-2026'}).status_code==200
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/reset-password',json={'token':second,'password':'Another-password-2026'}).status_code==422
    assert client.post('/api/auth/login',json={'username':'member','password':'New-password-2026'}).status_code==200
    assert client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).status_code==403
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'})
    expired=client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).json()['token']
    with storage.db() as conn:conn.execute("UPDATE password_reset_tokens SET expires_at='2000-01-01'")
    assert client.post('/api/auth/reset-password',json={'token':expired,'password':'Expired-password-2026'}).status_code==422
    pending=client.post(f'/api/admin/users/{member}/password-reset-token',json={'verification_note':'当面核验身份完成'}).json()['token']
    client.patch('/api/admin/users/'+member,json={'active':False})
    assert client.post('/api/auth/reset-password',json={'token':pending,'password':'Disabled-password-2026'}).status_code==422


def test_forgot_password_rate_limit(client):
    for _ in range(10):assert client.post('/api/auth/forgot-password',json={'username':'missing'}).status_code==200
    assert client.post('/api/auth/forgot-password',json={'username':'missing'}).status_code==429


def test_conflict_relink_and_snapshot_permission_pagination(client):
    with storage.db() as conn:
        mirror(conn,'t_case',[{'ca_office_id':'SH','ca_case_id':'CASE1','ca_temp_id':'TEMP1','ca_csm1_result':None,'ca_csm1_result0':None,'ca_csm1_description':None,'ca_conflictor':None,'ca_check_date':None}])
        identifier=case_id('SH','CASE1')
        storage.save_record(conn,'cases',{'title':'案件','status':'active','office':'SH','number':'CASE1'},'migration',record_id=identifier)
        report=storage.save_record(conn,'conflicts',{'status':'legacy:A','matches':[],'legacy_key':['S1'],'legacy_data':{'csm_search_id':'S1','csm_office_id':'SH','csm_case_id':'temp1'}},'migration',source_key='jw.conflict')
        conn.execute('CREATE TABLE migration_issues(table_name TEXT,source_key TEXT,code TEXT,resolved INTEGER)')
        mirror(conn,'t_conflict_search_det',[{'csd_search_id':'S1','csd_line':str(i),'csd_to_search':'主体','csd_client_name':'历史主体','csd_case_id':'OTHER','csd_case_name':'历史案件','csd_client_type':'对方','csd_status':'A','csd_memo':''} for i in range(25)])
        assert repair_conflicts(conn)['linked']==1
        assert repair_conflicts(conn)['linked']==0
    snapshot=client.get(f"/api/conflicts/{report['id']}/snapshot?page=2").json()
    assert snapshot['total']==25 and len(snapshot['items'])==5 and snapshot['items'][0]['line']=='20'
    assert 'case_id' not in snapshot['items'][0]
    assert client.get('/api/records/conflicts',params={'case_id':identifier}).json()['total']==1
    lawyer=client.post('/api/records/lawyers',json={'name':'无关律师'}).json()
    client.post('/api/admin/users',json={'username':'other','name':'无关律师','role':'lawyer','lawyer_id':lawyer['id'],'password':'Other-password-2026'})
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'other','password':'Other-password-2026'})
    assert client.get(f"/api/conflicts/{report['id']}/snapshot").status_code==403
