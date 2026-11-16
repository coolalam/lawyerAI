"""Case-list filtering on the compact, transactionally maintained search table."""
from fastapi import HTTPException

from .db import get_record
from .security import check_access


def literal_pattern(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def case_list_query(conn, user, q='', case_id=None, status=None, branch=''):
    conditions, args = [], []
    if case_id:
        check_access(conn, user, get_record(conn, case_id, 'cases'))
        conditions.append('c.id IN (SELECT id FROM records WHERE case_id=?)')
        args.append(case_id)
    if status:
        conditions.append('c.status=?')
        args.append(status)
    if branch:
        branch = branch.strip().upper()
        if not branch or len(branch) > 20 or not branch.isascii() or not branch.isalpha():
            raise HTTPException(422, '机构编码无效')
        conditions.append("c.number LIKE ? ESCAPE '\\'")
        args.append(branch + '%')
    if user['role'] == 'lawyer':
        conditions.append("(c.lead_lawyer_id=? OR c.id IN (SELECT case_id FROM records WHERE kind='allocations' AND deleted=0 AND json_extract(data,'$.lawyer_id')=?))")
        args.extend([user.get('lawyer_id') or '', user.get('lawyer_id') or ''])
    elif user['role'] not in ('admin', 'partner'):
        conditions.append('COALESCE(c.confidential,0)=0')

    q = q.strip()
    if q:
        # Check within the caller's filters and permissions: a hidden exact match
        # must not suppress visible substring matches or disclose its existence.
        exact = ' AND '.join([*conditions, 'c.number=? COLLATE NOCASE'])
        if conn.execute('SELECT 1 FROM case_search c WHERE ' + exact + ' LIMIT 1', [*args, q]).fetchone():
            return exact, [*args, q]
        columns = ('number', 'title', 'client', 'opponent')
        conditions.append('(' + ' OR '.join(f"c.{column} LIKE ? ESCAPE '\\'" for column in columns) + ')')
        args.extend([literal_pattern(q)] * len(columns))
    return ' AND '.join(conditions) or '1=1', args


def case_list_rows(conn, where, args, limit=-1, offset=0):
    # LIMIT keeps paging ahead of the join, so only the selected rows' large JSON
    # payloads are read. The final ordering is stable even for equal timestamps.
    return conn.execute('''SELECT r.* FROM (
        SELECT c.id,c.updated_at FROM case_search c WHERE ''' + where + '''
        ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?
    ) matched JOIN records r ON r.id=matched.id
    ORDER BY matched.updated_at DESC,matched.id''', [*args, limit, offset]).fetchall()
