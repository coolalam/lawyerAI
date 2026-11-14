import io
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from backend import db as storage
from backend import main, seed
from backend.service import split_amount


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'office.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.setattr(seed,'DATA',tmp_path)
    with TestClient(main.app) as c:
        assert c.post('/api/setup',json={'username':'admin','name':'管理员','password':'Test-password-2026'}).status_code==200
        assert c.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'}).status_code==200
        yield c


def create(c,kind,data):
    r=c.post('/api/records/'+kind,json=data)
    assert r.status_code==201,r.text
    return r.json()


def lawyer(c,name='测试律师'):
    return create(c,'lawyers',{'name':name})


def case(c,person=None,number='TEST-001',client_name='甲方企业'):
    person=person or lawyer(c)
    return create(c,'cases',{'number':number,'title':'测试委托案件','client':client_name,'opponent':'乙方企业',
        'lead_lawyer_id':person['id'],'office':'上海','category':'民事诉讼','opened_on':'2026-11-12','amount':'1000.01'})


def step(c,item,status,note='测试审批意见'):
    item=c.get(f"/api/records/{item['kind']}/{item['id']}").json()
    r=c.post(f"/api/records/{item['kind']}/{item['id']}/transition",json={'status':status,'note':note,'version':item['version']})
    assert r.status_code==200,r.text
    return r.json()


def activate(c,item):
    conflict=c.post(f"/api/cases/{item['id']}/conflicts",json={}).json()
    step(c,conflict,'waived' if conflict['matches'] else 'cleared')
    item=step(c,item,'draft');item=step(c,item,'submitted');return step(c,item,'active')


def allocate(c,item,rows):
    current=c.get(f"/api/records/{item['kind']}/{item['id']}").json()
    return c.put(f"/api/cases/{item['id'] if item['kind']=='cases' else item['case_id']}/allocations",json={'scope_id':item['id'],'version':current['version'],'rows':rows})


def upload(c,item,title='合同原件'):
    r=c.post('/api/documents/upload',data={'case_id':item['id'],'title':title,'category':'委托合同'},files={'file':('contract.txt','真实测试文件内容'.encode(),'text/plain')})
    assert r.status_code==201,r.text
    return r.json()


def test_intake_requires_conflict_and_approval(client):
    item=case(client)
    r=client.post(f"/api/records/cases/{item['id']}/transition",json={'status':'active','version':1,'note':'跳步'})
    assert r.status_code==409
    r=client.post(f"/api/records/cases/{item['id']}/transition",json={'status':'draft','version':1,'note':'跳过利冲'})
    assert r.status_code==409
    assert client.post('/api/records/invoices',json={'case_id':item['id']}).status_code==409
    approved=activate(client,item)
    assert approved['status']=='active'
    assert [x['to'] for x in approved['history']]==['draft','submitted','active']


def test_conflict_is_invalidated_by_party_changes(client):
    item=case(client)
    report=client.post(f"/api/cases/{item['id']}/conflicts",json={}).json()
    step(client,report,'cleared')
    create(client,'parties',{'case_id':item['id'],'name':'新关联企业','role':'第三人'})
    r=client.post(f"/api/records/cases/{item['id']}/transition",json={'status':'draft','version':1,'note':'资料改变'})
    assert r.status_code==409


def test_conflict_hits_require_waiver(client):
    case(client,number='FIRST',client_name='关联公司')
    item=case(client,number='SECOND',client_name='关联公司')
    report=client.post(f"/api/cases/{item['id']}/conflicts",json={}).json()
    assert len(report['matches'])>=1
    r=client.post(f"/api/records/conflicts/{report['id']}/transition",json={'status':'cleared','version':1,'note':'试图直接通过'})
    assert r.status_code==422
    assert step(client,report,'waived')['status']=='waived'


