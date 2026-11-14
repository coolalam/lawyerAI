"""Idempotent additions to a migrated database. Core source tables are never rewritten."""
import json
from collections import defaultdict
from backend.db import encode,now,save_record,audit
from migration.project import stable_id,case_id,text


def import_accounts(conn):
    employees={r['emp_empl_id']:dict(r) for r in conn.execute('SELECT * FROM src__t_employee')}
    reverse=defaultdict(set)
    for r in employees.values():
        if text(r.get('emp_user_id')):reverse[text(r['emp_user_id'])].add(r['emp_empl_id'])
    result={'created':0,'active':0,'disabled':0,'groups_skipped':0,'already_imported':0,'username_collision':0,'unlinked':0}
    for source in conn.execute('SELECT * FROM src__t_user'):
        r=dict(source);key=text(r['us_user_id'])
        if text(r['us_is_group'])=='1':result['groups_skipped']+=1;continue
        if conn.execute('SELECT 1 FROM imported_accounts WHERE source_user_id=?',(key,)).fetchone():
            result['already_imported']+=1;continue
        candidates=set(reverse.get(key,()))
        if text(r.get('us_empl_id')) in employees:candidates.add(text(r['us_empl_id']))
        # This source uses the same OA employee identifier as the login identifier.
        if key in employees:candidates.add(key)
        employee=employees[next(iter(candidates))] if len(candidates)==1 else None
        lawyer=stable_id('t_employee',[employee['emp_empl_id']]) if employee else None
        if lawyer and not conn.execute("SELECT 1 FROM records WHERE id=? AND kind='lawyers' AND deleted=0",(lawyer,)).fetchone():lawyer=None
        issue='' if lawyer else '员工编号未唯一匹配，账号停用待管理员核对'
        if not lawyer:result['unlinked']+=1
        active=int(bool(lawyer and text(r['us_status'])=='A' and text(employee['emp_status'])=='A' and not text(employee.get('emp_leave_date'))))
        username=key
        if conn.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
            result['username_collision']+=1
            username='legacy:'+key
            active=0;issue=(issue+'；' if issue else '')+'登录账号重名，已保留独立迁移账号待核对'
            if conn.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise ValueError('Reserved migrated username also exists')
        identifier=stable_id('t_user',[key])
        name=text(employee.get('emp_name')) if employee else text(r.get('us_user_name') or r.get('us_description')) or key
        conn.execute('INSERT INTO users(id,username,name,password_hash,role,lawyer_id,active,created_at) VALUES(?,?,?,?,?,?,?,?)',
                     (identifier,username,name,'!','lawyer',lawyer,active,now()))
        conn.execute('INSERT INTO imported_accounts VALUES(?,?,?,?,?)',(key,identifier,r['us_status'],employee['emp_empl_id'] if employee else None,issue))
        result['created']+=1;result['active' if active else 'disabled']+=1
    return result


def repair_conflicts(conn):
    # Resolve only unique same-office keys, with casefold for legacy MySQL collation.
    exact={};aliases=defaultdict(set)
    for r in conn.execute('SELECT ca_office_id,ca_case_id,ca_temp_id FROM src__t_case'):
        office,number=text(r[0]),text(r[1]);identifier=case_id(office,number)
        exact[(office,number)]=identifier
        for candidate in (number,text(r[2])):
            if candidate:aliases[(office.casefold(),candidate.casefold())].add(identifier)
    result={'linked':0,'unresolved':0,'embedded_snapshots':0}
    rows=conn.execute("SELECT id,data,version FROM records WHERE kind='conflicts' AND case_id IS NULL AND source_key IS NOT NULL AND deleted=0").fetchall()
    for row in rows:
        data=json.loads(row['data']);raw=data.get('legacy_data') or {}
        office,number=text(raw.get('csm_office_id')),text(raw.get('csm_case_id'))
        candidates=aliases.get((office.casefold(),number.casefold()),set()) if office and number else set()
        identifier=exact.get((office,number)) or (next(iter(candidates)) if len(candidates)==1 else None)
        if not identifier or not conn.execute("SELECT 1 FROM records WHERE id=? AND kind='cases' AND deleted=0",(identifier,)).fetchone():
            result['unresolved']+=1;continue
        data['case_link_basis']='same_office_unique_legacy_key'
        save_record(conn,'conflicts',data,'migration-repair',record_id=row['id'],expected_version=row['version'])
        conn.execute('UPDATE records SET case_id=? WHERE id=?',(identifier,row['id']))
        conn.execute("UPDATE migration_issues SET resolved=1 WHERE table_name='t_conflict_search_mstr' AND source_key=? AND code='unresolved_case'",(encode(data.get('legacy_key')),))
        result['linked']+=1
    for r in conn.execute("SELECT ca_office_id,ca_case_id,ca_csm1_result,ca_csm1_result0,ca_csm1_description,ca_conflictor,ca_check_date FROM src__t_case WHERE ca_csm1_result IS NOT NULL AND ca_csm1_result!=''"):
        identifier=case_id(r[0],r[1])
        if conn.execute("SELECT 1 FROM records WHERE case_id=? AND kind='conflicts' AND deleted=0 LIMIT 1",(identifier,)).fetchone():continue
        if not conn.execute("SELECT 1 FROM records WHERE id=? AND deleted=0",(identifier,)).fetchone():continue
        raw=dict(r)
        save_record(conn,'conflicts',{'title':'历史立案利冲审批摘要','status':'legacy:'+text(r['ca_csm1_result']),
            'matches':[],'history':[],'migration':True,'legacy_table':'t_case','legacy_key':[r[0],r[1]],
            'legacy_data':raw,'legacy_case_review':raw,'review_note':text(r['ca_csm1_description']),
            'notes':'来自案件原始利冲审批字段，源库未提供对应检索主记录。'},'migration-repair',
            case_id=identifier,record_id=stable_id('t_case_conflict_summary',[r[0],r[1]]),
            source_key='jw_db.t_case.conflict:'+encode([r[0],r[1]]))
        result['embedded_snapshots']+=1
    return result
