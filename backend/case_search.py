"""Compact, transactionally maintained case search and permission-aware bundles."""
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import db, get_record, unpack
from .schema import STATES
from .schema import allocation_role_label
from .security import check_access
from .service import finance_summary
from migration.references import References, text


class CaseQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    case_name: str | None = Field(None, description='案件名称，包含匹配', max_length=200)
    client_name: str | None = Field(None, description='客户名称，包含匹配', max_length=200)
    case_type: str | None = Field(None, description='案件类型名称或原始编码，精确匹配', max_length=200)
    lead_lawyer: str | None = Field(None, description='主办律师姓名（精确匹配，同名均匹配）或律师记录 ID', max_length=200)
    branch_name: str | None = Field(None, description='分支机构中文名称或编码，例如长沙或 CS；按案号前缀识别', max_length=200)
    opened_from: str | None = Field(None, description='收案日期起，YYYY-MM-DD，含当天', max_length=10)
    opened_to: str | None = Field(None, description='收案日期止，YYYY-MM-DD，含当天', max_length=10)
    opponent: str | None = Field(None, description='对方当事人名称，包含匹配', max_length=200)
    case_status: str | None = Field(None, description='案件状态中文名称、平台状态或源状态编码，精确匹配', max_length=200)
    case_number: str | None = Field(None, description='案号，包含匹配', max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1, le=50)

    @field_validator('case_name', 'client_name', 'case_type', 'lead_lawyer', 'branch_name', 'opened_from', 'opened_to', 'opponent', 'case_status', 'case_number')
    @classmethod
    def clean(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator('opened_from', 'opened_to')
    @classmethod
    def valid_date(cls, value):
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError('收案日期必须为 YYYY-MM-DD')
        return value

    @model_validator(mode='after')
    def filters_required(self):
        if not any(v for k, v in self.model_dump().items() if k not in ('page', 'page_size')):
            raise ValueError('至少提供一个有效查询条件，分页不算查询条件')
        if self.opened_from and self.opened_to and self.opened_from > self.opened_to:
            raise ValueError('收案日期起不能晚于止')
        return self


def init_case_search(conn):
    """No large legacy JSON scan per request. Triggers also cover migrations/deletion."""
    ready = conn.execute("SELECT 1 FROM settings WHERE key='case_search_v1_ready'").fetchone()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS case_search (
          id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
          number TEXT, title TEXT, client TEXT, category TEXT, office TEXT,
          opened_on TEXT, opponent TEXT, status TEXT, lead_lawyer_id TEXT,
          confidential INTEGER, source_category TEXT, source_status TEXT, source_area TEXT, updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_search_date ON case_search(opened_on);
        CREATE INDEX IF NOT EXISTS idx_search_lawyer ON case_search(lead_lawyer_id);
        CREATE INDEX IF NOT EXISTS idx_search_status ON case_search(status);
        CREATE INDEX IF NOT EXISTS idx_search_number ON case_search(number);
        CREATE INDEX IF NOT EXISTS idx_search_number_nocase ON case_search(number COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_search_updated ON case_search(updated_at DESC,id);
        CREATE INDEX IF NOT EXISTS idx_search_status_updated ON case_search(status,updated_at DESC,id);
    ''')
    if 'source_area' not in {row['name'] for row in conn.execute('PRAGMA table_info(case_search)') }:
        conn.execute('ALTER TABLE case_search ADD COLUMN source_area TEXT')
    fields = ('number','title','client','category','office','opened_on','opponent','status','lead_lawyer_id','confidential')
    def projection(alias):
        expressions = [f'{alias}.id'] + [f"json_extract({alias}.data,'$.{key}')" for key in fields]
        expressions += [f"json_extract({alias}.data,'$.legacy_data.ca_category')", f"json_extract({alias}.data,'$.legacy_data.ca_status')", f"json_extract({alias}.data,'$.legacy_data.ca_area')", f'{alias}.updated_at']
        return ','.join(expressions)
    conn.executescript(f'''
        CREATE TRIGGER IF NOT EXISTS case_search_insert AFTER INSERT ON records
        WHEN new.kind='cases' AND new.deleted=0 BEGIN
          INSERT OR REPLACE INTO case_search VALUES({projection('new')});
        END;
        CREATE TRIGGER IF NOT EXISTS case_search_update AFTER UPDATE OF data,kind,deleted,updated_at ON records
        WHEN old.kind='cases' OR new.kind='cases' BEGIN
          DELETE FROM case_search WHERE id=old.id;
          INSERT OR REPLACE INTO case_search SELECT {projection('new')} WHERE new.kind='cases' AND new.deleted=0;
        END;
    ''')
    if not ready:
        conn.execute('INSERT OR REPLACE INTO case_search SELECT '+projection('r')+" FROM records r WHERE kind='cases' AND deleted=0")
        conn.execute("INSERT OR REPLACE INTO settings VALUES('case_search_v1_ready','true')")


def matching_codes(conn, category, value):
    return {r['code'] for r in conn.execute('SELECT code FROM managed_metadata WHERE category=? AND (name=? OR code=?)', (category, value, value))}


def build_query(conn, user, filters, references):
    conditions, args = [], []
    # Every supplied field appends one AND clause; alternatives within a field are OR.
    for key, column in (('case_name','title'), ('client_name','client'), ('opponent','opponent')):
        value = getattr(filters, key)
        if value:
            conditions.append(f'instr(lower(c.{column}),lower(?))>0')
            args.append(value)
    if filters.case_number:
        conditions.append('instr(lower(c.number),lower(?))>0')
        args.append(filters.case_number)
    if filters.case_type:
        codes = sorted(matching_codes(conn, 'case_category', filters.case_type) | {filters.case_type})
        labels = [r['name'] for r in conn.execute('SELECT name FROM managed_metadata WHERE category=? AND code=?', ('case_category',filters.case_type))]
        parts = ['c.category=?'] * (1+len(labels)) + ['c.source_category=?'] * len(codes)
        conditions.append('('+' OR '.join(parts)+')'); args += [filters.case_type,*labels,*codes]
    if filters.case_status:
        codes = matching_codes(conn, 'case_status', filters.case_status) | {filters.case_status}
        codes |= {k for k,v in STATES['cases'].items() if v==filters.case_status}
        codes = sorted(codes)
        conditions.append('('+' OR '.join(['c.status=?','c.source_status=?']*len(codes))+')')
        args += [value for code in codes for value in (code,code)]
    if filters.lead_lawyer:
        conditions.append("c.lead_lawyer_id IN (SELECT id FROM records WHERE kind='lawyers' AND deleted=0 AND (id=? OR json_extract(data,'$.name')=?))")
        args += [filters.lead_lawyer,filters.lead_lawyer]
    if filters.branch_name:
        parts = [] ; branch_args = []
        parts.append('c.office=?'); branch_args.append(filters.branch_name)
        parts.append('c.source_area=?'); branch_args.append(filters.branch_name)
        for (office,code),name in references.branches.items():
            if filters.branch_name in (name,code) or filters.branch_name.upper()==code.upper():
                parts.append('(c.office=? AND c.source_area=?)')
                branch_args += [office,code]
                parts.append('substr(upper(c.number),1,?)=?')
                branch_args += [len(code),code.upper()]
        # The two confirmed prefixes also work in a fresh installation without source dictionaries.
        fallback = {'长沙':'CS','杭州':'HZ','CS':'CS','HZ':'HZ'}.get(filters.branch_name)
        if fallback:
            parts.append('substr(upper(c.number),1,2)=?'); branch_args.append(fallback)
        conditions.append('('+' OR '.join(parts)+')'); args += branch_args
    if filters.opened_from:
        conditions.append("c.opened_on<>'' AND c.opened_on>=?"); args.append(filters.opened_from)
    if filters.opened_to:
        conditions.append("c.opened_on<>'' AND substr(c.opened_on,1,10)<=?"); args.append(filters.opened_to)
    if user['role'] == 'lawyer':
        conditions.append("(c.lead_lawyer_id=? OR c.id IN (SELECT case_id FROM records WHERE kind='allocations' AND deleted=0 AND json_extract(data,'$.lawyer_id')=?))")
        args += [user.get('lawyer_id') or '',user.get('lawyer_id') or '']
    elif user['role'] not in ('admin','partner'):
        conditions.append('COALESCE(c.confidential,0)=0')
    return ' AND '.join(conditions), args


def client_details(conn, case, user, parties):
    raw = case.get('legacy_data') or {}
    result = {'name':case.get('client',''), 'source_records':[], 'parties':parties,
              'source_link_status':'not_applicable'}
    if case.get('legacy_table') != 't_case':
        return result
    result['source_link_status'] = 'not_found'
    if not raw.get('ca_client_id') or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='src__t_client'").fetchone():
        return result
    # Never join clients on name or client id alone across offices.
    for row in conn.execute('SELECT * FROM src__t_client WHERE cl_office_id=? AND cl_client_id=?', (raw.get('ca_office_id'),raw['ca_client_id'])):
        data = dict(row)
        if text(data.get('cl_is_secret')).upper() in ('1','Y','T') and user['role'] in ('finance','clerk'):
            result['source_link_status'] = 'restricted'
            continue
        result['source_records'].append({k:v.hex() if isinstance(v,bytes) else v for k,v in data.items()})
    if result['source_records']: result['source_link_status'] = 'matched'
    return result


def case_bundle(conn, case, user, references):
    check_access(conn,user,case)
    references.enrich(case)
    branch = next((name for (office,code),name in references.branches.items()
                   if office==case.get('office') and str(case.get('number','')).upper().startswith(code.upper())),None)
    if not branch:
        raw = case.get('legacy_data') or {}
        area = text(raw.get('ca_area'))
        branch = references.branches.get((case.get('office'),area)) or references.branches.get((case.get('office'),str(case.get('number',''))[:2].upper()))
    if not branch:
        branch = {'CS':'长沙','HZ':'杭州'}.get(str(case.get('number',''))[:2].upper(),case.get('office'))
    case['branch_name'] = branch
    groups = {kind:[] for kind in ('parties','invoices','receipts','payment_plans','allocations','seals','tasks','contracts')}
    for row in conn.execute("SELECT * FROM records WHERE case_id=? AND deleted=0 AND kind IN ('parties','invoices','receipts','payment_plans','allocations','seals','tasks','contracts') ORDER BY created_at,id", (case['id'],)):
        item = unpack(row)
        try: check_access(conn,user,item)
        except HTTPException as exc:
            if exc.status_code==403: continue
            raise
        if item['kind'] == 'allocations':
            item['role'] = allocation_role_label(item.get('role'))
        groups[item['kind']].append(item)
    lead = get_record(conn,case.get('lead_lawyer_id'), 'lawyers')
    if lead:
        references.enrich(lead)
        lead = {key:lead.get(key) for key in ('id','name','employee_id','office','branch_name','department','title','status')}
    return {'case':case,'lead_lawyer':lead,'client':client_details(conn,case,user,groups.pop('parties')),
            'finance':{'summary':finance_summary(conn,case),**{key:groups.pop(key) for key in ('invoices','receipts','payment_plans','allocations')}},
            **groups}


def query_cases(filters: CaseQuery, user):
    with db() as conn:
        conn.execute('BEGIN')  # Count, pages and all relations share one read snapshot.
        references = References(conn)
        where,args = build_query(conn,user,filters,references)
        total = conn.execute('SELECT COUNT(*) FROM case_search c WHERE '+where,args).fetchone()[0]
        ids = conn.execute('SELECT c.id FROM case_search c WHERE '+where+' ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?',args+[filters.page_size,(filters.page-1)*filters.page_size]).fetchall()
        items = [case_bundle(conn,get_record(conn,row['id'],'cases'),user,references) for row in ids]
        return {'items':items,'total':total,'page':filters.page,'page_size':filters.page_size,
                'has_more':filters.page*filters.page_size<total,
                'filters':filters.model_dump(exclude_none=True),
                'notes':['关联信息仅包含明确关联到案件且当前账号有权查看的记录；空列表不表示未关联的历史数据不存在。',
                         '附件仅返回已有位置或关联标识，不读取文件内容。']}
