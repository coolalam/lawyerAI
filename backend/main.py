import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import DATA, ROOT, audit, db, encode, get_record, init_db, now, save_record, uid, unpack
from .schema import GLOBAL_KINDS, INITIAL, ROLES, SCHEMAS, STATES, TRANSITIONS
from .security import check_access, current_user, expiry, password_hash, public_user, require_role, token_hash, verify_password, visible_case
from .service import allocations_for, children, conflict_identity, finance_summary, matching_conflicts, money, split_amount, validate, validate_refs
from migration.references import References
from .mcp_server import gateway as mcp_gateway, mcp_lifespan, router as mcp_router


@asynccontextmanager
async def lifespan(app):
    init_db()
    async with mcp_lifespan():
        yield


app = FastAPI(title='易律网 · 律所智能办公平台', version='1.0.0', lifespan=lifespan)


def _origin(scheme: str, host: str) -> str:
    """Build a normalized origin from the values supplied by the ASGI server."""
    return f'{scheme.strip().lower()}://{host.strip()}'.rstrip('/')


def _referer_origin(value: str) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return _origin(parsed.scheme, parsed.netloc)
    return None


def request_origins(request: Request) -> set[str]:
    """Return origins that identify this request's public URL.

    A container commonly sits behind an HTTPS reverse proxy. In that setup
    ``request.base_url`` can still point at the internal ``http://...:8000``
    address while the browser sends the public origin. Honour the standard
    forwarding headers so same-origin writes (including API key creation) are
    not rejected by the CSRF check.
    """
    origins = {str(request.base_url).rstrip('/')}
    host = request.headers.get('x-forwarded-host') or request.headers.get('host')
    if host:
        # Proxies may append their own value; the first value is the original
        # host. Strip whitespace but preserve an explicit port.
        host = host.split(',', 1)[0].strip()
        forwarded_proto = request.headers.get('x-forwarded-proto', '')
        scheme = (forwarded_proto.split(',', 1)[0].strip() if forwarded_proto else request.url.scheme)
        origins.add(_origin(scheme, host))
    return origins


@app.middleware('http')
async def request_security(request, call_next):
    if request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
        origin = request.headers.get('origin')
        if origin:
            allowed = request_origins(request)
            allowed.update(value.strip().rstrip('/') for value in os.environ.get('LAW_ALLOWED_ORIGINS', '').split(',') if value.strip())
            allowed.update(('http://127.0.0.1:5173', 'http://localhost:5173'))
            # When a proxy rewrites Host and drops forwarding headers, modern
            # browsers still identify a same-origin request in Fetch Metadata.
            # Referer is an additional browser-controlled fallback for clients
            # that do not send Sec-Fetch-Site. Neither signal is accepted for
            # cross-origin requests, so the explicit origin allowlist remains
            # the normal path.
            same_origin = request.headers.get('sec-fetch-site', '').lower() == 'same-origin'
            same_origin = same_origin or _referer_origin(request.headers.get('referer', '')) == origin
            if origin not in allowed and not same_origin:
                return JSONResponse({'detail': '请求来源不受信任'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    if request.url.path.startswith(('/api','/mcp')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse({'detail': '编号已存在或关联数据不完整，请核对后重试'}, status_code=409)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=409)


def kind_check(kind):
    if kind not in SCHEMAS and kind != 'allocations':
        raise HTTPException(404, '模块不存在')


def safe_password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 200:
        raise HTTPException(422, '密码长度需为 12–200 位')
    return password_hash(value)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/bootstrap')
def bootstrap():
    with db() as conn:
        return {'setup_required': conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 0}


@app.post('/api/setup')
def setup(data: dict, request: Request):
    if request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, '首次初始化仅允许在服务器本机进行')
    hashed = safe_password(data.get('password'))
    username = str(data.get('username', 'admin')).strip()
    name = str(data.get('name', '管理员')).strip()
    if not username or not name:
        raise HTTPException(422, '请输入管理员账号及姓名')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]:
            raise HTTPException(409, '系统已初始化')
        user_id = uid()
        conn.execute('INSERT INTO users(id,username,name,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',
                     (user_id, username, name, hashed, 'admin', now()))
        audit(conn, {'id': user_id, 'name': name}, '初始化系统', 'users', user_id)
        if data.get('demo') is True:
            from .seed import seed_demo
            seed_demo(conn, {'id': user_id, 'name': name})
    return {'ok': True}


@app.post('/api/auth/login')
def login(data: dict, response: Response, request: Request):
    username = str(data.get('username', '')).strip()
    key = request.client.host + ':' + username
    failed = False
    with db() as conn:
        attempt = conn.execute('SELECT * FROM login_attempts WHERE key=?', (key,)).fetchone()
        if attempt and attempt['until_at'] > now() and attempt['count'] >= 8:
            raise HTTPException(429, '尝试次数过多，请 15 分钟后重试')
        user = conn.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
        if not user or not verify_password(str(data.get('password', '')), user['password_hash']):
            count = attempt['count'] + 1 if attempt and attempt['until_at'] > now() else 1
            conn.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)', (key, count, expiry(.25)))
            failed = True
        else:
            conn.execute('DELETE FROM login_attempts WHERE key=?', (key,))
            conn.execute('DELETE FROM sessions WHERE expires_at<?', (now(),))
            token = secrets.token_urlsafe(48)
            conn.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), user['id'], expiry()))
            audit(conn, user, '登录', 'users', user['id'])
    if failed:
        raise HTTPException(401, '账号或密码错误')
    response.set_cookie('law_session', token, httponly=True, samesite='strict', secure=os.environ.get('LAW_HTTPS') == '1', max_age=43200)
    return public_user(user)


@app.post('/api/auth/logout')
def logout(request: Request, response: Response):
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(request.cookies.get('law_session', '')),))
    response.delete_cookie('law_session')
    return {'ok': True}


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return public_user(user)


@app.post('/api/auth/password')
def change_password(data: dict, user=Depends(current_user)):
    if not verify_password(str(data.get('old_password', '')), user['password_hash']):
        raise HTTPException(422, '原密码不正确')
    hashed = safe_password(data.get('password'))
    with db() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE id=?', (hashed, user['id']))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?',(user['id'],))
        conn.execute("UPDATE password_reset_requests SET status='completed' WHERE user_id=?",(user['id'],))
        audit(conn, user, '修改密码', 'users', user['id'])
    return {'ok': True}


def reset_rate_limit(conn, request, action):
    key='reset:'+action+':'+request.client.host
    attempt=conn.execute('SELECT count,until_at FROM login_attempts WHERE key=?',(key,)).fetchone()
    if attempt and attempt['until_at']>now() and attempt['count']>=10:
        return False
    count=attempt['count']+1 if attempt and attempt['until_at']>now() else 1
    conn.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,expiry(.25)))
    return True


@app.post('/api/auth/forgot-password')
def forgot_password(data:dict,request:Request):
    username=str(data.get('username','')).strip()[:200]
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        allowed=reset_rate_limit(conn,request,'request')
        if allowed:
            account=conn.execute('SELECT id FROM users WHERE username=? AND active=1',(username,)).fetchone()
            if account:
                conn.execute("INSERT INTO password_reset_requests VALUES(?,?,'pending') ON CONFLICT(user_id) DO UPDATE SET requested_at=excluded.requested_at,status='pending'",(account['id'],now()))
    if not allowed:raise HTTPException(429,'请求过于频繁，请 15 分钟后再试')
    return {'message':'如账号存在且已启用，重置申请已提交。请联系管理员核验身份后获取一次性重置码。'}


@app.get('/api/admin/password-reset-requests')
def reset_requests(user=Depends(current_user)):
    require_role(user,'admin')
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT r.*,u.username,u.name,u.active FROM password_reset_requests r JOIN users u ON u.id=r.user_id WHERE r.status IN ('pending','issued') ORDER BY r.requested_at DESC")]


