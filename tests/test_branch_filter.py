import csv
import io
from tests.test_workflows import client, case, lawyer


def test_branch_filters_pagination_keywords_status_and_export(client):
    person=lawyer(client)
    for number in ('CS2026001','CS2026002','HZ2026001','XCS2026001','SH2026001'):
        case(client,person,number=number,client_name='分所客户')
    response=client.get('/api/records/cases?branch=cs&page_size=1&page=2&q=分所客户&status=intake')
    assert response.status_code==200
    assert response.json()['total']==2
    assert len(response.json()['items'])==1
    assert response.json()['items'][0]['number'].startswith('CS')
    assert client.get('/api/records/cases?branch=HZ').json()['total']==1
    assert client.get('/api/records/cases?branch=CS&status=active').json()['total']==0
    assert client.get('/api/records/cases?branch=CS&q=不存在').json()['total']==0
    assert client.get('/api/records/cases?branch=CS_').status_code==422
    exported=client.get('/api/export/cases?branch=CS&q=分所客户&status=intake')
    rows=list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert len(rows)==2 and all(row['number'].startswith('CS') for row in rows)


def test_branch_filter_preserves_case_permissions(client):
    owner=lawyer(client,'本人');other=lawyer(client,'其他律师')
    case(client,owner,number='CS-OWN')
    case(client,other,number='CS-OTHER')
    case(client,owner,number='HZ-OWN')
    client.post('/api/admin/users',json={'name':'本人','username':'branch-lawyer','password':'Test-password-2026','role':'lawyer','lawyer_id':owner['id']})
    client.post('/api/auth/logout')
    client.post('/api/auth/login',json={'username':'branch-lawyer','password':'Test-password-2026'})
    result=client.get('/api/records/cases?branch=CS').json()
    assert result['total']==1 and result['items'][0]['number']=='CS-OWN'
    response=client.get('/api/export/cases?branch=CS')
    assert 'CS-OWN' in response.text and 'CS-OTHER' not in response.text and 'HZ-OWN' not in response.text
