import pytest
from fastapi.testclient import TestClient

from backend import db as storage, main
from backend.invoice_states import legacy_invoice_status
from migration.project import Projector, stable_id
from tests.test_workflows import activate, allocate, case, client, create, lawyer, step


def prepare(client):
    person = lawyer(client)
    matter = activate(client, case(client, person))
    assert allocate(client, matter, [{'lawyer_id': person['id'], 'ratio': '100'}]).status_code == 200
    invoice = create(client, 'invoices', {
        'case_id': matter['id'], 'title': '测试发票', 'tax_id': 'TAX-1', 'amount': '1000.01',
        'number': 'INVOICE-1', 'issued_on': '2026-11-19',
    })
    return matter, invoice


def test_collection_and_red_reversal_preserve_snapshots_and_finance(client):
    matter, invoice = prepare(client)
    path = f"/api/records/invoices/{invoice['id']}"
    assert client.post(path + '/transition', json={'status': 'red_reversed', 'version': invoice['version']}).status_code == 409
    for status in ('submitted', 'approved', 'issued', 'collected', 'red_requested', 'red_reversed'):
        previous = invoice
        invoice = step(client, invoice, status, note='')
        assert invoice['history'][-1]['from'] == previous['status']
        assert invoice['history'][-1]['note'] == ''
        assert client.post(path + '/transition', json={'status': status, 'version': previous['version']}).status_code == 409
        expected = '1000.01' if status in ('issued', 'collected', 'red_requested') else '0.00'
        assert client.get(f"/api/records/cases/{matter['id']}").json()['finance']['invoiced'] == expected
        dashboard = client.get('/api/dashboard').json()
        assert dashboard['funds']['CNY']['invoiced'] == expected
        if status == 'red_requested':
            assert invoice['id'] in [r['id'] for r in dashboard['pending']]
        if status in ('issued', 'collected', 'red_requested', 'red_reversed'):
            assert client.patch(path, json={'amount': '1', 'version': invoice['version']}).status_code == 409
            assert allocate(client, invoice, [{'lawyer_id': matter['lead_lawyer_id'], 'ratio': '100'}]).status_code == 409
        if status in ('collected', 'red_requested', 'red_reversed'):
            assert invoice['allocation_snapshot'] == previous['allocation_snapshot']
    assert invoice['collected_at'] and invoice['collected_by'] and invoice['red_reversed_at']
    assert client.get('/api/records/invoices?status=red_reversed').json()['total'] == 1


@pytest.mark.parametrize('status', ['issued', 'collected', 'red_requested'])
def test_effective_invoice_states_prevent_duplicates_and_allow_receipts(client, status):
    matter, invoice = prepare(client)
    sequence = ['submitted', 'approved', 'issued', 'collected', 'red_requested']
    for target in sequence[:sequence.index(status) + 1]:
        invoice = step(client, invoice, target)
    duplicate = create(client, 'invoices', {
        'case_id': matter['id'], 'title': '重复票号', 'tax_id': 'TAX-1', 'amount': '10.00',
        'number': invoice['number'], 'issued_on': '2026-11-19',
    })
    duplicate = step(client, step(client, duplicate, 'submitted'), 'approved')
    assert client.post(f"/api/records/invoices/{duplicate['id']}/transition", json={
        'status': 'issued', 'version': duplicate['version'],
    }).status_code == 409
    receipt = create(client, 'receipts', {
        'case_id': matter['id'], 'title': '到账', 'amount': '10.00', 'payer': '客户',
        'received_on': '2026-11-19', 'reference': 'BANK-1', 'invoice_id': invoice['id'],
    })
    receipt = step(client, receipt, 'confirmed')
    if status != 'red_requested':
        invoice = step(client, invoice, 'red_requested')
    assert client.post(f"/api/records/invoices/{invoice['id']}/transition", json={
        'status': 'red_reversed', 'version': invoice['version'],
    }).status_code == 409
    step(client, receipt, 'void')
    step(client, invoice, 'red_reversed')
    new_receipt = create(client, 'receipts', {
        'case_id': matter['id'], 'title': '冲红后到账', 'amount': '10.00', 'payer': '客户',
        'received_on': '2026-11-19', 'reference': 'BANK-2', 'invoice_id': invoice['id'],
    })
    assert client.post(f"/api/records/receipts/{new_receipt['id']}/transition", json={
        'status': 'confirmed', 'version': new_receipt['version'],
    }).status_code == 409