@app.post('/api/admin/users/{user_id}/password-reset-token')
def issue_reset_token(user_id:str,data:dict,user=Depends(current_user)):
    require_role(user,'admin')
    note=str(data.get('verification_note','')).strip()
    if len(note)<5 or len(note)>500:raise HTTPException(422,'请填写 5–500 字身份核验说明')
    token=secrets.token_urlsafe(32)
    deadline=expiry(1)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        account=conn.execute('SELECT id,active FROM users WHERE id=?',(user_id,)).fetchone()
        if not account or not account['active']:raise HTTPException(409,'账号不存在或已停用，请先核对账号归属和启用状态')
        conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?',(user_id,))
        conn.execute('INSERT INTO password_reset_tokens VALUES(?,?,?,?)',(token_hash(token),user_id,deadline,now()))
        conn.execute("INSERT INTO password_reset_requests VALUES(?,?,'issued') ON CONFLICT(user_id) DO UPDATE SET status='issued'",(user_id,now()))
        audit(conn,user,'签发密码重置码','users',user_id,note)
    return {'token':token,'expires_at':deadline}


@app.post('/api/auth/reset-password')
def reset_password(data:dict,request:Request):
    token=str(data.get('token',''))[:200]
    password=data.get('password')
    if not isinstance(password,str) or not 12<=len(password)<=200:raise HTTPException(422,'密码长度需为 12–200 位')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        allowed=reset_rate_limit(conn,request,'consume')
        account=conn.execute('SELECT u.id,u.name FROM password_reset_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.expires_at>? AND u.active=1',(token_hash(token),now())).fetchone() if allowed else None
        if account:
            conn.execute('UPDATE users SET password_hash=? WHERE id=?',(safe_password(password),account['id']))
            conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?',(account['id'],))
            conn.execute('DELETE FROM sessions WHERE user_id=?',(account['id'],))
            conn.execute("UPDATE password_reset_requests SET status='completed' WHERE user_id=?",(account['id'],))
            audit(conn,dict(account),'使用一次性重置码设置密码','users',account['id'])
    if not allowed:raise HTTPException(429,'尝试过于频繁，请 15 分钟后再试')
    if not account:raise HTTPException(422,'重置码无效、已过期或已使用')
    return {'ok':True}


@app.get('/api/schema')
def schemas(user=Depends(current_user)):
    schema, states = deepcopy(SCHEMAS), deepcopy(STATES)
    with db() as conn:
        categories = {'cases':'case_status','lawyers':'employee_status','conflicts':'conflict_status','invoices':'invoice_status','receipts':'payment_status','seals':'seal_status','contracts':'contract_status','documents':'document_status','tasks':'task_status','payment_plans':'payment_plan_status'}
        for kind, category in categories.items():
            for row in conn.execute('SELECT code,name FROM managed_metadata WHERE category=?',(category,)):
                if row['code'] in states[kind]:
                    states[kind][row['code']] = row['name']
        references = References(conn)
        departments = [references.enrich_department(dict(r)) for r in conn.execute('SELECT id,office,code,name FROM managed_departments WHERE active=1 ORDER BY sort_order,name')]
        schema['lawyers']['branches'] = references.branch_options()
        causes = [dict(r) for r in conn.execute("SELECT code,name FROM managed_metadata WHERE category='case_cause' AND active=1 ORDER BY sort_order,code")]
        for kind in ('cases','lawyers'):
            for field in schema[kind]['fields']:
                if field['key']=='department': field['suggestions']=departments
                if field['key']=='cause': field['suggestions']=causes
    return {'schemas': schema, 'states': states, 'transitions': TRANSITIONS, 'roles': ROLES}


def visible_query(conn, user, kind, q='', case_id=None, status=None, branch=''):
    # A literal case kind lets SQLite prove that the partial branch index applies.
    conditions, args = (["r.kind='cases'", 'r.deleted=0'], []) if kind == 'cases' else (['r.kind=?', 'r.deleted=0'], [kind])
    if case_id:
        case = get_record(conn, case_id, 'cases')
        check_access(conn, user, case)
        conditions.append('r.case_id=?')
        args.append(case_id)
    if status:
        if kind == 'lawyers':
            conditions.append("CASE WHEN r.version=1 AND json_extract(r.data,'$.legacy_table')='t_employee' AND trim(json_extract(r.data,'$.legacy_data.emp_status'))='X' AND json_extract(r.data,'$.status')='active' THEN 'inactive' ELSE json_extract(r.data,'$.status') END=?")
        else:
            conditions.append("json_extract(r.data,'$.status')=?")
        args.append(status)
    if kind == 'cases' and branch:
        branch = branch.strip().upper()
        if not branch or len(branch) > 20 or not branch.isascii() or not branch.isalpha():
            raise HTTPException(422, '机构编码无效')
        if len(branch) >= 2:
            conditions.append("substr(upper(json_extract(r.data,'$.number')),1,2)=?")
            args.append(branch[:2])
        if len(branch) != 2:
            conditions.append("UPPER(json_extract(r.data,'$.number')) LIKE ?")
            args.append(branch + '%')
    if q:
        # Literal substring search; % and _ are not treated as user wildcards.
        conditions.append("r.data LIKE ? ESCAPE '\\'")
        args.append('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    if kind != 'lawyers' and user['role'] not in ('admin', 'partner'):
        case_expr = 'r.id' if kind == 'cases' else 'r.case_id'
        if user['role'] == 'lawyer':
            conditions.append(f"{case_expr} IN (SELECT id FROM records WHERE kind='cases' AND (json_extract(data,'$.lead_lawyer_id')=? OR id IN (SELECT case_id FROM records WHERE kind='allocations' AND deleted=0 AND json_extract(data,'$.lawyer_id')=?)))")
            args.extend([user.get('lawyer_id') or '', user.get('lawyer_id') or ''])
        else:
            conditions.append(f"{case_expr} IN (SELECT id FROM records WHERE kind='cases' AND COALESCE(json_extract(data,'$.confidential'),0)=0)")
            if kind == 'documents':
                conditions.append("COALESCE(json_extract(r.data,'$.confidential'),0)=0")
    return ' AND '.join(conditions), args


def records_source(kind, branch):
    # Large migrated JSON rows make a scan through the generic kind index costly.
    # Keep both counting and paging on the prefix index when a prefix is supplied.
    if kind == 'cases' and len(branch.strip()) >= 2:
        return 'records r INDEXED BY idx_case_branch'
    return 'records r'


@app.get('/api/records/{kind}')
def list_records(kind: str, q: str = '', case_id: str | None = None, status: str | None = None, branch: str = '',
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), user=Depends(current_user)):
    kind_check(kind)
    with db() as conn:
        where, args = visible_query(conn, user, kind, q, case_id, status, branch)
        source = records_source(kind, branch)
        total = conn.execute('SELECT COUNT(*) FROM ' + source + ' WHERE ' + where, args).fetchone()[0]
        rows = conn.execute('SELECT r.* FROM ' + source + ' WHERE ' + where + ' ORDER BY r.updated_at DESC,r.id LIMIT ? OFFSET ?', args + [page_size, (page - 1) * page_size]).fetchall()
        items = [unpack(row) for row in rows]
        references = References(conn) if kind in ('cases','lawyers') else None
        for item in items:
            if references: references.enrich(item)
            if kind == 'cases':
                item['finance'] = finance_summary(conn, item)
            elif item.get('case_id'):
                case = get_record(conn, item['case_id'])
                item['case_title'] = case.get('title') if case else ''
                item['case_number'] = case.get('number') if case else ''
            if kind=='conflicts' and item.get('source_key') and item.get('legacy_data',{}).get('csm_search_id'):
                item['snapshot_count']=conn.execute('SELECT COUNT(*) FROM src__t_conflict_search_det WHERE csd_search_id=?',(item['legacy_data']['csm_search_id'],)).fetchone()[0]
            # Full legacy fields are available only in detail; keep list payload small.
            item.pop('legacy_data', None)
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


