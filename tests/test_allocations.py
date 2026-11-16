import json

from backend import db as storage
from tests.test_workflows import activate, case, client, lawyer


def test_case_allocation_maps_legacy_role_codes_and_accepts_missing_fee(client):
    person = lawyer(client, '承办律师')
    item = activate(client, case(client, person, number='ALLOC-LEGACY'))
    with storage.db() as conn:
        row = conn.execute('SELECT data FROM records WHERE id=?', (item['id'],)).fetchone()
        data = json.loads(row['data'])
        data['amount'] = None
        conn.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), item['id']))

    response = client.put('/api/cases/' + item['id'] + '/allocations', json={
        'scope_id': item['id'], 'version': item['version'],
        'rows': [{'lawyer_id': person['id'], 'ratio': '100', 'role': '0'}],
    })
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['role'] == '主办律师'
    assert response.json()['items'][0]['amount'] == '0.00'
    listed = client.get('/api/records/allocations', params={'case_id': item['id']}).json()['items']
    assert listed[0]['role'] == '主办律师'


def test_case_allocation_rejects_unknown_role_with_validation_error(client):
    person = lawyer(client, '角色校验律师')
    item = activate(client, case(client, person, number='ALLOC-ROLE'))
    response = client.put('/api/cases/' + item['id'] + '/allocations', json={
        'scope_id': item['id'], 'version': item['version'],
        'rows': [{'lawyer_id': person['id'], 'ratio': '100', 'role': '未知角色'}],
    })
    assert response.status_code == 422
    assert '承办角色' in response.text