def test_administration_approval_and_finance_issuance_have_separate_permissions(client):
    matter, invoice = prepare(client)
    for role in ('clerk', 'finance', 'lawyer'):
        assert client.post('/api/admin/users', json={
            'username': role, 'name': role, 'role': role, 'password': 'Test-password-2026',
            'lawyer_id': matter['lead_lawyer_id'] if role == 'lawyer' else None,
        }).status_code == 200
    with TestClient(main.app) as clerk, TestClient(main.app) as finance, TestClient(main.app) as member:
        for session, role in ((clerk, 'clerk'), (finance, 'finance'), (member, 'lawyer')):
            assert session.post('/api/auth/login', json={'username': role, 'password': 'Test-password-2026'}).status_code == 200
        invoice = step(member, invoice, 'submitted')
        path = f"/api/records/invoices/{invoice['id']}/transition"
        for session in (member, finance):
            assert session.post(path, json={'status': 'approved', 'version': invoice['version']}).status_code == 403
        invoice = step(clerk, invoice, 'rejected')
        invoice = step(member, invoice, 'submitted')
        invoice = step(clerk, invoice, 'approved')
        for session in (member, clerk):
            assert session.post(path, json={'status': 'issued', 'version': invoice['version']}).status_code == 403
        invoice = step(finance, invoice, 'issued')
        invoice = step(member, invoice, 'collected')
        invoice = step(member, invoice, 'red_requested')
        for session in (member, clerk):
            assert session.post(path, json={'status': 'red_reversed', 'version': invoice['version']}).status_code == 403
        assert step(finance, invoice, 'red_reversed')['status'] == 'red_reversed'


def test_invoice_builtin_labels_upgrade_but_preserve_custom_labels(client):
    with storage.db() as conn:
        conn.execute("UPDATE managed_metadata SET name='待审核' WHERE category='invoice_status' AND code='submitted'")
        conn.execute("UPDATE managed_metadata SET name='自定义开票阶段' WHERE category='invoice_status' AND code='approved'")
        storage.ensure_managed_references(conn)
        storage.ensure_managed_references(conn)
    schema = client.get('/api/schema').json()
    assert schema['states']['invoices']['submitted'] == '行政审批'
    assert schema['states']['invoices']['approved'] == '自定义开票阶段'
    assert schema['states']['invoices']['collected'] == '发票审领成功'
    assert schema['states']['invoices']['red_requested'] == '发票冲红申请'
    assert schema['states']['invoices']['red_reversed'] == '已冲红'


@pytest.mark.parametrize(('raw', 'state'), [
    ('AZ', 'submitted'), ('AD', 'rejected'), ('AA', 'approved'), ('AN', 'draft'),
    ('AB', 'issued'), ('AC', 'collected'), ('AS', 'submitted'),
    ('legacy:N|D', 'rejected'), ('legacy:A|c', 'collected'), ('A/Z', 'submitted'),
    ('legacy:X|X', 'void'), ('XC', 'void'), ('AX', 'void'),
    ('legacy:?|?', None), ('QZ', None), ('red_reversed', None), ('collected', None),
])
def test_legacy_mapping_matches_original_application_codes(raw, state):
    assert legacy_invoice_status(raw) == state