@app.get('/api/records/{kind}/{record_id}')
def record_detail(kind: str, record_id: str, user=Depends(current_user)):
    kind_check(kind)
    with db() as conn:
        item = get_record(conn, record_id, kind)
        check_access(conn, user, item)
        if kind in ('cases','lawyers'): References(conn).enrich(item)
        if kind == 'lawyers' and user['role'] != 'admin':
            item.pop('legacy_data', None)
        if kind == 'cases':
            item['finance'] = finance_summary(conn, item)
            counts = conn.execute('SELECT kind,COUNT(*) n FROM records WHERE case_id=? AND deleted=0 GROUP BY kind', (record_id,))
            item['counts'] = {r['kind']: r['n'] for r in counts}
            item['conflict_fingerprint'] = conflict_identity(conn, item)
        return item


@app.get('/api/conflicts/{record_id}/snapshot')
def conflict_snapshot(record_id: str, page: int = Query(1,ge=1), user=Depends(current_user)):
    with db() as conn:
        record=get_record(conn,record_id,'conflicts')
        check_access(conn,user,record)
        raw=record.get('legacy_data') or {}
        source_id=raw.get('csm_search_id')
        if not record.get('source_key') or not source_id:
            return {'items':[], 'total':0, 'page':page, 'page_size':20,
                    'historical':bool(record.get('source_key')), 'summary':record.get('legacy_case_review')}
        total=conn.execute('SELECT COUNT(*) FROM src__t_conflict_search_det WHERE csd_search_id=?',(source_id,)).fetchone()[0]
        rows=conn.execute('SELECT * FROM src__t_conflict_search_det WHERE csd_search_id=? ORDER BY _migration_row_id LIMIT 20 OFFSET ?',
                          (source_id,(page-1)*20)).fetchall()
        # Hit rows have no office key. Never invent a link to a same-number case.
        items=[{'line':r['csd_line'],'query':r['csd_to_search'],'name':r['csd_client_name'],
                'case_number':r['csd_case_id'],'case_title':r['csd_case_name'],
                'relation':r['csd_client_type'],'source_status':r['csd_status'],'notes':r['csd_memo']} for r in rows]
        return {'items':items,'total':total,'page':page,'page_size':20,'historical':True,
                'summary':{k:raw.get(k) for k in ('csm_search_date','csm_result','csm_description','csm_check_info','csm_result2','csm_check_info2')}}


def ensure_case_active(conn, case_id, kind):
    if kind in ('contracts', 'seals', 'invoices', 'receipts', 'payment_plans'):
        case = get_record(conn, case_id, 'cases')
        if not case or case['status'] not in ('active', 'closed'):
            raise HTTPException(409, '请先完成利冲审批和立案审批')
        reports = children(conn, case_id, 'conflicts')
        if reports and not reports[0].get('source_key') and not cleared_conflict(conn, case):
            raise HTTPException(409, '当前利冲审查未通过或当事人信息已变更，请先完成复核')


def bind_managed_references(conn, kind, item, data):
    if kind not in ('cases','lawyers'): return
    if data.get('department_id'):
        dept=conn.execute('SELECT * FROM managed_departments WHERE id=? AND active=1',(data['department_id'],)).fetchone()
        if not dept: raise HTTPException(422, '请选择启用的部门')
        item.update(department_id=dept['id'],office=dept['office'],department=dept['name'],department_code=dept['code'])
    elif 'department' in data or 'office' in data:
        rows = conn.execute('SELECT id FROM managed_departments WHERE active=1 AND office=? AND (name=? OR code=?)',
                            (item.get('office',''),item.get('department',''),item.get('department',''))).fetchall()
        item.pop('department_id',None)
        if len(rows)==1: item['department_id']=rows[0]['id']
    if kind=='lawyers' and 'branch_code' in data:
        branch=str(data['branch_code'] or '').strip()
        if branch and (item.get('office'),branch) not in References(conn).branches:
            raise HTTPException(422, '分支机构不存在')
        item['branch_code']=branch
    if kind=='cases' and 'cause' in data:
        rows = conn.execute("SELECT code FROM managed_metadata WHERE active=1 AND category='case_cause' AND (name=? OR code=?)",(item.get('cause',''),item.get('cause',''))).fetchall()
        item.pop('cause_code',None)
        if len(rows)==1: item['cause_code']=rows[0]['code']


@app.post('/api/records/{kind}', status_code=201)
def create_record(kind: str, data: dict, user=Depends(current_user)):
    kind_check(kind)
    if kind in ('allocations', 'conflicts', 'documents'):
        raise HTTPException(422, '请使用对应的专用流程')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        case_id = data.get('case_id')
        if kind in GLOBAL_KINDS:
            case_id = None
        if kind not in GLOBAL_KINDS and not case_id:
            raise HTTPException(422, '请选择所属案件')
        if kind == 'cases':
            require_role(user, 'admin', 'partner', 'lawyer')
            if user['role'] == 'lawyer' and data.get('lead_lawyer_id') != user.get('lawyer_id'):
                raise HTTPException(403, '律师只能以本人为主办发起立案')
            case_id = None
        else:
            check_access(conn, user, {'kind': kind, 'case_id': case_id}, write=True)
            ensure_case_active(conn, case_id, kind)
        item = validate(kind, data)
        bind_managed_references(conn,kind,item,data)
        item['case_id'] = case_id
        if kind in ('invoices', 'receipts', 'payment_plans'):
            item['currency'] = get_record(conn, case_id, 'cases').get('currency', 'CNY')
        validate_refs(conn, kind, item)
        item.update(status=INITIAL.get(kind, 'active'), applicant_id=user['id'], applicant_name=user['name'], history=[])
        result = save_record(conn, kind, item, user['id'], case_id=case_id)
        audit(conn, user, '创建', kind, result['id'], item.get('title') or item.get('name', ''))
        return result


@app.patch('/api/records/{kind}/{record_id}')
def edit_record(kind: str, record_id: str, data: dict, user=Depends(current_user)):
    kind_check(kind)
    if kind in ('allocations', 'conflicts'):
        raise HTTPException(422, '请使用分配方案或审批流程修改')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = get_record(conn, record_id, kind)
        check_access(conn, user, old, write=True)
        if kind == 'lawyers': old['status'] = References(conn).enrich(dict(old))['status']
        if not data.get('version'):
            raise HTTPException(422, '缺少版本号')
        if data.get('case_id') and data['case_id'] != old.get('case_id'):
            raise HTTPException(422, '不能更换所属案件')
        locked = {'cases': ('submitted',), 'invoices': ('submitted', 'issued', 'void'), 'receipts': ('confirmed', 'void'),
                  'seals': ('submitted', 'collected', 'void'), 'contracts': ('archived',)}
        if old.get('status') in locked.get(kind, ()):
            raise HTTPException(409, '当前状态已锁定，请通过审批 / 作废流程处理')
        if kind == 'cases' and user['role'] == 'lawyer' and data.get('lead_lawyer_id', old.get('lead_lawyer_id')) != old.get('lead_lawyer_id'):
            raise HTTPException(403, '变更主办律师须由合伙人处理')
        item = validate(kind, data, old)
        bind_managed_references(conn,kind,item,data)
        def changed(field):
            key = field['key']
            before = old.get(key)
            if before is None:
                before = field['default'] if field['default'] is not None else (False if field['type'] == 'boolean' else '')
            return key in data and item.get(key) != before
        if kind in ('invoices', 'seals') and old['status'] == 'approved':
            # Post-approval only issuance / filing details may change.
            permitted = {'number', 'issued_on', 'notes'} if kind == 'invoices' else {'archive_document_id', 'collector', 'notes'}
            if any(changed(f) for f in SCHEMAS[kind]['fields'] if f['key'] not in permitted):
                raise HTTPException(409, '审批后不能修改金额、用印内容等核心信息')
        validate_refs(conn, kind, item)
        if kind == 'seals' and old['status'] == 'stamped' and any(changed(f) for f in SCHEMAS[kind]['fields'] if f['key'] not in ('collector', 'notes')):
            raise HTTPException(409, '盖章完成后只能登记领取人及备注')
        if kind == 'contracts' and old['status'] in ('stamping', 'stamped') and any(changed(f) for f in SCHEMAS[kind]['fields'] if f['key'] not in ('archive_number', 'archive_location', 'notes')):
            raise HTTPException(409, '盖章流程中的合同原件及归档文件不能直接替换')
        if kind == 'cases' and old['status'] in ('active', 'closed') and any(item.get(k) != old.get(k) for k in ('client', 'opponent')):
            raise HTTPException(409, '已立案主体不能直接变更，请新建关联案件并重新进行利冲审查')
        result = save_record(conn, kind, item, user['id'], record_id=record_id, expected_version=data['version'])
        audit(conn, user, '修改', kind, record_id)
        return result


