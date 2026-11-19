from backend import db as storage
from migration.project import Projector, stable_id
from tests.test_workflows import client, case, lawyer, activate, create, step


def issued_invoice(c):
    person = lawyer(c)
    matter = activate(c, case(c, person))
    invoice = create(c, 'invoices', {'case_id': matter['id'], 'title': '自动带入付款客户', 'tax_id': 'TAX',
        'amount': '1000.01', 'number': 'RECEIPT-INVOICE', 'issued_on': '2026-11-19',
        'allocation': {'method': 'fixed', 'rows': [{'lawyer_id': person['id'], 'amount': '1000.01'}]}})
    for target in ('submitted', 'approved', 'issued'):
        invoice = step(c, invoice, target)
    return matter, invoice


def test_receipt_inherits_invoice_case_and_payer_with_third_party_override(client):
    matter, invoice = issued_invoice(client)
    choices = client.get('/api/finance/receipt-invoices?q=RECEIPT-INVOICE').json()
    assert choices['total'] == 1
    assert choices['items'][0]['case_number'] == matter['number']
    data = {'invoice_id': invoice['id'], 'amount': '300.01', 'received_on': '2026-11-19', 'reference': 'BANK-A'}
    receipt = create(client, 'receipts', data)
    assert receipt['case_id'] == matter['id']
    assert receipt['payer'] == invoice['title']
    assert receipt['title'] == invoice['number'] + '到账'
    receipt = step(client, receipt, 'confirmed')
    assert receipt['allocation_snapshot'][0]['amount'] == '300.01'
    assert client.get(f"/api/records/cases/{matter['id']}").json()['finance']['received'] == '300.01'
    rows = client.get('/api/records/receipts').json()['items']
    assert rows[0]['invoice_number'] == invoice['number']
    assert client.get(f"/api/records/receipts/{receipt['id']}").json()['invoice_title'] == invoice['title']
    third_party = create(client, 'receipts', {**data, 'reference': 'BANK-B', 'payer': '第三方代付公司'})
    assert third_party['payer'] == '第三方代付公司'
    foreign = case(client, number='FOREIGN')
    assert client.post('/api/records/receipts', json={**data, 'case_id': foreign['id']}).status_code == 422
    assert client.patch(f"/api/records/receipts/{third_party['id']}", json={
        'case_id': foreign['id'], 'invoice_id': invoice['id'], 'version': third_party['version']}).status_code == 422


def test_legacy_receipts_are_received_in_reads_filters_totals_and_reversal_guards(client):
    matter, invoice = issued_invoice(client)
    with storage.db() as conn:
        ids = {}
        for code in ('A', 'S', 'Y', 'B', 'X', '?'):
            row = storage.save_record(conn, 'receipts', {'status': 'legacy:'+code, 'amount': '10.00', 'currency': 'CNY',
                'invoice_id': invoice['id'], 'payer': '历史付款方', 'reference': 'BANK-'+code,
                'legacy_data': {'pay_status': code}}, 'migration', case_id=matter['id'])
            ids[code] = row['id']
        before = conn.execute("SELECT id,data,version FROM records WHERE kind='receipts' ORDER BY id").fetchall()
    for code in ('A', 'S', 'Y', 'B'):
        row = client.get('/api/records/receipts/'+ids[code]).json()
        assert row['status'] == 'confirmed' and row['legacy_data']['pay_status'] == code
    assert client.get('/api/records/receipts/'+ids['X']).json()['status'] == 'void'
    assert client.get('/api/records/receipts/'+ids['?']).json()['legacy_status_label'] == '到账状态待核对'
    assert client.get('/api/records/receipts?status=confirmed').json()['total'] == 4
    assert client.get('/api/schema').json()['states']['receipts']['confirmed'] == '已到账'
    assert client.get('/api/dashboard').json()['funds']['CNY']['received'] == '40.00'
    assert client.get(f"/api/records/cases/{matter['id']}").json()['finance']['received'] == '40.00'
    assert ids['A'] in client.get('/api/export/receipts?status=confirmed').text
    with storage.db() as conn:
        assert conn.execute("SELECT id,data,version FROM records WHERE kind='receipts' ORDER BY id").fetchall() == before
    duplicate = create(client, 'receipts', {'invoice_id': invoice['id'], 'amount': '10',
        'reference': 'BANK-A', 'received_on': '2026-11-19'})
    assert client.post(f"/api/records/receipts/{duplicate['id']}/transition", json={
        'status': 'confirmed', 'version': duplicate['version']}).status_code == 409
    invoice = step(client, invoice, 'red_requested')
    assert client.post(f"/api/records/invoices/{invoice['id']}/transition", json={
        'status': 'red_reversed', 'version': invoice['version']}).status_code == 409


def test_receipt_defaults_do_not_expose_inaccessible_invoice(client):
    matter, invoice = issued_invoice(client)
    person = lawyer(client, '其他律师')
    client.post('/api/admin/users', json={'username': 'foreign', 'name': '其他律师', 'role': 'lawyer',
        'lawyer_id': person['id'], 'password': 'Test-lawyer-2026'})
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'foreign', 'password': 'Test-lawyer-2026'})
    assert client.get('/api/finance/receipt-invoices').json()['total'] == 0
    assert client.post('/api/records/receipts', json={'invoice_id': invoice['id']}).status_code == 403


def test_payment_migration_maps_only_confirmed_codes(client):
    with storage.db() as conn:
        projector = Projector(conn)
        for code in ('A', 'S', 'Y', 'B', 'X', '?'):
            projector.receipts({'pay_id': code, 'pay_office_id': 'SH', 'pay_case_id': 'OLD',
                               'pay_amount': '20', 'pay_status': code})
            row = storage.get_record(conn, stable_id('t_payment', [code]))
            assert row['status'] == ('void' if code == 'X' else 'legacy:?' if code == '?' else 'confirmed')
            assert row['legacy_data']['pay_status'] == code
