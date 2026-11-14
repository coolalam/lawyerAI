import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request
from .db import db, get_record


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
    return f'{salt}:{digest}'


def verify_password(password, stored):
    if not stored or ':' not in stored:
        return False
    salt, digest = stored.split(':')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
    return hmac.compare_digest(digest, actual)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def expiry(hours=12):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec='seconds')


def public_user(user):
    return {key: user[key] for key in ('id', 'username', 'name', 'role', 'lawyer_id', 'active')}


def current_user(request: Request):
    token = request.cookies.get('law_session', '')
    with db() as conn:
        user = conn.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',
                            (token_hash(token), datetime.now(timezone.utc).isoformat(timespec='seconds'))).fetchone()
    if not user:
        raise HTTPException(401, '请先登录')
    return dict(user)


def require_role(user, *roles):
    if user['role'] not in roles:
        raise HTTPException(403, '当前角色没有此操作权限')


def visible_case(conn, user, case):
    if user['role'] in ('admin', 'partner'):
        return True
    if user['role'] in ('finance', 'clerk'):
        return not case.get('confidential', False)
    if case.get('lead_lawyer_id') == user.get('lawyer_id') and user.get('lawyer_id'):
        return True
    return bool(conn.execute("SELECT 1 FROM records WHERE kind='allocations' AND case_id=? AND deleted=0 AND json_extract(data,'$.lawyer_id')=? LIMIT 1",
                             (case['id'], user.get('lawyer_id') or '')).fetchone())


def check_access(conn, user, record, write=False):
    if not record:
        raise HTTPException(404, '记录不存在')
    kind = record['kind']
    if kind == 'cases':
        case = record
    else:
        case = get_record(conn, record.get('case_id')) if record.get('case_id') else None
    if case and not visible_case(conn, user, case):
        raise HTTPException(403, '无权访问此案件')
    if not case and kind not in ('cases', 'lawyers') and user['role'] not in ('admin', 'partner'):
        raise HTTPException(403, '未关联案件的历史数据仅管理员及合伙人可访问')
    if record.get('confidential') and user['role'] in ('finance', 'clerk'):
        raise HTTPException(403, '无权访问保密文档')
    if write:
        if case and case.get('status') in ('archived', 'void'):
            raise HTTPException(409, '归档或撤销案件已锁定')
        allowed = {
            'cases': ('admin', 'partner', 'lawyer'), 'lawyers': ('admin',),
            'parties': ('admin', 'partner', 'lawyer'), 'allocations': ('admin', 'partner', 'finance'),
            'invoices': ('admin', 'partner', 'lawyer', 'finance'), 'receipts': ('admin', 'finance'),
            'payment_plans': ('admin', 'partner', 'finance'), 'documents': ('admin', 'partner', 'lawyer', 'clerk'),
            'seals': ('admin', 'partner', 'lawyer', 'clerk'), 'tasks': ('admin', 'partner', 'lawyer', 'clerk'),
            'contracts': ('admin', 'partner', 'lawyer', 'clerk'),
            'conflicts': ('admin', 'partner', 'lawyer'),
        }
        require_role(user, *allowed.get(kind, ('admin',)))