@app.delete('/api/records/{kind}/{record_id}')
def delete_record(kind: str, record_id: str, version: int, user=Depends(current_user)):
    kind_check(kind)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = get_record(conn, record_id, kind)
        check_access(conn, user, item, write=True)
        if item.get('source_key') or kind in ('cases', 'conflicts', 'allocations', 'lawyers'):
            raise HTTPException(409, '此记录须通过状态流转处理，不可删除')
        if item.get('status') not in ('draft', 'open', 'active', 'missing'):
            raise HTTPException(409, '已审批或已入账记录不能删除')
        if kind == 'documents':
            if conn.execute("SELECT 1 FROM records WHERE deleted=0 AND kind IN ('contracts','seals') AND (json_extract(data,'$.document_id')=? OR json_extract(data,'$.archive_document_id')=?) LIMIT 1", (record_id, record_id)).fetchone():
                raise HTTPException(409, '文件正在被合同或用印流程使用')
        if kind in ('invoices', 'payment_plans'):
            field = 'invoice_id' if kind == 'invoices' else 'plan_id'
            if conn.execute(f"SELECT 1 FROM records WHERE deleted=0 AND json_extract(data,'$.{field}')=?", (record_id,)).fetchone():
                raise HTTPException(409, '存在关联到账记录，不能删除')
        result = conn.execute('UPDATE records SET deleted=1,version=version+1,updated_at=? WHERE id=? AND version=?', (now(), record_id, version))
        if result.rowcount != 1:
            raise HTTPException(409, '数据已更新，请刷新')
        audit(conn, user, '删除（保留历史）', kind, record_id)
    return {'ok': True}


def cleared_conflict(conn, case):
    fingerprint = conflict_identity(conn, case)
    reports = children(conn, case['id'], 'conflicts')
    return bool(reports and reports[0].get('status') in ('cleared', 'waived') and reports[0].get('fingerprint') == fingerprint)


@app.post('/api/records/{kind}/{record_id}/transition')
def transition(kind: str, record_id: str, data: dict, user=Depends(current_user)):
    kind_check(kind)
    target, note = data.get('status'), str(data.get('note', '')).strip()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = get_record(conn, record_id, kind)
        check_access(conn, user, item, write=True)
        if data.get('version') != item['version']:
            raise HTTPException(409, '数据已更新，请刷新后操作')
        if target not in TRANSITIONS.get(kind, {}).get(item.get('status'), []):
            raise HTTPException(409, '不允许跳过流程节点')
        if target != 'void':
            ensure_case_active(conn, item.get('case_id'), kind)
        if target in ('approved', 'rejected', 'blocked', 'cleared', 'waived', 'void', 'archived', 'active', 'confirmed') and not note:
            raise HTTPException(422, '请填写审批意见或操作原因')
        if kind == 'cases':
            if target in ('draft', 'submitted', 'active') and not cleared_conflict(conn, item):
                raise HTTPException(409, '须先完成当前当事人信息的利冲审批')
            if target == 'submitted':
                validate('cases', {f['key']: item.get(f['key']) for f in SCHEMAS['cases']['fields']})
            if target in ('active', 'rejected', 'archived', 'void'):
                require_role(user, 'admin', 'partner')
            if target == 'closed' and not item.get('result'):
                raise HTTPException(422, '请先填写结案结果')
            if target == 'archived' and (not item.get('archive_number') or not item.get('archive_location')):
                raise HTTPException(422, '请先填写归档编号和卷宗存放位置')
        elif kind == 'conflicts':
            require_role(user, 'admin', 'partner')
            case = get_record(conn, item['case_id'], 'cases')
            if item.get('fingerprint') != conflict_identity(conn, case):
                raise HTTPException(409, '当事人已变更，请重新发起利冲检索')
            if target == 'cleared' and (item.get('matches') or item.get('truncated')):
                raise HTTPException(422, '存在潜在冲突，须说明豁免依据或禁止承接')
            item['review_note'] = note
        elif kind == 'invoices':
            if target in ('approved', 'rejected', 'issued', 'void'):
                require_role(user, 'admin', 'finance')
            if target == 'issued':
                if not item.get('number') or not item.get('issued_on'):
                    raise HTTPException(422, '请先登记发票号码和开票日期')
                if conn.execute("SELECT 1 FROM records WHERE kind='invoices' AND deleted=0 AND id<>? AND json_extract(data,'$.number')=? AND json_extract(data,'$.status')='issued'", (record_id, item['number'])).fetchone():
                    raise HTTPException(409, '此发票号码已经开具')
                item['allocation_snapshot'] = allocation_snapshot(conn, item)
            if target == 'void' and conn.execute("SELECT 1 FROM records WHERE kind='receipts' AND deleted=0 AND json_extract(data,'$.invoice_id')=? AND json_extract(data,'$.status')='confirmed'", (record_id,)).fetchone():
                raise HTTPException(409, '请先处理该发票已确认的到账记录')
        elif kind == 'receipts':
            require_role(user, 'admin', 'finance')
            if target == 'confirmed':
                if item.get('invoice_id'):
                    invoice = get_record(conn, item['invoice_id'], 'invoices')
                    if not invoice or invoice['status'] != 'issued':
                        raise HTTPException(409, '关联发票尚未开具')
                if conn.execute("SELECT 1 FROM records WHERE kind='receipts' AND deleted=0 AND id<>? AND json_extract(data,'$.reference')=? AND json_extract(data,'$.status')='confirmed'", (record_id, item['reference'])).fetchone():
                    raise HTTPException(409, '该银行流水已确认，不能重复核销')
                item['allocation_snapshot'] = allocation_snapshot(conn, item)
        elif kind == 'seals':
            if target in ('approved', 'rejected'):
                require_role(user, 'admin', 'partner')
            if target in ('stamped', 'collected'):
                require_role(user, 'admin', 'clerk')
            if target == 'submitted':
                require_document(conn, item.get('document_id'), item['case_id'])
            if target == 'stamped':
                require_document(conn, item.get('archive_document_id'), item['case_id'])
                if item.get('contract_id'):
                    contract = get_record(conn, item['contract_id'], 'contracts')
                    contract.update(status='stamped', archive_document_id=item['archive_document_id'])
                    contract.setdefault('history', []).append({'at': now(), 'actor': user['name'], 'from': 'stamping', 'to': 'stamped', 'note': '关联用印完成'})
                    save_record(conn, 'contracts', contract, user['id'], record_id=contract['id'])
                    audit(conn, user, '合同盖章完成', 'contracts', contract['id'])
            if target == 'collected' and not item.get('collector'):
                raise HTTPException(422, '请先登记领取人')
            if target == 'void' and item.get('contract_id'):
                contract = get_record(conn, item['contract_id'], 'contracts')
                contract.update(status='draft', seal_id='')
                save_record(conn, 'contracts', contract, user['id'], record_id=contract['id'])
        elif kind == 'contracts':
            if target == 'stamping':
                require_document(conn, item.get('document_id'), item['case_id'])
                seal = save_record(conn, 'seals', {'title': item['title'], 'seal_type': '合同专用章', 'copies': 2,
                    'recipient': get_record(conn, item['case_id'])['client'], 'reason': '委托合同签署', 'document_id': item['document_id'],
                    'contract_id': item['id'], 'status': 'submitted', 'applicant_id': user['id'], 'applicant_name': user['name'], 'history': []}, user['id'], case_id=item['case_id'])
                item['seal_id'] = seal['id']
                audit(conn, user, '合同用印申请', 'seals', seal['id'])
            if target == 'archived':
                require_role(user, 'admin', 'clerk', 'partner')
                require_document(conn, item.get('archive_document_id'), item['case_id'])
                if not item.get('archive_number') or not item.get('archive_location'):
                    raise HTTPException(422, '须填写合同归档编号和存放位置')
        elif kind == 'lawyers':
            require_role(user, 'admin')
        previous = item['status']
        item.setdefault('history', []).append({'at': now(), 'actor': user['name'], 'from': previous, 'to': target, 'note': note})
        item['status'] = target
        result = save_record(conn, kind, item, user['id'], record_id=record_id, expected_version=data['version'])
        audit(conn, user, f'{STATES[kind].get(previous, previous)} → {STATES[kind].get(target, target)}', kind, record_id, note)
        return result


