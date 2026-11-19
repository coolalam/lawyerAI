import pytest

from backend import db as storage
from tests.test_workflows import case, client, lawyer, step, upload


def test_case_waiver_checkbox_is_saved_and_can_be_unchecked(client):
    matter = case(client)
    path = f"/api/records/cases/{matter['id']}"
    assert matter['conflict_waiver_clause'] is False
    for value in (True, False):
        response = client.patch(path, json={'conflict_waiver_clause': value, 'version': matter['version']})
        assert response.status_code == 200, response.text
        matter = client.get(path).json()
        assert matter['conflict_waiver_clause'] is value
    assert client.patch(path, json={'conflict_waiver_clause': 'false', 'version': matter['version']}).status_code == 422


def prepare(client, person=None):
    item = case(client, person)
    report = client.post(f"/api/cases/{item['id']}/conflicts", json={}).json()
    step(client, report, 'cleared', note='')
    document = upload(client, item)
    response = client.patch(f"/api/records/cases/{item['id']}", json={
        'contract_document_id': document['id'], 'version': item['version'],
    })
    assert response.status_code == 200, response.text
    return response.json(), document


def test_intake_contract_nodes_are_sequential_audited_and_accept_empty_notes(client):
    item, document = prepare(client)
    path = f"/api/records/cases/{item['id']}"
    sequence = ['submitted', 'our_seal', 'client_seal', 'contract_archive', 'active']
    for target in sequence:
        previous = item
        if target != 'active':
            response = client.post(path + '/transition', json={'status': 'active', 'version': item['version']})
            assert response.status_code == 409
        response = client.post(path + '/transition', json={'status': target, 'version': item['version']})
        assert response.status_code == 200, response.text
        item = response.json()
        assert item['history'][-1]['from'] == previous['status']
        assert item['history'][-1]['note'] == ''
        assert item['history'][-1]['actor'] == '管理员'
        assert item['history'][-1]['at']
        dashboard = client.get('/api/dashboard').json()
        assert dashboard['stats']['pending'] == (0 if target == 'active' else 1)
        assert dashboard['stats']['intake'] == (0 if target == 'active' else 1)
        if target != 'active':
            assert client.patch(path, json={'title': '审批中修改', 'version': item['version']}).status_code == 409
            assert client.post('/api/records/invoices', json={'case_id': item['id']}).status_code == 409
        # Replaying a previous approval cannot move the workflow twice.
        assert client.post(path + '/transition', json={'status': target, 'version': previous['version']}).status_code == 409
    assert [h['to'] for h in item['history']] == sequence
    assert item['contract_archived_at']
    assert item['contract_file_snapshot']['sha256'] == document['files'][0]['sha256']
    assert client.get(f"/api/documents/{document['id']}/download").content == '真实测试文件内容'.encode()
    for kind in ('contracts', 'seals'):
        assert client.get(f'/api/records/{kind}', params={'case_id': item['id']}).json()['total'] == 0
    with storage.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM record_versions WHERE record_id=?', (item['id'],)).fetchone()[0] == item['version']
        assert conn.execute("SELECT COUNT(*) FROM audit WHERE record_id=? AND action LIKE '%→%'", (item['id'],)).fetchone()[0] == len(sequence)


@pytest.mark.parametrize('status', ['intake', 'draft', 'rejected'])
def test_submission_requires_real_contract_for_new_and_existing_drafts(client, status):
    item = case(client)
    report = client.post(f"/api/cases/{item['id']}/conflicts", json={}).json()
    step(client, report, 'cleared', note='')
    if status != 'intake':
        with storage.db() as conn:
            item = storage.save_record(conn, 'cases', {**item, 'status': status}, 'migration', record_id=item['id'])
    path = f"/api/records/cases/{item['id']}"
    response = client.post(path + '/transition', json={'status': 'submitted', 'version': item['version']})
    assert response.status_code == 422
    assert '合同附件' in response.text
    with storage.db() as conn:
        placeholder = storage.save_record(conn, 'documents', {'title': '历史路径', 'status': 'missing'}, 'migration', case_id=item['id'])
    response = client.patch(path, json={'contract_document_id': placeholder['id'], 'version': item['version']})
    assert response.status_code == 200
    item = response.json()
    assert client.post(path + '/transition', json={'status': 'submitted', 'version': item['version']}).status_code == 422
    document = upload(client, item)
    item = client.patch(path, json={'contract_document_id': document['id'], 'version': item['version']}).json()
    assert step(client, item, 'submitted', note='')['status'] == 'submitted'


