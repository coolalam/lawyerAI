"""Revenue reports use issued invoice allocations, never current case shares."""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from .invoice_states import invoice_status_sql
from .schema import EFFECTIVE_INVOICE_STATES
from .service import money


def revenue_report(conn, where, args, *, lawyer_id='', date_from=None, date_to=None,
                   date_basis='issued_on', currency='CNY', dimension='lawyer', page=1, page_size=50):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    status = invoice_status_sql("json_extract(r.data,'$.status')")
    conditions = where + f" AND {status} IN ({','.join('?' for _ in EFFECTIVE_INVOICE_STATES)})"
    params = [*args, *EFFECTIVE_INVOICE_STATES, currency]
    conditions += " AND COALESCE(json_extract(r.data,'$.currency'),'CNY')=?"
    date_expr = "substr(r.created_at,1,10)" if date_basis == 'created_at' else "json_extract(r.data,'$.issued_on')"
    # Retain missing dates to report unresolved historical data to the user.
    for boundary, operator in ((date_from, '>='), (date_to, '<=')):
        if boundary:
            conditions += f" AND ({date_expr} {operator} ? OR COALESCE({date_expr},'')='')"
            params.append(boundary.isoformat())
    query = f"""SELECT r.id,r.case_id,{date_expr} AS report_date,{status} AS status,
        json_extract(r.data,'$.amount') AS amount,json_extract(r.data,'$.number') AS number,
        json_extract(r.data,'$.title') AS title,json_extract(r.data,'$.allocation_snapshot') AS snapshot
        FROM records r WHERE {conditions}"""
    invoices = conn.execute(query, params).fetchall()
    allocations = defaultdict(list)
    for row in conn.execute(f"""WITH invoices AS ({query})
        SELECT a.data,json_extract(a.data,'$.scope_id') AS invoice_id FROM invoices i
        JOIN records a ON a.case_id=i.case_id AND json_extract(a.data,'$.scope_id')=i.id
        WHERE a.kind='allocations' AND a.deleted=0""", params):
        allocations[row['invoice_id']].append(json.loads(row['data']))
    people = {r['id']: dict(r) for r in conn.execute("""SELECT id,
        json_extract(data,'$.name') AS name,json_extract(data,'$.department') AS department,
        json_extract(data,'$.office') AS office FROM records WHERE kind='lawyers' AND deleted=0""")}
    # Only compact case fields, restricted to the same visible invoice set.
    cases = {r['id']: dict(r) for r in conn.execute(f"""WITH invoices AS ({query})
        SELECT c.id,c.number,c.title,c.category,c.office FROM case_search c
        WHERE c.id IN (SELECT case_id FROM invoices)""", params)}
    details, unresolved, included = [], 0, set()
    for invoice in invoices:
        rows = json.loads(invoice['snapshot']) if invoice['snapshot'] else allocations[invoice['id']]
        try:
            report_date = date.fromisoformat(invoice['report_date'])
            if not isinstance(rows, list) or not rows:
                raise ValueError()
            amounts, seen = [], set()
            for row in rows:
                identifier = row.get('lawyer_id')
                if not identifier or identifier in seen or row.get('amount') in (None, ''):
                    raise ValueError()
                seen.add(identifier)
                amounts.append(Decimal(money(row['amount'])))
            if invoice['amount'] in (None, '') or sum(amounts) != Decimal(money(invoice['amount'])):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, HTTPException):
            unresolved += 1
            continue
        matter = cases.get(invoice['case_id'], {})
        for row, amount in zip(rows, amounts):
            if lawyer_id and row['lawyer_id'] != lawyer_id:
                continue
            person = people.get(row['lawyer_id'], {})
            details.append({'invoice_id': invoice['id'], 'invoice_number': invoice['number'] or invoice['title'],
                'case_id': invoice['case_id'], 'case_number': matter.get('number') or invoice['case_id'],
                'case_title': matter.get('title') or '', 'category': matter.get('category') or '未分类',
                'lawyer_id': row['lawyer_id'], 'lawyer_name': row.get('lawyer_name') or person.get('name') or '未匹配律师',
                'department': row.get('department', person.get('department')) or '未分配部门',
                'office': row.get('office', person.get('office')) or matter.get('office') or '未分配机构',
                'date': report_date.isoformat(), 'status': invoice['status'], 'amount': format(amount, '.2f')})
            included.add(invoice['id'])
    trend, groups, labels = defaultdict(Decimal), defaultdict(Decimal), {}
    for row in details:
        amount = Decimal(row['amount'])
        trend[row['date'][:7]] += amount
        key = row[{'lawyer': 'lawyer_id', 'case': 'case_id'}.get(dimension, dimension)]
        labels[key] = row['lawyer_name'] if dimension == 'lawyer' else row['case_number'] if dimension == 'case' else key
        groups[key] += amount
    details.sort(key=lambda r: (r['date'], r['invoice_id'], r['lawyer_id']), reverse=True)
    return {'currency': currency, 'summary': {'amount': format(sum(trend.values(), Decimal(0)), '.2f'),
                'invoice_count': len(included), 'lawyer_count': len({r['lawyer_id'] for r in details}),
                'unresolved_count': unresolved},
            'trend': [{'label': k, 'amount': format(v, '.2f')} for k, v in sorted(trend.items())],
            'breakdown': [{'id': k, 'label': labels[k], 'amount': format(v, '.2f')}
                          for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))],
            'items': details[(page-1)*page_size:page*page_size], 'total': len(details), 'page': page}
