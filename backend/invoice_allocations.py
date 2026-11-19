"""Invoice allocations: exact amounts, shared by forms, issuance and reporting."""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException

from .db import get_record, now, save_record, unpack
from .schema import allocation_role_label
from .security import check_access, require_role
from .service import money, split_amount


def scope_allocations(conn, case_id, scope_id):
    return [unpack(row) for row in conn.execute("""SELECT * FROM records
        WHERE kind='allocations' AND case_id=? AND deleted=0
        AND json_extract(data,'$.scope_id')=? ORDER BY rowid""", (case_id, scope_id))]


def case_team(conn, case):
    team = {}
    for row in scope_allocations(conn, case['id'], case['id']):
        identifier = row.get('lawyer_id')
        if identifier and identifier not in team:
            team[identifier] = {'lawyer_id': identifier, 'role': allocation_role_label(row.get('role')),
                                'ratio': row.get('ratio') or '0'}
    lead = case.get('lead_lawyer_id')
    if lead:
        team.setdefault(lead, {'lawyer_id': lead, 'ratio': '100' if not team else '0'})
        team[lead]['role'] = '主办律师'
    rows = list(team.values())
    rows.sort(key=lambda row: (row['lawyer_id'] != lead, row['lawyer_id']))
    for row in rows:
        person = get_record(conn, row['lawyer_id'], 'lawyers')
        row['lawyer_name'] = person.get('name', row['lawyer_id']) if person else row['lawyer_id']
    return rows


def check_invoice_allocation_write(conn, user, invoice):
    check_access(conn, user, invoice, write=True)
    if invoice.get('status') not in ('draft', 'rejected', 'approved'):
        raise HTTPException(409, '审批中或已开票的分配不可修改，可查看已保存的分配明细')
    require_role(user, *(['admin', 'finance'] if invoice['status'] == 'approved'
                         else ['admin', 'partner', 'lawyer', 'finance']))


def calculate_plan(conn, case, amount, plan):
    if not isinstance(plan, dict) or plan.get('method', 'ratio') not in ('ratio', 'fixed'):
        raise HTTPException(422, '请选择按比例或固定金额分配')
    method, rows = plan.get('method', 'ratio'), plan.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise HTTPException(422, '请填写 1–50 位律师的分配')
    total = Decimal(money(amount))
    if total <= 0:
        raise HTTPException(422, '请先填写大于零的开票金额')
    team = {r['lawyer_id']: r for r in case_team(conn, case)}
    normalized, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('lawyer_id'), str):
            raise HTTPException(422, '分配律师无效')
        identifier = row['lawyer_id']
        if identifier in seen:
            raise HTTPException(422, '同一律师不能重复分配')
        seen.add(identifier)
        person = get_record(conn, identifier, 'lawyers')
        if not person or person.get('status') != 'active':
            raise HTTPException(422, '分配律师不存在或已离职')
        role = team.get(identifier, {}).get('role', allocation_role_label(row.get('role')))
        entry = {'lawyer_id': identifier, 'lawyer_name': person['name'], 'role': role,
                 'department': person.get('department') or '', 'office': person.get('office') or '', 'method': method}
        key = 'ratio' if method == 'ratio' else 'amount'
        if row.get(key) is None or row.get(key) == '':
            raise HTTPException(422, '请填写每位律师的分配比例或金额（无分配可填 0）')
        entry[key] = money(row[key], 4 if method == 'ratio' else 2)
        if method == 'ratio' and Decimal(entry['ratio']) > 100:
            raise HTTPException(422, '分配比例不能超过 100%')
        normalized.append(entry)
    if method == 'ratio':
        return split_amount(total, normalized)
    if sum(Decimal(r['amount']) for r in normalized) != total:
        raise HTTPException(422, '固定金额分配合计必须等于开票金额')
    for row in normalized:
        row['ratio'] = format((Decimal(row['amount']) / total * 100).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP), '.4f')
    return normalized


def replace_plan(conn, invoice, rows, actor):
    conn.execute("""UPDATE records SET deleted=1,updated_at=? WHERE kind='allocations'
        AND case_id=? AND json_extract(data,'$.scope_id')=? AND deleted=0""",
        (now(), invoice['case_id'], invoice['id']))
    for row in rows:
        save_record(conn, 'allocations', {**row, 'scope_id': invoice['id'], 'status': 'active'}, actor, case_id=invoice['case_id'])


def invoice_snapshot(conn, invoice):
    rows = scope_allocations(conn, invoice['case_id'], invoice['id'])
    method = invoice.get('allocation_method', 'ratio')
    if not rows:
        rows = scope_allocations(conn, invoice['case_id'], invoice['case_id'])
        method = 'ratio'
    if not rows:
        raise HTTPException(422, '请先填写发票分配表或案件律师分配方案')
    return calculate_plan(conn, get_record(conn, invoice['case_id'], 'cases'), invoice['amount'], {'method': method, 'rows': rows})


def allocation_form(conn, user, case, invoice=None):
    check_access(conn, user, case)
    read_only = False
    if invoice:
        check_access(conn, user, invoice)
        if invoice.get('case_id') != case['id']:
            raise HTTPException(422, '发票不属于此案件')
        try:
            check_invoice_allocation_write(conn, user, invoice)
        except HTTPException as exc:
            if exc.status_code not in (403, 409):
                raise
            read_only = True
    rows = (invoice.get('allocation_snapshot') if invoice else None) or (
        scope_allocations(conn, case['id'], invoice['id']) if invoice else [])
    source = 'invoice' if rows else 'case'
    frozen = invoice and invoice.get('status') in ('issued', 'collected', 'red_requested', 'red_reversed', 'void')
    if not rows and frozen:
        source = 'missing'
    rows = [dict(r) for r in rows] if rows else ([] if frozen else case_team(conn, case))
    for row in rows:
        person = get_record(conn, row.get('lawyer_id'), 'lawyers')
        row['lawyer_name'] = row.get('lawyer_name') or (person or {}).get('name') or row.get('lawyer_id') or '未匹配律师'
    return {'method': (invoice or {}).get('allocation_method', 'ratio'), 'rows': rows,
            'read_only': read_only, 'source': source, 'version': (invoice or {}).get('version'),
            'currency': (invoice or {}).get('currency') or case.get('currency', 'CNY')}
