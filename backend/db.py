import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('LAW_DATA_DIR', ROOT / 'data')).resolve()
DB_PATH = Path(os.environ.get('LAW_DB_PATH', DATA / 'office.sqlite3'))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL, lawyer_id TEXT,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mcp_tokens (
          id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
          user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS password_reset_requests (
          user_id TEXT PRIMARY KEY REFERENCES users(id), requested_at TEXT NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS imported_accounts (
          source_user_id TEXT PRIMARY KEY, user_id TEXT UNIQUE REFERENCES users(id),
          source_status TEXT, employee_id TEXT, issue TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, case_id TEXT REFERENCES records(id),
          data TEXT NOT NULL CHECK(json_valid(data)), version INTEGER NOT NULL DEFAULT 1,
          deleted INTEGER NOT NULL DEFAULT 0, source_key TEXT UNIQUE,
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind,deleted,updated_at);
        CREATE INDEX IF NOT EXISTS idx_records_case ON records(case_id,kind,deleted);
        CREATE INDEX IF NOT EXISTS idx_records_status ON records(kind,json_extract(data,'$.status'));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_case_number ON records(json_extract(data,'$.office'),json_extract(data,'$.number')) WHERE kind='cases' AND deleted=0;
        CREATE INDEX IF NOT EXISTS idx_case_branch ON records(substr(upper(json_extract(data,'$.number')),1,2),updated_at DESC,id) WHERE kind='cases' AND deleted=0;
        CREATE INDEX IF NOT EXISTS idx_allocations_lawyer ON records(json_extract(data,'$.lawyer_id'),case_id) WHERE kind='allocations' AND deleted=0;
        CREATE INDEX IF NOT EXISTS idx_allocation_scope ON records(case_id,json_extract(data,'$.scope_id')) WHERE kind='allocations';
        CREATE TABLE IF NOT EXISTS record_versions (
          id INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), version INTEGER NOT NULL,
          data TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(record_id,version)
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, action TEXT NOT NULL,
          kind TEXT NOT NULL, record_id TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_runs (
          id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL,
          created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS source_tables (
          name TEXT PRIMARY KEY, schema_json TEXT NOT NULL, row_count INTEGER NOT NULL,
          checksum TEXT NOT NULL, run_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS managed_departments (
          id TEXT PRIMARY KEY,
          office TEXT NOT NULL,
          code TEXT NOT NULL,
          name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          source_table TEXT,
          source_key TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(office, code)
        );
        CREATE TABLE IF NOT EXISTS managed_metadata (
          id TEXT PRIMARY KEY,
          category TEXT NOT NULL,
          code TEXT NOT NULL,
          parent_code TEXT,
          name TEXT NOT NULL,
          source_table TEXT,
          source_key TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(category, code)
        );
        ''')
        if 'branch_code' not in {r['name'] for r in conn.execute('PRAGMA table_info(managed_departments)')}:
            conn.execute('ALTER TABLE managed_departments ADD COLUMN branch_code TEXT')
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('firm',?)", (encode({'name': '易律网', 'subtitle': '专业有序 · 协作有方'}),))
        # Migrate the previous built-in brand while preserving any name set by
        # an administrator in the settings page.
        conn.execute("UPDATE settings SET value=? WHERE key='firm' AND json_extract(value,'$.name')='律序律师事务所'",
                     (encode({'name': '易律网', 'subtitle': '专业有序 · 协作有方'}),))
        ensure_managed_references(conn)
        from .case_search import init_case_search
        init_case_search(conn)


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ensure_managed_references(conn):
    """Seed editable reference overlays once, while keeping migrated source tables read-only."""
    timestamp = now()
    def order_number(value):
        try:
            return int(value or 0)
        except (ValueError, TypeError):
            return 0
    if _table_exists(conn, 'src__t_department'):
        for source in conn.execute('SELECT * FROM src__t_department').fetchall():
            row = dict(source)
            office, code, name, status, order = (row.get(k) for k in ('dp_office_id','dp_dept_id','dp_description','dp_status','dp_sort_order'))
            office, code = str(office or '').strip(), str(code or '').strip()
            if not code:
                continue
            conn.execute('''INSERT OR IGNORE INTO managed_departments
                (id,office,code,name,active,sort_order,source_table,source_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (uid(), office or 'default', code, str(name or code).strip(),
                 0 if str(status or '').upper() in ('0','N','X','I') else 1,
                 order_number(order),
                 't_department', f'{office}:{code}', timestamp, timestamp))
            conn.execute('UPDATE managed_departments SET branch_code=? WHERE office=? AND code=? AND branch_code IS NULL',
                         (str(row.get('dp_area') or '').strip(), office or 'default', code))
    def add(category, code, name, parent='', source='', source_key='', active=1, order=0, notes=''):
        code, name = str(code or '').strip(), str(name or '').strip()
        if not code or not name:
            return
        conn.execute('''INSERT OR IGNORE INTO managed_metadata
            (id,category,code,parent_code,name,source_table,source_key,active,sort_order,notes,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (uid(), category, code, str(parent or '').strip() or None, name, source or None,
             source_key or None, int(bool(active)), order_number(order), notes or None, timestamp, timestamp))
    if _table_exists(conn, 'src__t_case_class'):
        for source in conn.execute('SELECT * FROM src__t_case_class'):
            r = dict(source)
            add('case_cause', r.get('cc_case_class'), r.get('cc_class_name'), r.get('cc_super_class'), 't_case_class', str(r.get('cc_case_class')),
                str(r.get('cc_status') or '').upper() not in ('0','N','X'), r.get('cc_sort_order'))
    if _table_exists(conn, 'src__t_interface_reason'):
        for source in conn.execute('SELECT * FROM src__t_interface_reason'):
            r = dict(source)
            add('case_cause_interface', r.get('itr_code'), r.get('itr_name'), r.get('itr_parent_code'), 't_interface_reason', str(r.get('itr_id') or r.get('itr_code')), 1, r.get('ltr_level_code'),
                f"编码组：{r.get('itr_code_group') or '默认'}")
    if _table_exists(conn, 'src__t_general_code'):
        for source in conn.execute('SELECT * FROM src__t_general_code'):
            r = dict(source); group = str(r.get('gc_code_group') or '').strip()
            if not group: continue
            category = {'CA_ZT':'case_status','CACT':'case_category','CAWS':'case_side'}.get(group,f'code_{group.lower()}')
            add(category,r.get('gc_id'),r.get('gc_name'),'', 't_general_code', f"{group}:{r.get('gc_id')}",
                str(r.get('gc_status') or '').upper() not in ('0','N','X'),r.get('gc_sort_order'),f'编码组：{group}')
    # These are display labels; the workflow graph remains defined by schema.TRANSITIONS.
    from .schema import STATES
    for kind, category in {'cases':'case_status','lawyers':'employee_status','conflicts':'conflict_status',
                           'invoices':'invoice_status','receipts':'payment_status','seals':'seal_status',
                           'contracts':'contract_status','documents':'document_status','tasks':'task_status',
                           'payment_plans':'payment_plan_status'}.items():
        for code,name in STATES[kind].items():
            add(category,code,name,'','application',f'{category}:{code}')
    # Upgrade former built-in invoice labels without overwriting custom names.
    for code, old_name in {'draft': '草稿', 'submitted': '待审核', 'approved': '待开票',
                           'issued': '已开票', 'rejected': '已退回', 'void': '已作废'}.items():
        conn.execute("""UPDATE managed_metadata SET name=?,updated_at=?
            WHERE category='invoice_status' AND code=? AND name=? AND source_table='application'""",
            (STATES['invoices'][code], timestamp, code, old_name))

    for code, old_name in {'draft': '待核销', 'confirmed': '已确认'}.items():
        conn.execute("UPDATE managed_metadata SET name=?,updated_at=? WHERE category='payment_status' AND code=? AND name=? AND source_table='application'",
                     (STATES['receipts'][code], timestamp, code, old_name))


def unpack(row):
    if row is None:
        return None
    value = json.loads(row['data'])
    value.update(id=row['id'], kind=row['kind'], case_id=row['case_id'], version=row['version'],
                 created_at=row['created_at'], updated_at=row['updated_at'], source_key=row['source_key'])
    if row['kind'] == 'invoices':
        from .invoice_states import normalize_invoice
        normalize_invoice(value)
    elif row['kind'] == 'receipts':
        from .receipt_states import normalize_receipt
        normalize_receipt(value)
    return value


def get_record(conn, record_id, kind=None):
    row = conn.execute('SELECT * FROM records WHERE id=? AND deleted=0', (record_id,)).fetchone()
    if row and (not kind or row['kind'] == kind):
        return unpack(row)


META = {'id', 'kind', 'case_id', 'version', 'created_at', 'updated_at', 'source_key'}


def save_record(conn, kind, data, actor, case_id=None, record_id=None, expected_version=None, source_key=None):
    record_id = record_id or uid()
    existing = conn.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
    payload = encode({k: v for k, v in data.items() if k not in META})
    if existing:
        version = expected_version if expected_version is not None else existing['version']
        result = conn.execute('UPDATE records SET data=?,version=version+1,updated_at=? WHERE id=? AND version=? AND deleted=0',
                              (payload, now(), record_id, version))
        if result.rowcount != 1:
            raise ValueError('数据已被其他人修改，请刷新后重试')
        new_version = version + 1
    else:
        new_version = 1
        conn.execute('INSERT INTO records(id,kind,case_id,data,source_key,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                     (record_id, kind, case_id, payload, source_key, actor, now(), now()))
    conn.execute('INSERT INTO record_versions(record_id,version,data,actor,created_at) VALUES(?,?,?,?,?)',
                 (record_id, new_version, payload, actor, now()))
    return get_record(conn, record_id)


def audit(conn, user, action, kind, record_id='', detail=''):
    conn.execute('INSERT INTO audit(actor_id,actor_name,action,kind,record_id,detail,created_at) VALUES(?,?,?,?,?,?,?)',
                 (user['id'], user['name'], action, kind, record_id, detail, now()))