def test_finance_exact_allocation_snapshots_and_reversal(client):
    a,b=lawyer(client,'律师甲'),lawyer(client,'律师乙')
    item=activate(client,case(client,a))
    shares=[{'lawyer_id':a['id'],'ratio':'50','role':'主办'},{'lawyer_id':b['id'],'ratio':'50','role':'协办'}]
    assert allocate(client,item,shares).status_code==200
    invoice=create(client,'invoices',{'case_id':item['id'],'title':'甲方企业','tax_id':'TEST-TAX','amount':'1000.01','tax_rate':'6','type':'增值税专用发票','number':'IV-001','issued_on':'2026-11-12'})
    assert invoice['tax_amount']=='56.60'
    invoice=step(client,step(client,step(client,invoice,'submitted'),'approved'),'issued')
    assert sum(Decimal(r['amount']) for r in invoice['allocation_snapshot'])==Decimal('1000.01')
    assert sorted(r['amount'] for r in invoice['allocation_snapshot'])==['500.00','500.01']
    receipt=create(client,'receipts',{'case_id':item['id'],'title':'首期到账','amount':'1000.01','payer':'甲方企业','received_on':'2026-11-12','reference':'BANK-001','invoice_id':invoice['id']})
    receipt=step(client,receipt,'confirmed')
    assert client.get(f"/api/records/cases/{item['id']}").json()['finance']['received']=='1000.01'
    assert allocate(client,item,[{'lawyer_id':a['id'],'ratio':'100'}]).status_code==200
    assert client.get(f"/api/records/receipts/{receipt['id']}").json()['allocation_snapshot']==receipt['allocation_snapshot']
    r=client.patch(f"/api/records/receipts/{receipt['id']}",json={'amount':'20','version':receipt['version']})
    assert r.status_code==409
    r=client.post(f"/api/records/invoices/{invoice['id']}/transition",json={'status':'void','note':'未冲销到账','version':invoice['version']})
    assert r.status_code==409
    step(client,receipt,'void')
    assert client.get(f"/api/records/cases/{item['id']}").json()['finance']['received']=='0.00'
    assert step(client,invoice,'void')['status']=='void'


def test_contract_stamp_archive_process(client):
    item=activate(client,case(client))
    original=upload(client,item)
    contract=create(client,'contracts',{'case_id':item['id'],'title':'委托代理合同','number':'HT-001','document_id':original['id']})
    contract=step(client,contract,'stamping')
    seal=client.get(f"/api/records/seals/{contract['seal_id']}").json()
    assert seal['status']=='submitted'
    seal=step(client,seal,'approved')
    bad=client.post(f"/api/records/seals/{seal['id']}/transition",json={'status':'stamped','note':'未提供附件','version':seal['version']})
    assert bad.status_code==422
    stamped=upload(client,item,'已盖章合同')
    seal=client.patch(f"/api/records/seals/{seal['id']}",json={'archive_document_id':stamped['id'],'collector':'领取人','version':seal['version']}).json()
    seal=step(client,seal,'stamped')
    assert step(client,seal,'collected')['status']=='collected'
    contract=client.get(f"/api/records/contracts/{contract['id']}").json()
    assert contract['status']=='stamped'
    updated=client.patch(f"/api/records/contracts/{contract['id']}",json={'archive_number':'ARCH-001','archive_location':'A 柜 1 层','version':contract['version']})
    assert updated.status_code==200,updated.text
    archived=step(client,updated.json(),'archived')
    assert archived['status']=='archived'
    assert client.get(f"/api/documents/{stamped['id']}/download").content=='真实测试文件内容'.encode()
    assert client.delete(f"/api/records/documents/{stamped['id']}?version={stamped['version']}").status_code==409


def test_optimistic_lock_and_reserved_fields(client):
    item=case(client)
    assert client.patch(f"/api/records/cases/{item['id']}",json={'title':'新标题','version':1}).status_code==200
    assert client.patch(f"/api/records/cases/{item['id']}",json={'title':'覆盖更新','version':1}).status_code==409
    assert client.patch(f"/api/records/cases/{item['id']}",json={'status':'active','version':2}).status_code==422


def test_currency_precision_and_invalid_distribution(client):
    person=lawyer(client);item=case(client,person)
    assert allocate(client,item,[{'lawyer_id':person['id'],'ratio':'99.99'}]).status_code==422
    assert client.patch(f"/api/records/cases/{item['id']}",json={'amount':'0.001','version':1}).status_code==422
    assert client.patch(f"/api/records/cases/{item['id']}",json={'amount':'NaN','version':1}).status_code==422
    rows=split_amount('0.01',[{'lawyer_id':'a','ratio':'33.3333'},{'lawyer_id':'b','ratio':'33.3333'},{'lawyer_id':'c','ratio':'33.3334'}])
    assert [r['amount'] for r in rows]==['0.00','0.00','0.01']


