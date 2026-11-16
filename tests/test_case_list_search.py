import csv
import io

import pytest

from backend import db as storage
from backend.case_list import case_list_query, case_list_rows
from backend.case_search import init_case_search
from tests.test_workflows import allocate, case, client, create, lawyer


def matches(client, q='', **filters):
    response = client.get('/api/records/cases', params={'q': q, **filters})
    assert response.status_code == 200, response.text
    return response.json()


def exported_ids(client, **filters):
    response = client.get('/api/export/cases', params=filters)
    assert response.status_code == 200, response.text
    return {row['id'] for row in csv.DictReader(io.StringIO(response.text.lstrip('\ufeff')))}


def test_exact_number_is_case_insensitive_and_keeps_same_number_across_offices(client):
    person = lawyer(client)
    exact = case(client, person, number='SH202600117')
    duplicate = create(client, 'cases', {key: exact[key] for key in
        ('number', 'title', 'client', 'lead_lawyer_id', 'category', 'opened_on', 'amount')} | {'office': '北京'})
    case(client, person, number='SH2026001170')
    case(client, person, number='OTHER', client_name='引用 SH202600117 的客户')
    result = matches(client, '  sh202600117  ')
    assert result['total'] == 2
    assert {row['id'] for row in result['items']} == {exact['id'], duplicate['id']}
    assert exported_ids(client, q='sh202600117') == {exact['id'], duplicate['id']}


@pytest.mark.parametrize('q', ['20260011', 'SH2026', '测试委托', '特殊客户', '乙方企业'])
def test_partial_number_name_client_and_opponent(client, q):
    item = case(client, number='SH202600117', client_name='特殊客户')
    result = matches(client, q)
    assert result['total'] == 1
    assert result['items'][0]['id'] == item['id']


@pytest.mark.parametrize('needle', ['%', '_', '\\', "' OR 1=1 --"])
def test_substring_search_treats_sql_metacharacters_literally(client, needle):
    person = lawyer(client)
    item = case(client, person, number='SPECIAL', client_name='客户' + needle + '名称')
    case(client, person, number='OTHER', client_name='普通客户')
    result = matches(client, needle)
    assert [row['id'] for row in result['items']] == [item['id']]


def test_search_index_tracks_updates_deletion_and_reinitialization(client):
    item = case(client, number='CS-BEFORE', client_name='旧客户')
    response = client.patch('/api/records/cases/' + item['id'], json={
        'version': item['version'], 'number': 'HZ-AFTER', 'client': '新客户'})
    assert response.status_code == 200, response.text
    with storage.db() as conn:
        init_case_search(conn)
    assert matches(client, 'CS-BEFORE')['total'] == 0
    assert matches(client, '旧客户')['total'] == 0
    assert matches(client, '新客户', branch='HZ')['total'] == 1
    assert matches(client, 'HZ-AFTER', branch='CS')['total'] == 0
    assert matches(client, 'HZ-AFTER', status='active')['total'] == 0
    with storage.db() as conn:
        conn.execute('UPDATE records SET deleted=1 WHERE id=?', (item['id'],))
    assert matches(client, 'HZ-AFTER')['total'] == 0
    assert exported_ids(client, q='新客户') == set()


@pytest.mark.parametrize('role', ['lawyer', 'finance', 'clerk'])
def test_search_permissions_include_assigned_cases_and_do_not_leak_hidden_exact_match(client, role):
    owner, other = lawyer(client, '本人'), lawyer(client, '他人')
    own = case(client, owner, number='CS-OWN')
    assigned = case(client, other, number='CS-ASSIGNED')
    assert allocate(client, assigned, [{'lawyer_id': owner['id'], 'ratio': '100', 'role': '协办'}]).status_code == 200
    hidden = case(client, other, number='CS-SECRET')
    assert client.patch('/api/records/cases/' + hidden['id'], json={
        'version': hidden['version'], 'confidential': True}).status_code == 200
    visible = case(client, owner, number='CS-PUBLIC', client_name='CS-SECRET 可见客户')
    assert client.post('/api/admin/users', json={'name': '搜索用户', 'username': 'search-user',
        'password': 'Test-password-2026', 'role': role, 'lawyer_id': owner['id'] if role == 'lawyer' else None}).status_code == 200
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'search-user', 'password': 'Test-password-2026'})
    expected = {own['id'], assigned['id'], visible['id']}
    assert {row['id'] for row in matches(client, 'CS-', branch='CS')['items']} == expected
    assert exported_ids(client, q='CS-', branch='CS') == expected
    assert [row['id'] for row in matches(client, 'CS-SECRET')['items']] == [visible['id']]
    assert exported_ids(client, q='CS-SECRET') == {visible['id']}


def test_stable_paging_and_blank_search_match_export(client):
    person = lawyer(client)
    items = [case(client, person, number=f'HZ-PAGE-{i}') for i in range(3)]
    with storage.db() as conn:
        conn.execute("UPDATE records SET updated_at='2026-11-16T00:00:00+00:00' WHERE kind='cases'")
    ids = []
    for page in range(1, 4):
        result = matches(client, '  ', branch='hz', status='intake', page=page, page_size=1)
        assert result['total'] == 3
        ids.append(result['items'][0]['id'])
    assert ids == sorted(item['id'] for item in items)
    assert exported_ids(client, q='  ', branch='hz', status='intake') == set(ids)
    assert matches(client, 'PAGE', page=4, page_size=1)['items'] == []


def test_query_plans_use_number_index_and_fetch_only_paged_records(client):
    item = case(client, number='SH202600117')
    user = {'role': 'admin'}
    with storage.db() as conn:
        where, args = case_list_query(conn, user, 'sh202600117')
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT COUNT(*) FROM case_search c WHERE ' + where, args).fetchall()
        assert any('idx_search_number_nocase (number=?)' in row['detail'] for row in plan)
        where, args = case_list_query(conn, user, '测试委托')
        statements = []
        conn.set_trace_callback(statements.append)
        rows = case_list_rows(conn, where, args, 1)
        conn.set_trace_callback(None)
        assert rows[0]['id'] == item['id']
        plan = conn.execute('EXPLAIN QUERY PLAN ' + statements[-1]).fetchall()
        assert any('SEARCH r USING INDEX sqlite_autoindex_records_1 (id=?)' in row['detail'] for row in plan)
        assert not any('SCAN r' in row['detail'] for row in plan)
        where, args = case_list_query(conn, {'role': 'lawyer', 'lawyer_id': item['lead_lawyer_id']}, 'SH2026')
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT COUNT(*) FROM case_search c WHERE ' + where, args).fetchall()
        assert any('idx_allocations_lawyer (<expr>=?)' in row['detail'] for row in plan)