def require_document(conn, document_id, case_id):
    doc = get_record(conn, document_id, 'documents') if document_id else None
    if not doc or doc['case_id'] != case_id or not doc.get('files'):
        raise HTTPException(422, '须上传同一案件的实际文件，不能仅使用历史路径引用')


def allocation_snapshot(conn, item):
    rows = allocations_for(conn, item['case_id'], item['id']) or allocations_for(conn, item['case_id'], item['case_id'])
    if not rows:
        raise HTTPException(422, '请先设置合计 100% 的律师分配方案')
    return split_amount(item['amount'], [{'lawyer_id': r['lawyer_id'], 'ratio': r['ratio'], 'role': r.get('role', '')} for r in rows])


@app.put('/api/cases/{case_id}/allocations')
def set_allocations(case_id: str, data: dict, user=Depends(current_user)):
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        check_access(conn, user, {'kind': 'allocations', 'case_id': case_id}, write=True)
        scope_id = data.get('scope_id', case_id)
        scope = get_record(conn, scope_id)
        if not scope or scope['kind'] not in ('cases', 'invoices', 'receipts') or (scope_id != case_id and scope.get('case_id') != case_id):
            raise HTTPException(422, '分配对象不属于此案件')
        if scope.get('status') in ('issued', 'confirmed', 'void', 'archived'):
            raise HTTPException(409, '已记账的分配快照不可修改')
        if data.get('version') != scope['version']:
            raise HTTPException(409, '分配对象已更新，请刷新')
        rows = data.get('rows', [])
        if not 1 <= len(rows) <= 50 or len({r.get('lawyer_id') for r in rows}) != len(rows):
            raise HTTPException(422, '请添加 1–50 位不同律师')
        normalized = []
        for row in rows:
            lawyer = get_record(conn, row.get('lawyer_id'), 'lawyers')
            if not lawyer or lawyer.get('status') != 'active':
                raise HTTPException(422, '分配律师不存在或已离职')
            ratio = money(row.get('ratio'), 4)
            if not 0 < float(ratio) <= 100:
                raise HTTPException(422, '每位律师比例须大于 0 且不超过 100%')
            normalized.append({'lawyer_id': lawyer['id'], 'lawyer_name': lawyer['name'], 'ratio': ratio, 'role': str(row.get('role', '协办律师'))})
        calculated = split_amount(scope.get('amount', '0'), normalized)
        conn.execute("UPDATE records SET deleted=1,updated_at=? WHERE kind='allocations' AND case_id=? AND json_extract(data,'$.scope_id')=? AND deleted=0", (now(), case_id, scope_id))
        for row in calculated:
            save_record(conn, 'allocations', {**row, 'scope_id': scope_id, 'status': 'active'}, user['id'], case_id=case_id)
        # Scope version guards concurrent replacement of the allocation set.
        updated = save_record(conn, scope['kind'], scope, user['id'], record_id=scope_id, expected_version=data['version'])
        audit(conn, user, '设置律师分配', 'allocations', scope_id, encode(normalized))
        return {'items': calculated, 'version': updated['version']}


@app.post('/api/cases/{case_id}/conflicts')
def run_conflict(case_id: str, data: dict, user=Depends(current_user)):
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        case = get_record(conn, case_id, 'cases')
        check_access(conn, user, {'kind': 'conflicts', 'case_id': case_id}, write=True)
        terms, matches, truncated = matching_conflicts(conn, case, str(data.get('query', '')))
        for hit in matches:
            other = get_record(conn, hit['case_id']) if hit.get('case_id') else None
            if other and visible_case(conn, user, other):
                hit['case_number'], hit['case_title'] = other['number'], other['title']
            else:
                hit['case_id'] = None
                hit['record_id'] = None
                hit['case_title'] = '受限案件，请合伙人复核'
        item = save_record(conn, 'conflicts', {'title': ' / '.join(terms), 'terms': terms, 'matches': matches,
            'truncated': truncated, 'status': 'pending', 'fingerprint': conflict_identity(conn, case),
            'notes': str(data.get('notes', '')), 'history': [], 'applicant_id': user['id'], 'applicant_name': user['name']}, user['id'], case_id=case_id)
        audit(conn, user, '发起利冲审查', 'conflicts', item['id'], f'命中 {len(matches)} 条')
        return item


