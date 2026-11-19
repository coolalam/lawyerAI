import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fastapi import HTTPException
from .db import encode, get_record, unpack
from .schema import SCHEMAS, EFFECTIVE_INVOICE_STATES, allocation_role_label
from .invoice_states import normalize_invoice
from .receipt_states import normalize_receipt


def money(value, places=2, allow_negative=False):
    try:
        amount = Decimal(str(value or '0'))
        if not amount.is_finite() or abs(amount) >= Decimal('10000000000000000'):
            raise ValueError()
        if not allow_negative and amount < 0:
            raise ValueError()
        quantum = Decimal(1).scaleb(-places)
        if amount.quantize(quantum) != amount:
            raise ValueError()
        return format(amount, f'.{places}f')
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f'金额 / 比例必须为非负数字，最多 {places} 位小数')


def validate(kind, data, old=None):
    fields = SCHEMAS[kind]['fields']
    allowed = {f['key'] for f in fields}
    reference_fields = {'department_id'} if kind in ('cases','lawyers') else set()
    if kind == 'lawyers': reference_fields.add('branch_code')
    for key in reference_fields & data.keys():
        if not isinstance(data[key],str) or len(data[key])>200:
            raise HTTPException(422, '部门或分支机构编码无效')
    allowed |= reference_fields
    unknown = set(data) - allowed - {'case_id', 'version'}
    if unknown:
        raise HTTPException(422, f'不允许修改字段：{", ".join(sorted(unknown))}')
    result = dict(old or {})
    for field in fields:
        key = field['key']
        value = data.get(key, result.get(key, field['default']))
        if field['required'] and (value is None or str(value).strip() == ''):
            raise HTTPException(422, f'请填写{field["label"]}')
        if value is None or value == '':
            result[key] = ''
            continue
        if field['type'] in ('money', 'ratio'):
            value = money(value, 4 if field['type'] == 'ratio' else 2)
            if field['type'] == 'ratio' and Decimal(value) > 100:
                raise HTTPException(422, f'{field["label"]}不能超过 100%')
        elif field['type'] == 'integer':
            if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 10000:
                raise HTTPException(422, f'{field["label"]}必须为 1–10000 的整数')
            value = int(value)
        elif field['type'] == 'boolean':
            if not isinstance(value, bool):
                raise HTTPException(422, f'{field["label"]}必须为布尔值')
        elif field['type'] == 'date':
            try:
                value = date.fromisoformat(str(value)).isoformat()
            except ValueError:
                raise HTTPException(422, f'{field["label"]}日期格式错误')
        else:
            if not isinstance(value, str):
                raise HTTPException(422, f'{field["label"]}必须是文本')
            value = value.strip()
            if len(value) > (50000 if field['type'] == 'textarea' else 2000):
                raise HTTPException(422, f'{field["label"]}过长')
            if field['options'] and value not in field['options'] and value != (old or {}).get(key):
                raise HTTPException(422, f'{field["label"]}选项无效')
        result[key] = value
    if kind in ('invoices', 'receipts', 'payment_plans') and Decimal(result['amount']) <= 0:
        raise HTTPException(422, '金额必须大于零')
    if kind == 'cases' and result.get('start_on') and result.get('end_on') and result['start_on'] > result['end_on']:
        raise HTTPException(422, '委托结束日期不能早于开始日期')
    if kind == 'invoices':
        gross, rate = Decimal(result['amount']), Decimal(result['tax_rate']) / 100
        result['tax_amount'] = str((gross - gross / (1 + rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    if kind == 'receipts':
        split = sum(Decimal(result.get(k) or '0') for k in ('lawyer_fee', 'case_fee'))
        if split and split != Decimal(result['amount']):
            raise HTTPException(422, '律师费与代收办案费合计必须等于到账金额')
    return result


def validate_refs(conn, kind, record):
    for field in SCHEMAS[kind]['fields']:
        ref_kind = {'lawyer': 'lawyers', 'document': 'documents', 'invoice': 'invoices', 'plan': 'payment_plans'}.get(field['type'])
        value = record.get(field['key'])
        if not ref_kind or not value:
            continue
        ref = get_record(conn, value, ref_kind)
        case_id = record.get('id') if kind == 'cases' else record.get('case_id')
        if not ref or (ref_kind != 'lawyers' and (not case_id or ref['case_id'] != case_id)):
            raise HTTPException(422, f'{field["label"]}不存在或不属于当前案件')
        if ref_kind == 'lawyers' and ref.get('status') == 'inactive':
            raise HTTPException(422, '不能指派已离职律师')


def children(conn, case_id, kind=None):
    query = 'SELECT * FROM records WHERE case_id=? AND deleted=0'
    args = [case_id]
    if kind:
        query += ' AND kind=?'
        args.append(kind)
    return [unpack(r) for r in conn.execute(query + ' ORDER BY created_at DESC,rowid DESC', args)]


def finance_summary(conn, case):
    sums = {'invoiced': Decimal(0), 'received': Decimal(0), 'planned': Decimal(0)}
    other = {}
    unresolved = 0
    for row in conn.execute("SELECT kind,data FROM records WHERE case_id=? AND deleted=0 AND kind IN ('invoices','receipts','payment_plans')", (case['id'],)):
        import json
        data = json.loads(row['data'])
        if row['kind'] == 'invoices':
            normalize_invoice(data)
        elif row['kind'] == 'receipts':
            normalize_receipt(data)
        if data.get('amount') is None or str(data.get('status', '')).startswith('legacy:'):
            unresolved += 1
            continue
        key = {'invoices': 'invoiced', 'receipts': 'received', 'payment_plans': 'planned'}[row['kind']]
        if (row['kind'] == 'invoices' and data.get('status') not in EFFECTIVE_INVOICE_STATES) or (row['kind'] == 'receipts' and data.get('status') != 'confirmed'):
            continue
        unit = data.get('currency') or case.get('currency', 'CNY')
        if unit != case.get('currency', 'CNY'):
            other.setdefault(unit, {'invoiced': Decimal(0), 'received': Decimal(0), 'planned': Decimal(0)})[key] += Decimal(data.get('amount') or '0')
        else:
            sums[key] += Decimal(data.get('amount') or '0')
    sums['contract'] = Decimal(case.get('amount') or '0')
    sums['outstanding'] = max(Decimal(0), sums['contract'] - sums['received'])
    sums['uninvoiced'] = max(Decimal(0), sums['contract'] - sums['invoiced'])
    sums['overpaid'] = max(Decimal(0), sums['received'] - sums['contract'])
    result = {**{k: format(v, '.2f') for k, v in sums.items()}, 'currency': case.get('currency', 'CNY'),
              'unresolved': unresolved, 'other_currencies': {unit: {k: format(v, '.2f') for k, v in values.items()} for unit, values in other.items()}}
    if case.get('amount') is None:
        result.update(contract=None, outstanding=None, uninvoiced=None, overpaid=None)
    return result


def allocations_for(conn, case_id, scope_id):
    return [r for r in children(conn, case_id, 'allocations') if r.get('scope_id') == scope_id]


def split_amount(amount, rows):
    """Deterministic largest-remainder distribution, integer cents, exact total."""
    try:
        cents = int(Decimal(str(amount or '0')) * 100)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, '分配基数金额无效，请先补充有效的合同服务费')
    weights = [Decimal(r['ratio']) for r in rows]
    if sum(weights) != Decimal(100):
        raise HTTPException(422, '律师分配比例合计必须等于 100%')
    exact = [Decimal(cents) * w / 100 for w in weights]
    allocated = [int(x) for x in exact]
    remaining = cents - sum(allocated)
    order = sorted(range(len(rows)), key=lambda i: (-(exact[i] - allocated[i]), rows[i]['lawyer_id']))
    for i in order[:remaining]:
        allocated[i] += 1
    return [{**r, 'role': allocation_role_label(r.get('role')), 'amount': format(Decimal(allocated[i]) / 100, '.2f')} for i, r in enumerate(rows)]


def normalize_name(name):
    import unicodedata
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', name).casefold())


def conflict_identity(conn, case):
    names = [case.get('client', ''), case.get('opponent', '')]
    names += [p.get('name', '') + '|' + p.get('aliases', '') + '|' + p.get('identifier', '') + '|' + p.get('role', '') for p in children(conn, case['id'], 'parties')]
    return hashlib.sha256(encode(sorted(names)).encode()).hexdigest()


def matching_conflicts(conn, case, additional=''):
    names = [case.get('client', ''), case.get('opponent', '')]
    for p in children(conn, case['id'], 'parties'):
        names += [p.get('name', ''), p.get('aliases', ''), p.get('identifier', '')]
    names.append(additional)
    terms = sorted({normalize_name(n) for name in names for n in re.split(r'[;；\n|]', name) if len(normalize_name(n)) >= 2})
    matches = []
    # All historical parties are included, including original relation types.
    for row in conn.execute("SELECT * FROM records WHERE kind IN ('cases','parties') AND deleted=0"):
        record = unpack(row)
        other_case = record['id'] if record['kind'] == 'cases' else record.get('case_id')
        if other_case == case['id']:
            continue
        fields = ('client', 'opponent') if record['kind'] == 'cases' else ('name', 'aliases', 'identifier')
        for field in fields:
            value = normalize_name(record.get(field, ''))
            if value and any(term in value or value in term for term in terms):
                matches.append({'case_id': other_case, 'record_id': record['id'], 'name': record.get(field), 'relation': record.get('role') or ('委托方' if field == 'client' else '对方当事人')})
                break
        if len(matches) >= 501:
            break
    return terms, matches[:500], len(matches) > 500
