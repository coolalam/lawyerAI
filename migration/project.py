"""Lossless raw rows plus conservative business projections; ambiguity becomes an issue."""
import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from backend.db import encode, get_record, now, save_record
from migration.references import References


def text(value):
    return '' if value is None else str(value).strip()


def stable_id(table, key):
    return 'jw_' + hashlib.sha256(encode([table, key]).encode()).hexdigest()[:32]


def case_id(office, number):
    return stable_id('t_case', [text(office), text(number)])


def legacy_json(row):
    return {k: {'__binary_base64__': base64.b64encode(v).decode()} if isinstance(v, bytes) else v for k,v in dict(row).items() if k != '_migration_row_id'}


class Projector:
    def __init__(self, conn, rules=None):
        self.conn, self.rules = conn, rules or {}
        self.issue_count = 0
        self.projected = {}
        self.dictionary = {}
        self.references = References(conn)
        self.conn.execute('CREATE TABLE IF NOT EXISTS migration_issues (id INTEGER PRIMARY KEY, table_name TEXT, source_key TEXT, code TEXT, detail TEXT, resolved INTEGER DEFAULT 0)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_migration_issue_source ON migration_issues(table_name,source_key,resolved)')
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_allocation_scope ON records(case_id,json_extract(data,'$.scope_id')) WHERE kind='allocations'")
        if self.exists('t_general_code'):
            for row in self.rows('t_general_code'):
                self.dictionary[(text(row['gc_office_id']),text(row['gc_code_group']),text(row['gc_id']))] = text(row['gc_name'])

    def exists(self, table):
        return bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", ('src__'+table,)).fetchone())

    def rows(self, table):
        return self.conn.execute(f'SELECT * FROM "src__{table}" ORDER BY _migration_row_id')

    def issue(self, table, key, code, detail):
        self.issue_count += 1
        self.conn.execute('INSERT INTO migration_issues(table_name,source_key,code,detail) VALUES(?,?,?,?)',(table,encode(key),code,detail))

    def label(self, group, value, office='default'):
        return self.dictionary.get((text(office),group,text(value))) or self.dictionary.get(('default',group,text(value))) or text(value)

    def amount(self, table, key, value, optional=False, places=2):
        if value is None or text(value)=='':
            if not optional:
                self.issue(table,key,'missing_amount','金额为空；未替换为零')
            return None
        try:
            number=Decimal(text(value))
            if not number.is_finite() or number<0 or number.quantize(Decimal(1).scaleb(-places))!=number:
                raise ValueError()
            return format(number,f'.{places}f')
        except (InvalidOperation, ValueError):
            self.issue(table,key,'invalid_amount',f'金额/比例无法无损映射到 {places} 位小数：{value}')
            return None

    def status(self, table, key, source, mapping):
        result = self.rules.get('statuses',{}).get(table,{}).get(text(source),mapping.get(text(source)))
        if not result:
            self.issue(table,key,'unmapped_status',f'状态 {source!r} 需确认业务语义')
            return 'legacy:'+text(source)
        valid = {
            't_case': {'intake','draft','submitted','rejected','active','closed','archived','void'},
            't_invoice': {'draft','submitted','approved','issued','rejected','void'},
            't_payment': {'draft','confirmed','void'},
            'xz_seal_request': {'draft','submitted','approved','stamped','collected','rejected','void'},
            't_conflict_search_mstr': {'pending','cleared','waived','blocked'},
        }
        if result not in valid.get(table,{result}):
            raise ValueError(f'无效状态映射：{table} {source} -> {result}')
        return result

    def linked_case(self, table, key, office, number):
        identifier=case_id(office,number)
        if self.conn.execute("SELECT 1 FROM records WHERE id=? AND kind='cases'",(identifier,)).fetchone():
            return identifier
        self.issue(table,key,'unresolved_case',f'未找到复合键案件：分所={office!r}, 案号={number!r}')
        return None

    def save(self, table, key, kind, row, data, case=None):
        source='jw_db.'+table+':'+encode(key)
        identifier=stable_id(table,key)
        data.update(legacy_data=legacy_json(row),legacy_table=table,legacy_key=key,history=[],migration=True)
        data['migration_issue_count'] = self.conn.execute('SELECT COUNT(*) FROM migration_issues WHERE table_name=? AND source_key=? AND resolved=0',(table,encode(key))).fetchone()[0]
        save_record(self.conn,kind,data,'migration',case_id=case,record_id=identifier,source_key=source)
        time_fields = {
            't_case': ('ca_create_date','ca_modify_date'), 't_employee': ('emp_create_date','emp_modify_date'),
            't_invoice': ('iv_create_date','iv_modify_date'), 't_payment': ('pay_create_date','pay_create_date'),
            't_document': ('do_create_date','do_last_edit_date'), 't_document_contract': ('do_create_date','do_last_edit_date'),
            't_conflict_search_mstr': ('csm_search_date','csm_search_date'), 'xz_seal_request': ('xzsr_create_date','xzsr_create_date'),
            't_payment_sharer': ('ps_create_date','ps_create_date'),
        }
        if table in time_fields:
            created_field, modified_field = time_fields[table]
            created, modified = text(row.get(created_field)), text(row.get(modified_field))
            # Preserve source dates when parseable; zero-dates remain in the raw layer.
            from datetime import datetime
            try:
                if created:
                    datetime.fromisoformat(created)
                    self.conn.execute('UPDATE records SET created_at=? WHERE id=?',(created,identifier))
                if modified:
                    datetime.fromisoformat(modified)
                    self.conn.execute('UPDATE records SET updated_at=? WHERE id=?',(modified,identifier))
            except ValueError:
                self.issue(table,key,'invalid_source_date','源日期无法解析；原值已保留')
        self.projected[table]=self.projected.get(table,0)+1
        return identifier

    def run(self):
        for table, handler in [
            ('t_employee',self.lawyers),('t_case',self.cases),('t_case_client_relation_info',self.parties),
            ('t_case_pay_detail',self.plans),('t_invoice',self.invoices),('t_payment',self.receipts),
            ('t_case_lawer_ref',self.case_shares),('t_invoice_share_set',self.invoice_shares),
            ('t_payment_sharer',self.payment_shares),('t_document',self.documents),
            ('xz_seal_request',self.seals),('t_conflict_search_mstr',self.conflicts),('t_caseend',self.closures),
        ]:
            if self.exists(table):
                count = 0
                for raw in self.rows(table):
                    handler(dict(raw))
                    count += 1
                    if count % 10000 == 0:
                        print(f'Projection {table}: {count} rows', flush=True)
                print(f'Projection {table}: {count} rows complete', flush=True)
        # Legacy allocations are never silently rescaled to 100%.
        groups=self.conn.execute("SELECT case_id,json_extract(data,'$.scope_id') scope FROM records WHERE kind='allocations' AND deleted=0 GROUP BY case_id,scope")
        for group in groups.fetchall():
            ratios=[json.loads(r[0]).get('ratio') for r in self.conn.execute("SELECT data FROM records WHERE kind='allocations' AND case_id IS ? AND json_extract(data,'$.scope_id') IS ?",(group['case_id'],group['scope']))]
            if any(x is None for x in ratios) or sum(Decimal(x or '0') for x in ratios)!=100:
                self.issue('allocations',group['scope'],'allocation_total','原始分配比例不等于 100%，原样保存，需人工核对')
        return {'projected':self.projected,'issue_count':self.issue_count}

    def lawyers(self,r):
        key=[r['emp_empl_id']]
        self.save('t_employee',key,'lawyers',r,{'name':text(r['emp_name']), 'office':text(r['emp_office_id']),
            'department':self.references.department(r.get('emp_office_id'),r.get('emp_dept_id')), 'title':text(r.get('emp_duty')), 'phone':text(r.get('emp_phone')),
            'email':text(r.get('emp_email')), 'license':text(r.get('emp_license_no')), 'specialty':text(r.get('emp_zy')),
            'status':'inactive' if r.get('emp_leave_date') or text(r.get('emp_status'))=='X' else 'active', 'legacy_status':r.get('emp_status')})

    def cases(self,r):
        key=[text(r['ca_office_id']),text(r['ca_case_id'])]
        client=''
        if self.exists('t_client'):
            row=self.conn.execute('SELECT cl_client_name FROM src__t_client WHERE cl_office_id=? AND cl_client_id=?',(r['ca_office_id'],r.get('ca_client_id'))).fetchone()
            client=text(row[0]) if row else ''
        if not client:
            self.issue('t_case',key,'unresolved_client','源客户复合键未匹配；未用任意同名客户替代')
        lawyer=stable_id('t_employee',[r.get('ca_manager')])
        if not get_record(self.conn,lawyer,'lawyers'):
            self.issue('t_case',key,'unresolved_lawyer','牵头合伙人员工编号未找到');lawyer=''
        self.save('t_case',key,'cases',r,{'number':text(r['ca_case_id']),'office':text(r['ca_office_id']),
            'title':text(r.get('ca_case_name')),'client':client,'opponent':text(r.get('ca_side_power')),
            'lead_lawyer_id':lawyer,'department':self.references.department(r.get('ca_office_id'),r.get('ca_dept_id')),
            'category':self.label('CACT',r.get('ca_category')),'stage':text(r.get('ca_stage')),
            'opened_on':text(r.get('ca_case_date'))[:10], 'description':text(r.get('ca_description')),
            'cause':self.references.cause(r), 'mandate':text(r.get('ca_submit_thing')),
            'representing':self.label('CAWS',r.get('ca_which_side')), 'court':text(r.get('ca_court')),
            'judge':text(r.get('ca_court_penson')), 'court_number':text(r.get('ca_other_no')),
            'amount':self.amount('t_case',key,r.get('ca_amount')), 'target_amount':self.amount('t_case',key,r.get('ca_Target'),True),
            'currency':{'RMB':'CNY'}.get(text(r.get('ca_currency')),text(r.get('ca_currency')) or 'UNKNOWN'),
            'fee_method':text(r.get('ca_pay_style')), 'confidential':text(r.get('ca_is_secret')).upper() in ('1','Y','T'),
            'notes':text(r.get('ca_memo')), 'start_on':text(r.get('ca_start_date'))[:10], 'end_on':text(r.get('ca_end_date'))[:10],
            'status':self.status('t_case',key,r.get('ca_status'),{'A':'active','E':'archived','Z':'closed','B':'intake','F':'intake','V':'draft','H':'draft','I':'draft','S':'submitted','R':'rejected','U':'void','X':'void','M':'void'}),
            'legacy_status_label':self.label('CA_ZT',r.get('ca_status'))})

    def parties(self,r):
        key=[r['ccri_office_id'],r['ccri_line'],r['ccri_case_id']]
        self.save('t_case_client_relation_info',key,'parties',r,{'name':text(r.get('ccri_name')),
            'role':self.label('CACL',r.get('ccri_type')), 'aliases':text(r.get('ccri_enname')),'identifier':text(r.get('ccri_no')),
            'contact':text(r.get('ccri_linker')),'phone':text(r.get('ccri_phone')),'email':text(r.get('ccri_email')),
            'address':text(r.get('ccri_addr')),'notes':text(r.get('ccri_memo')),'status':'active'},
            self.linked_case('t_case_client_relation_info',key,r['ccri_office_id'],r['ccri_case_id']))

    def plans(self,r):
        key=[r['cpr_office_id'],r['cpr_case_id'],r['cpr_line']]
        self.save('t_case_pay_detail',key,'payment_plans',r,{'title':text(r.get('cpr_description')) or f"收款节点 {r['cpr_line']}",
            'amount':self.amount('t_case_pay_detail',key,r.get('cpr_pay_amount')),'due_on':text(r.get('cpr_pay_date'))[:10],
            'condition':text(r.get('cpr_memo')),'status':'active','legacy_received_amount':r.get('cpr_arrive_amount')},
            self.linked_case('t_case_pay_detail',key,r['cpr_office_id'],r['cpr_case_id']))

    def invoices(self,r):
        key=[r['iv_index']]
        self.save('t_invoice',key,'invoices',r,{'title':text(r.get('iv_title')),'number':text(r.get('iv_id')),
            'tax_id':text(r.get('iv_tax_no')),'amount':self.amount('t_invoice',key,r.get('iv_amount')),
            'tax_amount':self.amount('t_invoice',key,r.get('iv_tax_amount'),True), 'legacy_tax_ratio':r.get('iv_tax_ratio'),
            'type':text(r.get('iv_type')), 'issued_on':text(r.get('iv_date'))[:10], 'bank_account':text(r.get('iv_bank_no')),
            'address':text(r.get('iv_address')), 'phone':text(r.get('iv_phone')), 'notes':text(r.get('iv_remarks')),
            'currency':{'RMB':'CNY'}.get(text(r.get('iv_currency')),text(r.get('iv_currency')) or 'UNKNOWN'),
            'status':self.status('t_invoice',key,text(r.get('iv_status'))+'|'+text(r.get('iv_app_status')),{}),
            'legacy_status_label':'原状态 '+text(r.get('iv_status'))+'/'+text(r.get('iv_app_status'))},
            self.linked_case('t_invoice',key,r['iv_office_id'],r['iv_case_id']))

    def receipts(self,r):
        key=[r['pay_id']];linked=self.linked_case('t_payment',key,r['pay_office_id'],r['pay_case_id'])
        invoice_id=''
        if r.get('pay_iv_id') and self.exists('t_invoice'):
            matches=self.conn.execute('SELECT iv_index FROM src__t_invoice WHERE iv_id=? AND iv_office_id=? AND iv_case_id=?',(r['pay_iv_id'],r['pay_office_id'],r['pay_case_id'])).fetchall()
            if len(matches)==1:invoice_id=stable_id('t_invoice',[matches[0][0]])
            else:self.issue('t_payment',key,'ambiguous_invoice','发票号关联不唯一或不存在，未自动绑定')
        self.save('t_payment',key,'receipts',r,{'title':text(r.get('pay_remarks')) or '历史到账',
            'amount':self.amount('t_payment',key,r.get('pay_amount')), 'received_on':text(r.get('pay_date'))[:10],
            'payer':text(r.get('pay_payor')), 'method':text(r.get('pay_style')), 'reference':text(r.get('pay_checkID')),
            'invoice_id':invoice_id, 'lawyer_fee':self.amount('t_payment',key,r.get('pay_lawyerFee'),True),
            'case_fee':self.amount('t_payment',key,r.get('pay_caseFee'),True), 'notes':text(r.get('pay_remarks')),
            'currency':{'RMB':'CNY'}.get(text(r.get('pay_currency')),text(r.get('pay_currency')) or 'UNKNOWN'),
            'status':self.status('t_payment',key,r.get('pay_status'),{})},linked)

    def case_shares(self,r):
        key=[r['cld_office_id'],r['cld_case_id'],r['cld_empl_id'],r['cld_task_type']]
        case=self.linked_case('t_case_lawer_ref',key,r['cld_office_id'],r['cld_case_id'])
        self.save('t_case_lawer_ref',key,'allocations',r,{'scope_id':case,'lawyer_id':stable_id('t_employee',[r['cld_empl_id']]),
            'role':text(r['cld_task_type']),'ratio':self.amount('t_case_lawer_ref',key,r.get('cld_alloc_ratio'),True,4),
            'amount':self.amount('t_case_lawer_ref',key,r.get('cld_alloc_amt'),True),'status':'active'},case)

    def invoice_shares(self,r):
        key=[r['ivss_id']];scope=stable_id('t_invoice',[r.get('ivss_iv_id')]);invoice=get_record(self.conn,scope,'invoices')
        if not invoice:self.issue('t_invoice_share_set',key,'unresolved_invoice','ivss_iv_id 无法匹配发票索引')
        elif text(invoice.get('legacy_data',{}).get('iv_office_id'))!=text(r.get('ivss_office_id')):
            self.issue('t_invoice_share_set',key,'office_mismatch','发票分配所属分所不一致');invoice=None
        self.save('t_invoice_share_set',key,'allocations',r,{'scope_id':scope if invoice else None,
            'lawyer_id':stable_id('t_employee',[r.get('ivss_empl_id')]),'role':'发票分配',
            'ratio':self.amount('t_invoice_share_set',key,r.get('ivss_ratio'),True,4),
            'amount':self.amount('t_invoice_share_set',key,r.get('ivss_amt'),True),'status':'active'},invoice.get('case_id') if invoice else None)

    def payment_shares(self,r):
        key=[r['ps_id']];scope=stable_id('t_payment',[r.get('ps_pay_id')]);receipt=get_record(self.conn,scope,'receipts')
        if not receipt:self.issue('t_payment_sharer',key,'unresolved_payment','ps_pay_id 无法匹配到账主记录')
        elif text(receipt.get('legacy_data',{}).get('pay_office_id'))!=text(r.get('ps_office_id')):
            self.issue('t_payment_sharer',key,'office_mismatch','到账分配所属分所不一致');receipt=None
        case=self.linked_case('t_payment_sharer',key,r.get('ps_office_id'),r.get('ps_case_id'))
        self.save('t_payment_sharer',key,'allocations',r,{'scope_id':scope if receipt else None,
            'lawyer_id':stable_id('t_employee',[r.get('ps_empl_id')]),'role':'到账分配',
            'ratio':self.amount('t_payment_sharer',key,r.get('ps_ratio'),True,4),
            'amount':self.amount('t_payment_sharer',key,r.get('ps_amount'),True),'status':'active'},case)

    def documents(self,r):
        key=[r['do_doc_id']]
        self.issue('t_document',key,'attachment_pending','保留原文件路径、偏移及长度；文件实体待单独迁移')
        linked_case=self.linked_case('t_document',key,r.get('do_office_id'),r.get('do_relate_case'))
        document_id=self.save('t_document',key,'documents',r,{'title':text(r.get('do_title')),'category':text(r.get('do_doc_class')),
            'description':text(r.get('do_brief')), 'confidential':text(r.get('do_is_private'))=='1',
            'status':'missing','files':[], 'source_path':r.get('do_file_path'), 'source_offset':r.get('do_file_start'),
            'source_length':r.get('do_file_length')},linked_case)
        if text(r.get('do_is_hetong')).upper() in ('1','Y','T'):
            self.issue('t_document_contract',key,'contract_status_pending','源合同标记已保留，合同用印与归档状态待确认')
            self.save('t_document_contract',key,'contracts',r,{'title':text(r.get('do_title')), 'number':text(r['do_doc_id']),
                'document_id':document_id, 'status':'legacy:unknown','legacy_status_label':'历史合同待核对'},linked_case)

    def seals(self,r):
        key=[r['xzsr_id']];case=None
        explicit=self.rules.get('seal_case_links',{}).get(text(r['xzsr_id']))
        if explicit and len(explicit)==2:case=self.linked_case('xz_seal_request',key,*explicit)
        else:self.issue('xz_seal_request',key,'seal_case_pending','用印表无案件外键；保留独立记录，等待明确关联')
        self.save('xz_seal_request',key,'seals',r,{'title':text(r.get('xzsr_content')) or text(r.get('xzsr_no')),
            'number':text(r.get('xzsr_no')),'recipient':text(r.get('xzsr_send_unit')), 'copies':int(r.get('xzsr_num') or 1),
            'reason':text(r.get('xzsr_reason')), 'notes':text(r.get('xzsr_memo')), 'applicant_name':text(r.get('xzsr_requestor')),
            'status':self.status('xz_seal_request',key,r.get('xzsr_status'),{'A':'submitted','B':'submitted','C':'submitted','D':'approved','E':'stamped','R':'rejected','S':'rejected','T':'rejected','U':'rejected'}),
            'legacy_status_label':self.label('xzstatus',r.get('xzsr_status'))},case)

    def conflicts(self,r):
        key=[r['csm_search_id']]
        self.save('t_conflict_search_mstr',key,'conflicts',r,{'title':text(r.get('csm_search_key')) or '历史利冲审查',
            'notes':text(r.get('csm_description')), 'review_note':text(r.get('csm_check_info')),
            'status':self.status('t_conflict_search_mstr',key,r.get('csm_status'),{}),
            'source_result':r.get('csm_result'), 'source_search_id':r['csm_search_id'],
            'matches':[], 'history_detail_table':'t_conflict_search_det'},
            self.linked_case('t_conflict_search_mstr',key,r.get('csm_office_id'),r.get('csm_case_id')))

    def closures(self,r):
        key=[r['cc_office_id'],r['cc_case_id']];identifier=self.linked_case('t_caseend',key,*key)
        if not identifier:return
        case=get_record(self.conn,identifier,'cases')
        case.update(result=text(r.get('cc_result')),closed_on=text(r.get('cc_date'))[:10],archive_number=text(r.get('cc_archive_id')),
                    archive_location=text(r.get('cc_archive_addr')),legacy_closure=legacy_json(r))
        save_record(self.conn,'cases',case,'migration',record_id=identifier)
        self.projected['t_caseend']=self.projected.get('t_caseend',0)+1