def test_contract_attachment_cannot_cross_cases_or_change_during_approval(client):
    item, document = prepare(client)
    other = case(client, number='OTHER')
    response = client.patch(f"/api/records/cases/{other['id']}", json={'contract_document_id': document['id'], 'version': other['version']})
    assert response.status_code == 422
    item = step(client, item, 'submitted', note='')
    for status in ('submitted', 'our_seal', 'client_seal', 'contract_archive', 'active'):
        if status != 'submitted':
            item = step(client, item, status, note='')
        assert client.delete(f"/api/records/documents/{document['id']}?version={document['version']}").status_code == 409
        response = client.post('/api/documents/upload', data={
            'case_id': item['id'], 'document_id': document['id'], 'version': document['version'],
        }, files={'file': ('replacement.txt', b'replacement', 'text/plain')})
        assert response.status_code == 409, response.text
    assert client.patch(f"/api/records/cases/{item['id']}", json={'contract_document_id': '', 'version': item['version']}).status_code == 409


@pytest.mark.parametrize('status', ['submitted', 'our_seal', 'client_seal', 'contract_archive'])
def test_rejection_allows_edit_and_resubmission_with_new_contract(client, status):
    item, _ = prepare(client)
    for target in ('submitted', 'our_seal', 'client_seal', 'contract_archive'):
        item = step(client, item, target, note='')
        if target == status:
            break
    item = step(client, item, 'rejected', note='')
    replacement = upload(client, item, '修改后的合同')
    response = client.patch(f"/api/records/cases/{item['id']}", json={
        'contract_document_id': replacement['id'], 'amount': '500.00', 'version': item['version'],
    })
    assert response.status_code == 200, response.text
    item = step(client, response.json(), 'submitted', note='')
    assert item['contract_file_snapshot']['document_id'] == replacement['id']
    assert item['amount'] == '500.00'


@pytest.mark.parametrize('target', ['our_seal', 'client_seal', 'contract_archive', 'active'])
def test_case_approval_nodes_still_require_reviewer_permission(client, target):
    person = lawyer(client)
    item, _ = prepare(client, person)
    item = step(client, item, 'submitted', note='')
    for stage in ('our_seal', 'client_seal', 'contract_archive'):
        if stage == target:
            break
        item = step(client, item, stage, note='')
    response = client.post('/api/admin/users', json={'username': 'lawyer', 'name': '律师', 'role': 'lawyer', 'lawyer_id': person['id'], 'password': 'Test-lawyer-2026'})
    assert response.status_code == 200
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'lawyer', 'password': 'Test-lawyer-2026'})
    response = client.post(f"/api/records/cases/{item['id']}/transition", json={'status': target, 'version': item['version']})
    assert response.status_code == 403
    assert client.get(f"/api/records/cases/{item['id']}").json()['status'] == item['status']


def test_conflict_approval_still_rejects_changed_parties(client):
    item = case(client)
    report = client.post(f"/api/cases/{item['id']}/conflicts", json={}).json()
    assert client.patch(f"/api/records/cases/{item['id']}", json={'opponent': '更改对方', 'version': item['version']}).status_code == 200
    response = client.post(f"/api/records/conflicts/{report['id']}/transition", json={'status': 'cleared', 'version': report['version']})
    assert response.status_code == 409
    assert '当事人已变更' in response.text