def test_legacy_list_detail_filters_exports_and_finance_use_same_mapping(client):
    matter, invoice = prepare(client)
    ids = {}
    with storage.db() as conn:
        for index, raw in enumerate(('legacy:A|Z', 'AD', 'legacy:N|C', 'legacy:A|c', 'legacy:X|X', 'legacy:?|?')):
            item = storage.save_record(conn, 'invoices', {
                'title': raw, 'amount': '10.00', 'status': raw, 'number': f'LEGACY-{index}',
                'legacy_data': {'iv_status': raw},
            }, 'migration', case_id=matter['id'], source_key=f'jw.invoice:{index}')
            ids[raw] = item['id']
        stored = conn.execute("SELECT id,data,version FROM records WHERE kind='invoices' ORDER BY id").fetchall()
    expected = {'legacy:A|Z': 'submitted', 'AD': 'rejected', 'legacy:N|C': 'collected',
                'legacy:A|c': 'collected', 'legacy:X|X': 'void', 'legacy:?|?': 'legacy:?|?'}
    rows = client.get('/api/records/invoices').json()['items']
    for raw, state in expected.items():
        detail = client.get('/api/records/invoices/' + ids[raw]).json()
        assert detail['status'] == state
        assert detail['legacy_data'] == {'iv_status': raw}
        assert next(r for r in rows if r['id'] == ids[raw])['status'] == state
        filtered = client.get('/api/records/invoices', params={'status': state}).json()['items']
        assert ids[raw] in [r['id'] for r in filtered]
    export = client.get('/api/export/invoices?status=collected').text
    assert ids['legacy:N|C'] in export and ids['legacy:A|c'] in export
    assert ids['AD'] not in export
    summary = client.get(f"/api/records/cases/{matter['id']}").json()['finance']
    assert summary['invoiced'] == '20.00' and summary['unresolved'] == 1
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['funds']['CNY']['invoiced'] == '20.00'
    assert dashboard['stats']['unresolved_finance'] == 1
    with storage.db() as conn:
        assert conn.execute("SELECT id,data,version FROM records WHERE kind='invoices' ORDER BY id").fetchall() == stored
    # A receipt can refer to a confirmed historical issued/collected invoice.
    receipt = create(client, 'receipts', {'case_id': matter['id'], 'title': '历史发票到账',
        'amount': '10.00', 'payer': '客户', 'received_on': '2026-11-19', 'reference': 'LEGACY-BANK',
        'invoice_id': ids['legacy:N|C']})
    assert step(client, receipt, 'confirmed')['status'] == 'confirmed'
    invoice = client.patch(f"/api/records/invoices/{invoice['id']}", json={
        'number': 'LEGACY-2', 'version': invoice['version'],
    }).json()
    invoice = step(client, step(client, invoice, 'submitted'), 'approved')
    assert client.post(f"/api/records/invoices/{invoice['id']}/transition", json={
        'status': 'issued', 'version': invoice['version'],
    }).status_code == 409


def test_new_states_are_not_overridden_by_legacy_source_fields(client):
    matter, _ = prepare(client)
    with storage.db() as conn:
        item = storage.save_record(conn, 'invoices', {'status': 'red_reversed',
            'legacy_data': {'iv_status': 'A', 'iv_app_status': 'C'}, 'amount': '12.00'},
            'migration', case_id=matter['id'])
    assert client.get('/api/records/invoices/' + item['id']).json()['status'] == 'red_reversed'
    assert client.get(f"/api/records/cases/{matter['id']}").json()['finance']['invoiced'] == '0.00'


def test_new_migration_uses_confirmed_codes_and_keeps_explicit_overrides(client):
    with storage.db() as conn:
        projector = Projector(conn)
        for index, (code, state) in enumerate((('Z', 'submitted'), ('D', 'rejected'), ('c', 'collected'), ('?', 'legacy:A|?'))):
            source = {'iv_index': str(index), 'iv_office_id': 'SH', 'iv_case_id': 'OLD',
                      'iv_status': 'A', 'iv_app_status': code, 'iv_amount': '10.00'}
            projector.invoices(source)
            item = storage.get_record(conn, stable_id('t_invoice', [str(index)]))
            assert item['status'] == state
            assert item['legacy_data'] == source
        assert conn.execute("SELECT COUNT(*) FROM migration_issues WHERE code='unmapped_status'").fetchone()[0] == 1
        projector = Projector(conn, {'statuses': {'t_invoice': {'A|c': 'red_reversed'}}})
        projector.invoices({'iv_index': 'override', 'iv_office_id': 'SH', 'iv_case_id': 'OLD',
                            'iv_status': 'A', 'iv_app_status': 'c', 'iv_amount': '10.00'})
        assert storage.get_record(conn, stable_id('t_invoice', ['override']))['status'] == 'red_reversed'
