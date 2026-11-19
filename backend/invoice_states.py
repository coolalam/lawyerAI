"""Invoice application states confirmed against JianWeiOA's financeinvoice pages.

invoiceReceiveList.aspx.vb defines N/A/B/C/D/X; invoiceApplyList.aspx.vb
also defines Z (awaiting approval), and invoiceReceiveList.aspx defines S
(administration approval). iv_status describes payment/voiding, not approval.
"""

APPLICATION_STATES = {
    'N': 'draft', 'Z': 'submitted', 'S': 'submitted', 'A': 'approved',
    'B': 'issued', 'C': 'collected', 'D': 'rejected', 'X': 'void',
}


def _aliases():
    aliases = {}
    for invoice in ('', 'A', 'N', 'Y', 'Z', 'X'):
        for application, status in APPLICATION_STATES.items():
            target = 'void' if invoice == 'X' else status
            for code in (invoice + '|' + application, invoice + '/' + application, invoice + application):
                aliases[code] = target
                aliases['LEGACY:' + code] = target
    return aliases


LEGACY_INVOICE_STATES = _aliases()


def legacy_invoice_status(value):
    return LEGACY_INVOICE_STATES.get(str(value or '').strip().upper())


def normalize_invoice(record):
    """Translate confirmed codes on read; preserve source data and explicit new states."""
    status = record.get('status')
    mapped = legacy_invoice_status(status)
    if mapped:
        record.setdefault('legacy_status', status)
        record['status'] = mapped
    return record


def invoice_status_sql(expression):
    """Use the same aliases for filtering and duplicate invoice checks as for reads."""
    clauses = []
    for target in dict.fromkeys(LEGACY_INVOICE_STATES.values()):
        codes = ','.join("'" + code + "'" for code, state in LEGACY_INVOICE_STATES.items() if state == target)
        clauses.append(f"WHEN upper(trim({expression})) IN ({codes}) THEN '{target}'")
    return 'CASE ' + ' '.join(clauses) + f' ELSE {expression} END'
