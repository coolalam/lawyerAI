from decimal import Decimal
import pytest
from backend import db as storage
from tests.test_workflows import client, case, lawyer, activate, allocate, create, step


def prepare(c):
    a, b = lawyer(c, '主办甲'), lawyer(c, '代理乙')
    matter = activate(c, case(c, a))
    assert allocate(c, matter, [{'lawyer_id': a['id'], 'ratio': '60', 'role': '主办律师'},
                               {'lawyer_id': b['id'], 'ratio': '40', 'role': '协办律师'}]).status_code == 200
    return matter, a, b


def invoice_data(matter, a, b, method='ratio'):
    rows = [{'lawyer_id': a['id'], 'ratio': '50', 'amount': '750.00'},
            {'lawyer_id': b['id'], 'ratio': '50', 'amount': '250.01'}]
    return {'case_id': matter['id'], 'title': '测试开票客户', 'tax_id': 'TAX', 'amount': '1000.01',
            'number': 'INV-ALLOCATION', 'issued_on': '2026-11-19', 'allocation': {'method': method, 'rows': rows}}


def issue(c, invoice):
    for status in ('submitted', 'approved', 'issued'):
        invoice = step(c, invoice, status)
    return invoice


def report(c, **params):
    response = c.get('/api/finance/revenue', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_invoice_form_inherits_team_and_fixed_plan_is_atomic(client):
    matter, a, b = prepare(client)
    defaults = client.get(f"/api/cases/{matter['id']}/invoice-allocations").json()
    assert [r['lawyer_id'] for r in defaults['rows']] == [a['id'], b['id']]
    assert defaults['rows'][0]['role'] == '主办律师'
    data = invoice_data(matter, a, b, 'fixed')
    data['allocation']['rows'][0]['amount'] = '749.99'
    assert client.post('/api/records/invoices', json=data).status_code == 422
    assert client.get('/api/records/invoices').json()['total'] == 0
    data['allocation']['rows'][0]['amount'] = '750.00'
    invoice = create(client, 'invoices', data)
    url = f"/api/cases/{matter['id']}/invoice-allocations?invoice_id={invoice['id']}"
    saved = client.get(url).json()
    assert saved['method'] == 'fixed'
    assert [r['amount'] for r in saved['rows']] == ['750.00', '250.01']
    # Changing a fixed invoice total requires a matching allocation; failure keeps both intact.
    response = client.patch('/api/records/invoices/'+invoice['id'], json={'amount': '1200', 'version': invoice['version']})
    assert response.status_code == 422
    assert client.get('/api/records/invoices/'+invoice['id']).json()['amount'] == '1000.01'
    assert client.get(url).json()['rows'] == saved['rows']
    issued = issue(client, invoice)
    assert [r['amount'] for r in issued['allocation_snapshot']] == ['750.00', '250.01']
    assert client.get(url).json()['read_only'] is True
    assert allocate(client, issued, data['allocation']['rows']).status_code == 409


def test_ratio_rounding_amount_change_and_stale_save(client):
    matter, a, b = prepare(client)
    invoice = create(client, 'invoices', invoice_data(matter, a, b))
    updated = client.patch('/api/records/invoices/'+invoice['id'], json={'amount': '0.01', 'version': invoice['version']}).json()
    url = f"/api/cases/{matter['id']}/invoice-allocations?invoice_id={invoice['id']}"
    saved = client.get(url).json()
    assert sorted(r['amount'] for r in saved['rows']) == ['0.00', '0.01']
    winner = next(r for r in saved['rows'] if r['amount'] == '0.01')
    assert winner['lawyer_id'] == min(a['id'], b['id'])
    response = client.put(f"/api/cases/{matter['id']}/allocations", json={'scope_id': invoice['id'],
        'version': invoice['version'], 'method': 'fixed', 'rows': [{'lawyer_id': a['id'], 'amount': '0.01'}]})
    assert response.status_code == 409
    assert client.get(url).json()['rows'] == saved['rows']
    assert updated['version'] == saved['version']


def test_revenue_uses_frozen_invoice_amounts_filters_and_dimensions(client):
    matter, a, b = prepare(client)
    invoice = issue(client, create(client, 'invoices', invoice_data(matter, a, b, 'fixed')))
    assert allocate(client, matter, [{'lawyer_id': a['id'], 'ratio': '100'}]).status_code == 200
    result = report(client, date_from='2026-11-19', date_to='2026-11-19', page_size=1)
    assert result['summary'] == {'amount': '1000.01', 'invoice_count': 1, 'lawyer_count': 2, 'unresolved_count': 0}
    assert result['trend'] == [{'label': '2026-11', 'amount': '1000.01'}]
    assert result['total'] == 2 and len(result['items']) == 1
    assert report(client, lawyer_id=b['id'])['summary']['amount'] == '250.01'
    assert report(client, currency='USD')['total'] == 0
    assert report(client, date_to='2026-11-18')['total'] == 0
    for dimension in ('lawyer', 'case', 'category', 'department', 'office'):
        result = report(client, dimension=dimension)
        assert sum(Decimal(r['amount']) for r in result['breakdown']) == Decimal('1000.01')
    assert client.get('/api/finance/revenue?dimension=invalid').status_code == 422
    assert client.get('/api/finance/revenue?date_from=2026-12-01&date_to=2026-01-01').status_code == 422
    invoice = step(client, invoice, 'red_requested')
    assert report(client)['summary']['amount'] == '1000.01'
    step(client, invoice, 'red_reversed')
    assert report(client)['summary']['amount'] == '0.00'


def test_legacy_invoice_scoped_amounts_and_missing_data(client):
    matter, a, b = prepare(client)
    with storage.db() as conn:
        for index in range(61):
            invoice = storage.save_record(conn, 'invoices', {'status': 'legacy:A|C', 'amount': '10.00', 'currency': 'CNY',
                'issued_on': '2026-10-01', 'title': '历史发票'}, 'migration', case_id=matter['id'])
            storage.save_record(conn, 'allocations', {'scope_id': invoice['id'], 'lawyer_id': a['id'],
                'amount': '10.00', 'ratio': '100'}, 'migration', case_id=matter['id'])
        for extra in ({}, {'allocation_snapshot': [{'lawyer_id': a['id'], 'amount': '9'}]},
                      {'issued_on': '', 'allocation_snapshot': [{'lawyer_id': a['id'], 'amount': '10'}]}):
            storage.save_record(conn, 'invoices', {'status': 'collected', 'amount': '10.00', 'currency': 'CNY',
                'issued_on': '2026-10-01', **extra}, 'migration', case_id=matter['id'])
    result = report(client, page=2, date_from='2026-01-01')
    assert result['summary']['amount'] == '610.00'
    assert result['summary']['unresolved_count'] == 3
    assert result['total'] == 61 and len(result['items']) == 11


def test_revenue_and_allocations_follow_case_visibility(client):
    matter, a, b = prepare(client)
    invoice = issue(client, create(client, 'invoices', invoice_data(matter, a, b)))
    foreign = lawyer(client, '无关律师')
    assert client.post('/api/admin/users', json={'username': 'foreign', 'name': '无关', 'password': 'Test-lawyer-2026',
        'role': 'lawyer', 'lawyer_id': foreign['id']}).status_code == 200
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'foreign', 'password': 'Test-lawyer-2026'})
    assert report(client)['total'] == 0
    assert client.get(f"/api/cases/{matter['id']}/invoice-allocations?invoice_id={invoice['id']}").status_code == 403
    assert client.get('/api/finance/revenue', params={'case_id': matter['id']}).status_code == 403
