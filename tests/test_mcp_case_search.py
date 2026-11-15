import json
from datetime import date
import pytest
import httpx
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend import db as storage
from backend import main, seed
from backend.case_search import CaseQuery, query_cases
from tests.test_workflows import activate, case, create, lawyer


@pytest.fixture
def client(tmp_path, monkeypatch, request):
    monkeypatch.delenv('LAW_ALLOWED_ORIGINS', raising=False)
    monkeypatch.delenv('LAW_MCP_ALLOWED_HOSTS', raising=False)
    for key, value in getattr(request, 'param', {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'office.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.setattr(seed,'DATA',tmp_path)
    with TestClient(main.app) as c:
        c.post('/api/setup',json={'username':'admin','name':'管理员','password':'Test-password-2026'})
        c.post('/api/auth/login',json={'username':'admin','password':'Test-password-2026'})
        yield c


def test_case_query_requires_a_condition():
    try: CaseQuery()
    except Exception as exc: assert '至少提供一个' in str(exc)
    else: assert False


def test_mcp_streamable_http_requires_token_and_exposes_query_tool(client):
    assert client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==401
    token=client.post('/api/mcp/tokens',json={'name':'自动化查询'}).json()['token']
    headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'}
    listed=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
    assert listed.status_code==200 and 'query_cases' in listed.text
    invalid=client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'query_cases','arguments':{'filters':{}}}})
    assert invalid.status_code==200 and '至少提供一个' in invalid.text
    token_id=client.get('/api/mcp/tokens').json()['items'][0]['id']
    assert client.delete('/api/mcp/tokens/'+token_id).status_code==200
    assert client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/list','params':{}}).status_code==401


@pytest.mark.parametrize('path', ['/mcp', '/mcp/'])
@pytest.mark.parametrize('client,origin', [
    ({'LAW_ALLOWED_ORIGINS': 'https://law.example.com:8443'}, 'https://law.example.com:8443'),
    ({'LAW_MCP_ALLOWED_HOSTS': 'law.example.com:8443'}, None),
], indirect=['client'])
def test_streamable_http_sdk_initializes_and_calls_tool_at_public_host(client, path, origin):
    token = client.post('/api/mcp/tokens', json={'name': 'Streamable HTTP 测试'}).json()['token']
    headers = {'Authorization': 'Bearer ' + token}
    if origin:
        headers['Origin'] = origin

    async def connect():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), headers=headers) as http:
            async with streamable_http_client('https://law.example.com:8443' + path, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.capabilities.tools is not None
                    listed = await session.list_tools()
                    assert [tool.name for tool in listed.tools] == ['query_cases']
                    result = await session.call_tool('query_cases', {'filters': {'case_name': '不存在的测试案件'}})
                    assert not result.isError
                    assert json.loads(result.content[0].text)['items'] == []

    client.portal.call(connect)


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '[::1]'])
def test_mcp_allows_local_host_without_port_and_returns_json(client, host):
    token = client.post('/api/mcp/tokens', json={'name': '本机客户端'}).json()['token']
    response = client.post('/mcp', headers={'Host': host, 'Authorization': 'Bearer ' + token,
                                         'Accept': 'application/json, text/event-stream'},
                           json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/json')


def test_mcp_rejects_unconfigured_host_and_origin(client):
    token = client.post('/api/mcp/tokens', json={'name': '白名单测试'}).json()['token']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'}
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    unknown_host = client.post('/mcp', headers={**headers, 'Host': 'untrusted.example',
                                               'X-Forwarded-Host': 'localhost'}, json=payload)
    assert unknown_host.status_code == 421
    unknown_origin = client.post('/mcp', headers={**headers, 'Origin': 'https://untrusted.example',
                                                 'Sec-Fetch-Site': 'same-origin'}, json=payload)
    assert unknown_origin.status_code == 403


def test_token_creation_accepts_public_origin_forwarded_by_reverse_proxy(client):
    headers = {
        'Origin': 'https://law.example.com',
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Host': 'law.example.com',
    }
    response = client.post('/api/mcp/tokens', headers=headers, json={'name': '反向代理客户端'})
    assert response.status_code == 201, response.text


def test_token_creation_accepts_browser_same_origin_when_proxy_rewrites_host(client):
    headers = {
        'Origin': 'http://www.guogaoyi.cn',
        'Sec-Fetch-Site': 'same-origin',
    }
    response = client.post('/api/mcp/tokens', headers=headers, json={'name': '同源浏览器客户端'})
    assert response.status_code == 201, response.text


def test_case_query_and_bundle_relations(client):
    person=lawyer(client,'MCP主办律师')
    item=case(client,person,number='CS-MCP-001',client_name='MCP客户')
    create(client,'parties',{'case_id':item['id'],'name':'MCP客户','role':'委托方','identifier':'C-1'})
    item=activate(client,item)
    for kind,payload in [('invoices',{'title':'MCP发票','tax_id':'T1','type':'增值税普通发票','amount':'10.00','tax_rate':'6'}),
                         ('receipts',{'title':'MCP到账','amount':'10.00','received_on':'2026-11-14','payer':'MCP客户','reference':'R1','method':'银行转账'}),
                         ('seals',{'title':'MCP盖章','seal_type':'律所公章','copies':1,'recipient':'MCP客户','reason':'MCP验证','document_id':'missing'}),
                         ('tasks',{'title':'MCP待办','type':'工作任务','assignee_id':person['id'],'due_on':'2026-11-15','priority':'普通'})]:
        if kind=='seals': continue
        create(client,kind,{'case_id':item['id'],**payload})
    user=client.get('/api/auth/me').json()
    with storage.db() as conn:
        from backend.case_search import init_case_search
        init_case_search(conn)
        result=query_cases(CaseQuery(client_name='MCP客户',branch_name='长沙',opened_from='2026-11-01',opened_to='2026-11-30',page_size=5),user)
    assert result['total']==1
    bundle=result['items'][0]
    assert bundle['case']['number']=='CS-MCP-001'
    assert bundle['client']['name']=='MCP客户'
    assert len(bundle['finance']['invoices'])==1 and len(bundle['finance']['receipts'])==1
    assert len(bundle['tasks'])==1
    with storage.db() as conn:
        assert conn.execute("SELECT 1 FROM case_search WHERE id=?",(item['id'],)).fetchone()