def test_role_case_scope_and_approval_protection(client):
    person,other=lawyer(client,'自己的律师'),lawyer(client,'其他律师')
    own=case(client,person,'OWN');foreign=case(client,other,'OTHER')
    result=client.post('/api/admin/users',json={'username':'lawyer','password':'Test-lawyer-2026','name':'办案律师','role':'lawyer','lawyer_id':person['id']})
    assert result.status_code==200
    client.post('/api/auth/logout')
    client.post('/api/auth/login',json={'username':'lawyer','password':'Test-lawyer-2026'})
    assert client.get(f"/api/records/cases/{own['id']}").status_code==200
    assert client.get(f"/api/records/cases/{foreign['id']}").status_code==403
    listed=client.get('/api/records/cases').json()
    assert [r['id'] for r in listed['items']]==[own['id']]
    assert client.get('/api/admin/users').status_code==403
    conflict=client.post(f"/api/cases/{own['id']}/conflicts",json={}).json()
    assert client.post(f"/api/records/conflicts/{conflict['id']}/transition",json={'status':'waived','note':'越权审批','version':1}).status_code==403


def test_upload_scope_validation_empty_file_and_versions(client):
    a=activate(client,case(client,number='A'));b=activate(client,case(client,number='B'))
    doc=upload(client,a)
    bad=client.post('/api/records/contracts',json={'case_id':b['id'],'title':'跨案引用','number':'X','document_id':doc['id']})
    assert bad.status_code==422
    empty=client.post('/api/documents/upload',data={'case_id':a['id']},files={'file':('empty.txt',b'','text/plain')})
    assert empty.status_code==422
    update=client.post('/api/documents/upload',data={'case_id':a['id'],'document_id':doc['id'],'version':doc['version']},files={'file':('v2.txt',b'version2','text/plain')})
    assert update.status_code==201
    assert len(update.json()['files'])==2
    assert client.get(f"/api/documents/{doc['id']}/download?file_version=0").content!='version2'.encode()
    assert client.get(f"/api/documents/{doc['id']}/download").content==b'version2'


def test_csv_injection_and_csrf(client):
    create(client,'lawyers',{'name':'=HYPERLINK("evil")'})
    response=client.get('/api/export/lawyers')
    assert "'=HYPERLINK" in response.text
    assert client.post('/api/records/lawyers',json={'name':'攻击'},headers={'Origin':'https://evil.example'}).status_code==403


def test_demo_is_explicit_and_not_repeated(client):
    r=client.post('/api/admin/demo')
    assert r.status_code==200,r.text
    result=client.get('/api/dashboard').json()
    assert result['stats']['total']==8
    assert result['stats']['active']==5
    assert client.post('/api/admin/demo').status_code==409


def test_source_manifest_is_available(client):
    result=client.get('/api/admin/migration-plan')
    assert result.status_code==200
    assert len(result.json()['tables'])==60


def test_foreign_currency_is_not_merged_into_case_currency(client):
    item=activate(client,case(client))
    with storage.db() as conn:
        storage.save_record(conn,'receipts',{'title':'历史美元到账','amount':'100.00','currency':'USD','status':'confirmed'},'migration',case_id=item['id'])
    summary=client.get(f"/api/records/cases/{item['id']}").json()['finance']
    assert summary['received']=='0.00'
    assert summary['other_currencies']['USD']['received']=='100.00'
    assert client.get('/api/dashboard').json()['funds']['USD']['received']=='100.00'


def test_new_conflict_supersedes_previous_within_same_second(client):
    item=activate(client,case(client))
    latest=client.post(f"/api/cases/{item['id']}/conflicts",json={'notes':'再次核查'}).json()
    assert latest['status']=='pending'
    r=client.post('/api/records/invoices',json={'case_id':item['id'],'title':'甲方','tax_id':'T','amount':'10'})
    assert r.status_code==409


def test_imported_employee_private_fields_are_admin_only(client):
    person=lawyer(client)
    with storage.db() as conn:
        item=storage.get_record(conn,person['id']);item['legacy_data']={'emp_idcard':'SENSITIVE'}
        storage.save_record(conn,'lawyers',item,'migration',record_id=person['id'])
    client.post('/api/admin/users',json={'username':'staff','name':'普通成员','role':'lawyer','lawyer_id':person['id'],'password':'Test-staff-2026'})
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'staff','password':'Test-staff-2026'})
    assert 'legacy_data' not in client.get(f"/api/records/lawyers/{person['id']}").json()
    assert client.get(f"/api/history/{person['id']}").status_code==403