@app.post('/api/documents/upload', status_code=201)
async def upload_document(case_id: str = Form(...), category: str = Form('其他'), title: str = Form(''),
                          document_id: str = Form(''), version: int = Form(0), file: UploadFile = File(...), user=Depends(current_user)):
    # Check permissions before consuming/storing an upload.
    with db() as conn:
        case = get_record(conn, case_id, 'cases')
        check_access(conn, user, {'kind': 'documents', 'case_id': case_id}, write=True)
        if not case:
            raise HTTPException(404, '案件不存在')
        old = get_record(conn, document_id, 'documents') if document_id else None
        if document_id and (not old or old['case_id'] != case_id):
            raise HTTPException(404, '文档不存在')
        if old:
            check_access(conn, user, old, write=True)
            if old['version'] != version:
                raise HTTPException(409, '文档已更新')
            if conn.execute("SELECT 1 FROM records WHERE deleted=0 AND kind IN ('contracts','seals') AND json_extract(data,'$.status') NOT IN ('draft','rejected','void') AND (json_extract(data,'$.document_id')=? OR json_extract(data,'$.archive_document_id')=?)", (document_id, document_id)).fetchone():
                raise HTTPException(409, '审批或归档中的文件不能替换；请另传文档')
    upload_dir = DATA / 'uploads'
    upload_dir.mkdir(exist_ok=True)
    blob_id = uid()
    destination = upload_dir / blob_id
    size, digest = 0, hashlib.sha256()
    try:
        with destination.open('xb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, '单文件上限为 50MB')
                digest.update(chunk)
                stream.write(chunk)
        if not size:
            raise HTTPException(422, '不能上传空文件')
        filename = Path((file.filename or 'document').replace('\\', '/')).name
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if old:
                latest = get_record(conn, old['id'])
                if latest['version'] != version:
                    raise HTTPException(409, '文档已更新，请重新上传')
                item = latest
            else:
                item = validate('documents', {'title': title or filename, 'category': category})
                item['history'] = []
            item['status'] = 'active'
            item.setdefault('files', []).append({'blob': blob_id, 'filename': filename, 'size': size, 'sha256': digest.hexdigest(), 'uploaded_at': now(), 'uploaded_by': user['name']})
            result = save_record(conn, 'documents', item, user['id'], case_id=case_id, record_id=document_id or None, expected_version=version if old else None)
            audit(conn, user, '上传文件版本', 'documents', result['id'], filename)
            return result
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


@app.get('/api/documents/{document_id}/download')
def download(document_id: str, file_version: int = Query(-1, ge=-1), user=Depends(current_user)):
    with db() as conn:
        doc = get_record(conn, document_id, 'documents')
        check_access(conn, user, doc)
        files = doc.get('files', [])
        if not files or file_version >= len(files):
            raise HTTPException(404, '历史附件尚未迁移实体文件')
        file = files[file_version]
        path = DATA / 'uploads' / file['blob']
        if not path.is_file():
            raise HTTPException(404, '文件实体缺失，请管理员核对')
        audit(conn, user, '下载文档', 'documents', document_id, file['filename'])
        return FileResponse(path, filename=file['filename'], media_type='application/octet-stream')


@app.get('/api/dashboard')
def dashboard(user=Depends(current_user)):
    from decimal import Decimal
    def projected_columns(fields):
        pairs = ','.join(f"'{field}',json_extract(r.data,'$.{field}')" for field in fields)
        return 'r.id,r.kind,r.case_id,r.version,r.source_key,r.created_at,r.updated_at,json_object(' + pairs + ') data'
    with db() as conn:
        where, args = visible_query(conn, user, 'cases')
        columns = projected_columns(['title','number','status','category','currency','amount'])
        rows = conn.execute('SELECT ' + columns + ' FROM records r WHERE ' + where, args).fetchall()
        stats = {'total': len(rows), 'active': 0, 'intake': 0, 'closed': 0, 'unresolved_finance': 0}
        by_currency, categories = {}, {}
        accessible = set()
        case_currencies = {}
        for row in rows:
            case = unpack(row)
            accessible.add(case['id'])
            status = case.get('status')
            stats['active'] += status == 'active'
            stats['intake'] += status in ('intake', 'draft', 'submitted', 'rejected')
            stats['closed'] += status in ('closed', 'archived')
            categories[case.get('category', '未分类')] = categories.get(case.get('category', '未分类'), 0) + 1
            currency = case.get('currency', 'CNY')
            case_currencies[case['id']] = currency
            sums = by_currency.setdefault(currency, {'contract': Decimal(0), 'received': Decimal(0), 'invoiced': Decimal(0)})
            if status in ('active', 'closed', 'archived'):
                stats['unresolved_finance'] += case.get('amount') is None
                sums['contract'] += Decimal(case.get('amount') or '0')
        pending, tasks, activity = [], [], []
        columns = projected_columns(['title','amount','currency','status','due_on','assignee_id','priority','type'])
        for row in conn.execute("SELECT " + columns + " FROM records r WHERE kind IN ('invoices','receipts','conflicts','seals','tasks') AND deleted=0 ORDER BY updated_at DESC"):
            item = unpack(row)
            if item.get('case_id') not in accessible:
                continue
            if item['kind'] in ('invoices', 'receipts'):
                stats['unresolved_finance'] += item.get('amount') is None or str(item.get('status','')).startswith('legacy:')
                currency = item.get('currency') or case_currencies[item['case_id']]
                by_currency.setdefault(currency, {'contract': Decimal(0), 'received': Decimal(0), 'invoiced': Decimal(0)})
                if item['kind'] == 'invoices' and item.get('status') == 'issued':
                    by_currency[currency]['invoiced'] += Decimal(item.get('amount') or '0')
                if item['kind'] == 'receipts' and item.get('status') == 'confirmed':
                    by_currency[currency]['received'] += Decimal(item.get('amount') or '0')
            if item.get('status') in ('submitted', 'pending'):
                pending.append({k: item.get(k) for k in ('id', 'title', 'kind', 'case_id', 'status', 'updated_at')})
            if item['kind'] == 'tasks' and item.get('status') == 'open':
                tasks.append(item)
        for row in rows:
            c = unpack(row)
            if c.get('status') == 'submitted':
                pending.append(c)
        stats['pending'] = len(pending)
        stats['overdue'] = sum(bool(t.get('due_on')) and t['due_on'] < date.today().isoformat() for t in tasks)
        for row in conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200'):
            item = dict(row)
            record = get_record(conn, item['record_id'])
            if user['role'] == 'admin' or record and (record['id'] in accessible or record.get('case_id') in accessible):
                activity.append(item)
            if len(activity) == 8:
                break
        funds = {c: {k: format(v, '.2f') for k, v in amounts.items()} for c, amounts in by_currency.items()}
        return {'stats': stats, 'funds': funds, 'categories': categories, 'pending': pending[:8],
                'tasks': sorted(tasks, key=lambda t: t.get('due_on') or '9999')[:8], 'activity': activity}


@app.get('/api/export/{kind}')
def export(kind: str, case_id: str | None = None, q: str = '', status: str | None = None, branch: str = '', user=Depends(current_user)):
    kind_check(kind)
    if kind == 'allocations':
        keys = ['lawyer_id', 'ratio', 'amount', 'scope_id']
    else:
        keys = [f['key'] for f in SCHEMAS[kind]['fields']]
    def safe_cell(value):
        text = str(value if value is not None else '')
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    with db() as conn:
        where, args = visible_query(conn, user, kind, q, case_id, status, branch)
        rows = conn.execute('SELECT r.* FROM ' + records_source(kind, branch) + ' WHERE ' + where, args).fetchall()
        references = References(conn) if kind in ('cases','lawyers') else None
        audit(conn, user, '导出 CSV', kind, detail=f'{len(rows)} 条')
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['id', 'case_id', 'status', *keys])
    for row in rows:
        item = unpack(row)
        if references: references.enrich(item)
        writer.writerow([safe_cell(item.get(k, '')) for k in ['id', 'case_id', 'status', *keys]])
    return Response('\ufeff' + stream.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{kind}.csv"'})


@app.get('/api/history/{record_id}')
def history(record_id: str, user=Depends(current_user)):
    with db() as conn:
        item = get_record(conn, record_id)
        check_access(conn, user, item)
        if item['kind'] == 'lawyers':
            require_role(user, 'admin')
        rows = conn.execute('SELECT version,data,actor,created_at FROM record_versions WHERE record_id=? ORDER BY version DESC', (record_id,))
        return [dict(r, data=json.loads(r['data'])) for r in rows]


@app.get('/api/legacy/{record_id}')
def legacy_details(record_id: str, table: str = '', page: int = Query(1, ge=1), user=Depends(current_user)):
    from migration.scope import CASE_KEYS
    with db() as conn:
        record = get_record(conn, record_id)
        check_access(conn, user, record)
        if not record.get('source_key'):
            return {'tables': [], 'items': [], 'total': 0}
        known = {r[0] for r in conn.execute('SELECT name FROM source_tables')}
        queries = {}
        raw = record.get('legacy_data', {})
        if record['kind'] == 'conflicts' and 't_conflict_search_det' in known:
            queries['t_conflict_search_det'] = ('SELECT * FROM src__t_conflict_search_det WHERE csd_search_id=?', [raw.get('csm_search_id')])
        elif record['kind'] == 'documents' and 't_doc_version' in known:
            queries['t_doc_version'] = ('SELECT * FROM src__t_doc_version WHERE dv_doc_id=?', [raw.get('do_doc_id')])
        elif record['kind'] == 'cases':
            require_role(user, 'admin', 'partner')
            for name, (office_column, case_column) in CASE_KEYS.items():
                if name in known and name != 't_case':
                    queries[name] = (f'SELECT * FROM "src__{name}" WHERE "{office_column}"=? AND "{case_column}"=?', [raw.get('ca_office_id'), raw.get('ca_case_id')])
        if not table:
            return {'tables': list(queries), 'items': [], 'total': 0}
        if table not in queries:
            raise HTTPException(404, '没有此关联明细')
        query, args = queries[table]
        count = conn.execute('SELECT COUNT(*) FROM (' + query + ')', args).fetchone()[0]
        rows = conn.execute(query + ' ORDER BY _migration_row_id LIMIT 20 OFFSET ?', args + [(page - 1) * 20]).fetchall()
        return {'tables': list(queries), 'items': [{k: v.hex() if isinstance(v, bytes) else v for k, v in dict(row).items()} for row in rows], 'total': count}


@app.get('/api/admin/migration-issues')
def migration_issues(page: int = Query(1, ge=1), user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='migration_issues'").fetchone():
            return {'items': [], 'total': 0, 'summary': []}
        rows = conn.execute('SELECT * FROM migration_issues WHERE resolved=0 ORDER BY id LIMIT 50 OFFSET ?', ((page - 1) * 50,))
        return {'items': [dict(r) for r in rows], 'total': conn.execute('SELECT COUNT(*) FROM migration_issues WHERE resolved=0').fetchone()[0],
                'summary': [dict(r) for r in conn.execute('SELECT code,COUNT(*) count FROM migration_issues WHERE resolved=0 GROUP BY code')]}


@app.get('/api/admin/users')
def users(user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        return [dict(public_user(r),needs_password_reset=r['password_hash']=='!',source_user_id=r['source_user_id'],migration_issue=r['issue'])
                for r in conn.execute('SELECT u.*,i.source_user_id,i.issue FROM users u LEFT JOIN imported_accounts i ON i.user_id=u.id ORDER BY u.created_at')]


@app.post('/api/admin/users')
def add_user(data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    if data.get('role') not in ROLES or not data.get('username') or not data.get('name'):
        raise HTTPException(422, '请填写账号、姓名及角色')
    hashed = '!' if data.get('reset_required') is True else safe_password(data.get('password'))
    with db() as conn:
        if data.get('role') == 'lawyer' and not get_record(conn, data.get('lawyer_id'), 'lawyers'):
            raise HTTPException(422, '律师账号必须关联律师档案')
        identifier = uid()
        conn.execute('INSERT INTO users(id,username,name,password_hash,role,lawyer_id,created_at) VALUES(?,?,?,?,?,?,?)',
                     (identifier, data['username'], data['name'], hashed, data['role'], data.get('lawyer_id'), now()))
        audit(conn, user, '创建账号', 'users', identifier)
    return {'id': identifier}


@app.patch('/api/admin/users/{user_id}')
def update_user(user_id: str, data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not old:
            raise HTTPException(404, '用户不存在')
        if user_id == user['id'] and (data.get('active') is False or data.get('role', 'admin') != 'admin'):
            raise HTTPException(422, '不能禁用或降级当前管理员账号')
        role = data.get('role', old['role'])
        if role not in ROLES:
            raise HTTPException(422, '无效角色')
        lawyer_id = data.get('lawyer_id', old['lawyer_id'])
        if role == 'lawyer' and not get_record(conn, lawyer_id, 'lawyers'):
            raise HTTPException(422, '律师账号须关联档案')
        hashed = safe_password(data['password']) if data.get('password') else old['password_hash']
        conn.execute('UPDATE users SET name=?,role=?,active=?,lawyer_id=?,password_hash=? WHERE id=?',
                     (data.get('name', old['name']), role, int(data.get('active', old['active'])), lawyer_id, hashed, user_id))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?',(user_id,))
        audit(conn, user, '修改账号权限', 'users', user_id)
    return {'ok': True}


def _page_bounds(page, page_size):
    return (page - 1) * page_size, min(page_size, 200)


@app.get('/api/admin/departments')
def admin_departments(q: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current_user)):
    require_role(user, 'admin')
    q = q.strip().lower()
    with db() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM managed_departments ORDER BY office,sort_order,code')]
        lawyers = []
        references = References(conn)
        for row in conn.execute("SELECT id,data FROM records WHERE kind='lawyers' AND deleted=0"):
            data = references.enrich(json.loads(row['data']))
            lawyers.append((row['id'], str(data.get('office') or '').strip(), str(data.get('department_code') or '').strip()))
        account_ids = {r['lawyer_id'] for r in conn.execute('SELECT lawyer_id FROM users WHERE lawyer_id IS NOT NULL')}
        for item in rows:
            references.enrich_department(item)
            matched = [x[0] for x in lawyers if x[1] == item['office'] and x[2] == item['code']]
            item['employee_count'] = len(matched)
            item['account_count'] = sum(x in account_ids for x in matched)
        if q:
            rows = [r for r in rows if q in ' '.join(str(r.get(k) or '').lower() for k in ('office','branch_code','branch_name','code','name'))]
        total = len(rows); start, size = _page_bounds(page, page_size)
        return {'items': rows[start:start + size], 'total': total, 'page': page, 'page_size': size}


@app.post('/api/admin/departments')
def add_department(data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    office, code, name = str(data.get('office','')).strip(), str(data.get('code','')).strip(), str(data.get('name','')).strip()
    if not office or not code or not name:
        raise HTTPException(422, '请填写分所、部门编码及名称')
    with db() as conn:
        identifier = uid(); timestamp = now()
        branch_code = str(data.get('branch_code') or '').strip()
        if branch_code and (office,branch_code) not in References(conn).branches:
            raise HTTPException(422, '分支机构不存在')
        conn.execute('''INSERT INTO managed_departments
          (id,office,code,name,active,sort_order,source_table,source_key,created_at,updated_at,branch_code)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (identifier, office, code, name, int(bool(data.get('active', True))), int(data.get('sort_order', 0) or 0), None, None, timestamp, timestamp,branch_code))
        audit(conn, user, '新增部门', 'departments', identifier, f'{office}/{code}')
        return dict(conn.execute('SELECT * FROM managed_departments WHERE id=?', (identifier,)).fetchone())


@app.patch('/api/admin/departments/{department_id}')
def update_department(department_id: str, data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        old = conn.execute('SELECT * FROM managed_departments WHERE id=?', (department_id,)).fetchone()
        if not old: raise HTTPException(404, '部门不存在')
        office, code = str(data.get('office', old['office'])).strip(), str(data.get('code', old['code'])).strip()
        name = str(data.get('name', old['name'])).strip()
        if not office or not code or not name: raise HTTPException(422, '部门编码和名称不能为空')
        if office != old['office'] or code != old['code']:
            raise HTTPException(422, '部门的分所和编码不可修改，请新增部门后调整成员归属')
        conn.execute('''UPDATE managed_departments SET office=?,code=?,name=?,active=?,sort_order=?,updated_at=? WHERE id=?''',
                     (office, code, name, int(bool(data.get('active', old['active']))), int(data.get('sort_order', old['sort_order']) or 0), now(), department_id))
        audit(conn, user, '修改部门', 'departments', department_id, f'{office}/{code}')
        return dict(conn.execute('SELECT * FROM managed_departments WHERE id=?', (department_id,)).fetchone())


@app.get('/api/admin/members')
def admin_members(q: str = '', department: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        references = References(conn)
        accounts = {}
        for r in conn.execute('SELECT u.*,i.issue FROM users u LEFT JOIN imported_accounts i ON i.user_id=u.id WHERE u.lawyer_id IS NOT NULL ORDER BY u.username'):
            accounts.setdefault(r['lawyer_id'],[]).append(dict(public_user(r),needs_password_reset=r['password_hash']=='!',migration_issue=r['issue'] or ''))
        dept = conn.execute('SELECT office,code FROM managed_departments WHERE id=?',(department,)).fetchone() if department else None
        rows = []
        for raw_row in conn.execute("SELECT * FROM records WHERE kind='lawyers' AND deleted=0 ORDER BY updated_at DESC"):
            item = unpack(raw_row); raw = item.get('legacy_data') or {}
            references.enrich(item)
            member_accounts = accounts.get(item['id'],[])
            item.update(employee_id=str(raw.get('emp_empl_id') or ''),
                        employee_status=str(raw.get('emp_status') or item.get('status') or ''), accounts=member_accounts, account=member_accounts[0] if member_accounts else None)
            haystack=' '.join(str(item.get(k) or '').lower() for k in ('name','employee_id','branch_name','branch_code','department','title','phone','email'))+' '+' '.join(a['username'].lower() for a in member_accounts)
            if q and q.strip().lower() not in haystack: continue
            if department and (not dept or (item.get('office'),item.get('department_code'))!=(dept['office'],dept['code'])): continue
            item.pop('legacy_data', None)
            rows.append(item)
        total = len(rows); start, size = _page_bounds(page, page_size)
        return {'items': rows[start:start + size], 'total': total, 'page': page, 'page_size': size}


@app.patch('/api/admin/members/{lawyer_id}')
def update_member(lawyer_id: str, data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        record = get_record(conn, lawyer_id, 'lawyers')
        if not record: raise HTTPException(404, '成员档案不存在')
        if data.get('version') != record['version']: raise HTTPException(409,'成员资料已更新，请刷新后重试')
        record['status'] = References(conn).enrich(dict(record))['status']
        payload = dict(record)
        for key in ('name','office','department','title','license','phone','email','specialty','status','notes'):
            if key in data: payload[key] = str(data[key] or '').strip()
        if not payload.get('name'): raise HTTPException(422,'成员姓名不能为空')
        if payload.get('status') not in ('active','inactive'): raise HTTPException(422,'成员状态无效')
        if data.get('department_id'):
            dept = conn.execute('SELECT * FROM managed_departments WHERE id=?', (data['department_id'],)).fetchone()
            if not dept: raise HTTPException(422, '部门不存在')
            if not dept['active'] and data['department_id']!=record.get('department_id'): raise HTTPException(422,'请选择启用的部门')
            payload.update(office=dept['office'], department=dept['name'], department_code=dept['code'],department_id=dept['id'])
        updated = save_record(conn, 'lawyers', payload, user['id'], record_id=lawyer_id, expected_version=record['version'], source_key=record.get('source_key'))
        account_data = data.get('account')
        if isinstance(account_data,dict):
            account = conn.execute('SELECT * FROM users WHERE id=? AND lawyer_id=?',(account_data.get('id'),lawyer_id)).fetchone()
            if not account: raise HTTPException(422,'账号不属于当前成员')
            role=account_data.get('role',account['role']); active=account_data.get('active',bool(account['active']))
            if role not in ROLES or not isinstance(active,bool): raise HTTPException(422,'账号角色或启用状态无效')
            if account['id']==user['id'] and (not active or role!='admin'): raise HTTPException(422,'不能禁用或降级当前管理员账号')
            if (role,int(active))!=(account['role'],account['active']):
                conn.execute('UPDATE users SET role=?,active=? WHERE id=?',(role,int(active),account['id']))
                conn.execute('DELETE FROM sessions WHERE user_id=?',(account['id'],))
                conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?',(account['id'],))
                audit(conn,user,'修改账号权限','users',account['id'])
        audit(conn, user, '修改成员档案', 'lawyers', lawyer_id)
        return updated


@app.get('/api/admin/metadata/categories')
def metadata_categories(user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        return [dict(r) for r in conn.execute('SELECT category,COUNT(*) count,SUM(active) active_count FROM managed_metadata GROUP BY category ORDER BY category')]


@app.get('/api/admin/metadata')
def admin_metadata(category: str = '', q: str = '', page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200), user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        conditions, args = [], []
        if category: conditions.append('category=?'); args.append(category)
        if q: conditions.append('(code LIKE ? OR name LIKE ? OR parent_code LIKE ?)'); args.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
        where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
        total = conn.execute('SELECT COUNT(*) FROM managed_metadata' + where, args).fetchone()[0]
        start, size = _page_bounds(page, page_size)
        rows = [dict(r) for r in conn.execute('SELECT * FROM managed_metadata' + where + ' ORDER BY category,sort_order,code LIMIT ? OFFSET ?', args + [size, start])]
        return {'items': rows, 'total': total, 'page': page, 'page_size': size}


@app.post('/api/admin/metadata')
def add_metadata(data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    category, code, name = str(data.get('category','')).strip(), str(data.get('code','')).strip(), str(data.get('name','')).strip()
    if not category or not code or not name: raise HTTPException(422, '请填写类别、编码及名称')
    with db() as conn:
        identifier, timestamp = uid(), now()
        conn.execute('''INSERT INTO managed_metadata
          (id,category,code,parent_code,name,source_table,source_key,active,sort_order,notes,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (identifier, category, code, data.get('parent_code') or None, name, None, None, int(bool(data.get('active', True))), int(data.get('sort_order', 0) or 0), data.get('notes') or None, timestamp, timestamp))
        audit(conn, user, '新增元数据', 'metadata', identifier, f'{category}/{code}')
        return dict(conn.execute('SELECT * FROM managed_metadata WHERE id=?', (identifier,)).fetchone())


@app.patch('/api/admin/metadata/{metadata_id}')
def update_metadata(metadata_id: str, data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        old = conn.execute('SELECT * FROM managed_metadata WHERE id=?', (metadata_id,)).fetchone()
        if not old: raise HTTPException(404, '元数据不存在')
        category, code, name = str(data.get('category', old['category'])).strip(), str(data.get('code', old['code'])).strip(), str(data.get('name', old['name'])).strip()
        if not category or not code or not name: raise HTTPException(422, '类别、编码及名称不能为空')
        if category != old['category'] or code != old['code']:
            raise HTTPException(422, '字典的类别和编码不可修改，请新增字典项')
        conn.execute('''UPDATE managed_metadata SET category=?,code=?,parent_code=?,name=?,active=?,sort_order=?,notes=?,updated_at=? WHERE id=?''',
                     (category, code, data.get('parent_code', old['parent_code']) or None, name, int(bool(data.get('active', old['active']))), int(data.get('sort_order', old['sort_order']) or 0), data.get('notes', old['notes']), now(), metadata_id))
        audit(conn, user, '修改元数据', 'metadata', metadata_id, f'{category}/{code}')
        return dict(conn.execute('SELECT * FROM managed_metadata WHERE id=?', (metadata_id,)).fetchone())


@app.get('/api/settings')
def settings(user=Depends(current_user)):
    with db() as conn:
        return {r['key']: json.loads(r['value']) for r in conn.execute("SELECT * FROM settings WHERE key='firm'")}


@app.put('/api/admin/settings')
def update_settings(data: dict, user=Depends(current_user)):
    require_role(user, 'admin')
    value = data.get('firm', {})
    if not value.get('name'):
        raise HTTPException(422, '律所名称不能为空')
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES('firm',?)", (encode(value),))
        audit(conn, user, '更新律所设置', 'settings')
    return {'ok': True}


@app.get('/api/admin/audit')
def audit_log(page: int = Query(1, ge=1), user=Depends(current_user)):
    require_role(user, 'admin', 'partner')
    with db() as conn:
        return {'items': [dict(r) for r in conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 50 OFFSET ?', ((page - 1) * 50,))], 'total': conn.execute('SELECT COUNT(*) FROM audit').fetchone()[0]}


@app.get('/api/admin/migrations')
def migrations(user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        supplemental=[dict(r,schema_json=json.loads(r['schema_json'])) for r in conn.execute('SELECT * FROM supplemental_tables ORDER BY name')] if conn.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental_tables'").fetchone() else []
        return {'runs': [dict(r, report=json.loads(r['report'])) for r in conn.execute('SELECT * FROM migration_runs ORDER BY created_at DESC')],
                'tables': [dict(r, schema_json=json.loads(r['schema_json'])) for r in conn.execute('SELECT * FROM source_tables ORDER BY name')],
                'supplemental_tables':supplemental}


@app.get('/api/admin/migration-plan')
def migration_plan(user=Depends(current_user)):
    require_role(user, 'admin')
    path = ROOT / 'migration' / 'jw_manifest.json'
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else {'tables': []}


@app.post('/api/admin/demo')
def load_demo(user=Depends(current_user)):
    require_role(user, 'admin')
    from .seed import seed_demo
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        seed_demo(conn, user)
    return {'ok': True}


@app.get('/api/admin/source/{table}')
def source_records(table: str, page: int = Query(1, ge=1), user=Depends(current_user)):
    require_role(user, 'admin')
    with db() as conn:
        meta = conn.execute('SELECT * FROM source_tables WHERE name=?', (table,)).fetchone()
        if not meta and conn.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental_tables'").fetchone():
            meta=conn.execute('SELECT * FROM supplemental_tables WHERE name=?',(table,)).fetchone()
        if not meta or not table.replace('_', '').isalnum():
            raise HTTPException(404, '源表未迁移')
        rows = conn.execute(f'SELECT * FROM "src__{table}" LIMIT 20 OFFSET ?', ((page - 1) * 20,)).fetchall()
        return {'items': [{k: (v.hex() if isinstance(v, bytes) else v) for k, v in dict(r).items()} for r in rows], 'total': meta['row_count']}


# Production: same-origin Vue build; API routes must be registered first.
app.include_router(mcp_router)
app.add_route('/mcp', mcp_gateway, methods=['GET','POST','DELETE','OPTIONS'])
app.mount('/mcp', mcp_gateway, name='mcp')
if (ROOT / 'dist').is_dir():
    app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
