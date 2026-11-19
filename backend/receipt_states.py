"""JianWeiOA receipt A/S/Y/B are received funds; S/Y/B track later distribution.

receivecaseregister.aspx.vb creates A; receivemain.aspx.vb deletes with X and
sends to partners with S; myreceivelst.aspx labels Y confirmed and B allocated.
invoicemain.aspx.vb sums all non-X payments as received invoice funds.
"""
RECEIPT_STATES = {'A': 'confirmed', 'S': 'confirmed', 'Y': 'confirmed', 'B': 'confirmed', 'X': 'void'}
ALIASES = {prefix + code: state for code, state in RECEIPT_STATES.items() for prefix in ('', 'LEGACY:')}


def normalize_receipt(record):
    status = record.get('status')
    mapped = ALIASES.get(str(status or '').strip().upper())
    if mapped:
        record.setdefault('legacy_status', status)
        record['status'] = mapped
    elif str(status or '').lower().startswith('legacy:'):
        record['legacy_status_label'] = '到账状态待核对'
    return record


def receipt_status_sql(expression):
    clauses = [f"WHEN upper(trim({expression})) IN ({','.join(repr(code) for code, state in ALIASES.items() if state == target)}) THEN '{target}'"
               for target in ('confirmed', 'void')]
    return 'CASE ' + ' '.join(clauses) + f' ELSE {expression} END'
