import hashlib
import json
import sqlite3
from decimal import Decimal
from datetime import datetime
from backend import db as storage
from migration.project import Projector,case_id,stable_id
from scripts.migrate_mysql import source_value,digest_row,verify_table


def mirror(conn,table,rows):
    columns=list(rows[0])
    conn.execute(f'CREATE TABLE "src__{table}" (_migration_row_id INTEGER PRIMARY KEY,'+','.join(f'"{c}" TEXT' for c in columns)+')')
    for row in rows:
        conn.execute(f'INSERT INTO "src__{table}" ('+','.join(f'"{c}"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')',[source_value(row[c]) for c in columns])


def test_lossless_values_and_duplicate_rows():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE src__test (_migration_row_id INTEGER PRIMARY KEY, value TEXT, payload BLOB, missing TEXT)')
    values=[source_value(Decimal('9007199254740993.01')),b'\x00\xff\x01',None]
    conn.executemany('INSERT INTO src__test(value,payload,missing) VALUES(?,?,?)',[values,values])
    count,digest=verify_table(conn,'test',['value','payload','missing'])
    assert count==2 and digest==hashlib.sha256(digest_row(values)*2).hexdigest()
    assert conn.execute('SELECT value FROM src__test').fetchone()[0]=='9007199254740993.01'
    assert source_value(datetime(2026,11,12,12,30,10,123456))=='2026-11-12T12:30:10.123456'


def test_composite_case_keys_and_unknown_status_preserved(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'migration.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    storage.init_db()
    with storage.db() as conn:
        mirror(conn,'t_employee',[{'emp_empl_id':'007','emp_office_id':'SH','emp_name':'律师','emp_status':'A'}])
        mirror(conn,'t_client',[{'cl_client_id':'C1','cl_office_id':'SH','cl_client_name':'上海客户'},{'cl_client_id':'C1','cl_office_id':'BJ','cl_client_name':'北京客户'}])
        rows=[{'ca_case_id':'001','ca_office_id':office,'ca_client_id':'C1','ca_manager':'007','ca_case_name':office+' 案件','ca_amount':Decimal('9007199254740993.01'),'ca_currency':'RMB','ca_status':'A','custom_legacy_field':'不可丢弃'} for office in ['SH','BJ']]
        mirror(conn,'t_case',rows)
        mirror(conn,'t_invoice',[{'iv_index':'10','iv_office_id':'BJ','iv_case_id':'001','iv_status':'?','iv_app_status':'?','iv_amount':'123.45','iv_title':'北京发票','iv_id':'000001'}])
        report=Projector(conn).run()
        sh=storage.get_record(conn,case_id('SH','001'))
        bj=storage.get_record(conn,case_id('BJ','001'))
        assert sh['id']!=bj['id']
        assert sh['client']=='上海客户' and bj['client']=='北京客户'
        assert sh['amount']=='9007199254740993.01'
        assert sh['legacy_data']['custom_legacy_field']=='不可丢弃'
        invoice=storage.get_record(conn,stable_id('t_invoice',['10']))
        assert invoice['case_id']==bj['id']
        assert invoice['status']=='legacy:?|?'
        assert report['issue_count']>=1
        assert conn.execute("SELECT COUNT(*) FROM migration_issues WHERE code='unmapped_status'").fetchone()[0]==1


def test_projection_is_deterministic_and_orphans_survive(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'migration.sqlite3');monkeypatch.setattr(storage,'DATA',tmp_path);storage.init_db()
    with storage.db() as conn:
        mirror(conn,'t_payment',[{'pay_id':'88','pay_office_id':'missing','pay_case_id':'unknown','pay_amount':'12.34','pay_status':'Q'}])
        first=Projector(conn).run()
        second=Projector(conn).run()
        assert first['projected']==second['projected']
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='receipts'").fetchone()[0]==1
        record=storage.get_record(conn,stable_id('t_payment',['88']))
        assert record['case_id'] is None
        assert record['legacy_data']['pay_case_id']=='unknown'


def test_invalid_money_is_never_silently_converted(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'migration.sqlite3');monkeypatch.setattr(storage,'DATA',tmp_path);storage.init_db()
    with storage.db() as conn:
        p=Projector(conn)
        assert p.amount('t_case',['X'],'按小时结算') is None
        assert p.amount('t_invoice',['1'],'0.001') is None
        assert p.amount('t_invoice',['2'],'-10') is None
        assert p.issue_count==3


def test_attachment_byte_ranges_and_path_escape(tmp_path):
    import pytest
    from scripts.import_attachments import copy_range
    source=tmp_path/'source';source.mkdir()
    (source/'packed.bin').write_bytes(b'HEADER'+b'DOCUMENT'+b'TRAILER')
    result=copy_range(source,{'path':'packed.bin','offset':6,'length':8},tmp_path/'copied')
    assert (tmp_path/'copied').read_bytes()==b'DOCUMENT'
    assert result['sha256']==hashlib.sha256(b'DOCUMENT').hexdigest()
    with pytest.raises(ValueError):copy_range(source,{'path':'../copied'},tmp_path/'bad')
    with pytest.raises(ValueError):copy_range(source,{'path':'packed.bin','offset':100,'length':2},tmp_path/'bad')
    with pytest.raises(ValueError):copy_range(source,{'path':'packed.bin','sha256':'not-valid'},tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_actual_mirror_count_checksum_and_decimal_totals():
    from scripts.migrate_mysql import mirror_table
    rows=[('01',Decimal('9007199254740993.01'),b'\x00\xff',None),('02',Decimal('.09'),b'blob','')]
    class Cursor:
        def __init__(self):self.position=0
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):self.query=sql;self.position=0
        def fetchone(self):return (2,) if 'COUNT(*)' in self.query else (Decimal('9007199254740993.10'),)
        def fetchmany(self,n):
            batch=rows[self.position:self.position+n];self.position+=n;return batch
    class Source:
        def cursor(self,*args):return Cursor()
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE source_tables(name TEXT PRIMARY KEY,schema_json TEXT,row_count INTEGER,checksum TEXT,run_id TEXT)')
    entry={'name':'sample','columns':[{'name':'id','type':'varchar(20)','primary':True},{'name':'amount','type':'decimal(18,2)'},{'name':'file','type':'blob'},{'name':'nullable','type':'text'}]}
    report=mirror_table(Source(),conn,entry,'test-run')
    assert report['source_count']==report['target_count']==2
    assert report['decimal_totals']['amount']['source']=='9007199254740993.10'
    assert report['decimal_totals']['amount']['target']=='9007199254740993.10'
    assert conn.execute('SELECT nullable FROM src__sample ORDER BY id').fetchall()==[(None,),('',)]


def test_historical_contract_marker_and_source_dates_survive(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'migration.sqlite3');monkeypatch.setattr(storage,'DATA',tmp_path);storage.init_db()
    with storage.db() as conn:
        mirror(conn,'t_document',[{'do_doc_id':'D-01','do_title':'历史合同','do_office_id':'SH','do_relate_case':'A-01','do_is_hetong':'1','do_create_date':'2018-01-01T09:00:00'}])
        Projector(conn).run()
        document=storage.get_record(conn,stable_id('t_document',['D-01']))
        contract=storage.get_record(conn,stable_id('t_document_contract',['D-01']))
        assert document['created_at']=='2018-01-01T09:00:00'
        assert contract['document_id']==document['id']
        assert contract['status']=='legacy:unknown'


def test_verifier_rejects_incomplete_scope_and_missing_file(tmp_path,monkeypatch):
    from scripts.verify_migration import verify_database
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'partial.sqlite3');monkeypatch.setattr(storage,'DATA',tmp_path);storage.init_db()
    with storage.db() as conn:
        conn.execute('CREATE TABLE migration_issues(id INTEGER PRIMARY KEY,code TEXT,resolved INTEGER)')
        conn.execute("INSERT INTO migration_runs VALUES('r','jw_db','verified','{}','2026-11-12',NULL)")
        storage.save_record(conn,'documents',{'status':'active','files':[{'blob':'missing','filename':'missing.pdf','size':10,'sha256':'x','is_current':True}]},'migration',source_key='jw_db.t_document:test')
    report=verify_database(storage.DB_PATH,tmp_path)
    assert not report['accepted']
    assert len(report['missing_tables'])==60
    assert report['file_error_count']==1


def complete_record_snapshot(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts.migrate_mysql import quote
    from migration.project import legacy_json
    monkeypatch.setattr(storage,'DB_PATH',tmp_path/'complete.sqlite3')
    monkeypatch.setattr(storage,'DATA',tmp_path)
    storage.init_db()
    manifest_bytes=(Path(__file__).resolve().parents[1]/'migration/jw_manifest.json').read_bytes()
    manifest=json.loads(manifest_bytes)
    report={'tables':[], 'extraction_completed':True, 'projection':{'projected':{'t_document':1},'issue_count':2},
            'source_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest()}
    with storage.db() as conn:
        for entry in manifest['tables']:
            name=entry['name'];columns=entry['columns']
            conn.execute('CREATE TABLE '+quote('src__'+name)+' (_migration_row_id INTEGER PRIMARY KEY,'+','.join(quote(c['name'])+' TEXT' for c in columns)+')')
            if name=='t_document':
                conn.execute("INSERT INTO src__t_document(do_doc_id,do_file_path,do_file_start,do_file_length) VALUES('doc-1','original/path',NULL,'0')")
                conn.execute('CREATE INDEX test_doc_source ON src__t_document(do_doc_id)')
            if name=='t_invoice':conn.execute("INSERT INTO src__t_invoice(iv_amount) VALUES('9007199254740993.01')")
            count,checksum=verify_table(conn,name,[c['name'] for c in columns])
            conn.execute('INSERT INTO source_tables VALUES(?,?,?,?,?)',(name,storage.encode(entry),count,checksum,'r'))
            totals={c['name']:{'source':None,'target':None} for c in columns if c['type'].startswith('decimal')}
            if name=='t_invoice':totals['iv_amount']={'source':'9007199254740993.01','target':'9007199254740993.01'}
            report['tables'].append({'table':name,'source_count':count,'target_count':count,'sha256':checksum,'decimal_totals':totals})
        conn.execute('CREATE TABLE migration_issues(id INTEGER PRIMARY KEY,code TEXT,resolved INTEGER)')
        conn.executemany('INSERT INTO migration_issues(code,resolved) VALUES(?,0)',[('attachment_pending',),('unmapped_status',)])
        conn.execute('INSERT INTO migration_runs VALUES(?,?,?,?,?,NULL)',('r','jw_db','needs_review',storage.encode(report),'2026-11-13'))
        raw=conn.execute('SELECT * FROM src__t_document').fetchone()
        storage.save_record(conn,'documents',{'files':[],'status':'missing','source_path':'original/path',
            'source_offset':None,'source_length':'0','legacy_table':'t_document','legacy_key':['doc-1'],
            'legacy_data':legacy_json(raw)},'migration',record_id=stable_id('t_document',['doc-1']),source_key='jw_db.t_document:test')
    return storage.DB_PATH


def test_records_only_accepts_retained_locations_without_attachment_bytes(tmp_path,monkeypatch):
    from scripts.verify_migration import verify_database
    path=complete_record_snapshot(tmp_path,monkeypatch)
    record_report=verify_database(path,records_only=True)
    assert record_report['accepted'] and record_report['record_migration_accepted']
    assert not record_report['business_projection_accepted']
    assert record_report['attachments_pending']==record_report['attachment_pending_issue_count']==1
    assert not record_report['attachment_entities_checked']
    strict=verify_database(path)
    assert not strict['accepted'] and strict['file_error_count']==1


def test_records_only_rejects_lost_location_and_missing_document(tmp_path,monkeypatch):
    from scripts.verify_migration import verify_database
    path=complete_record_snapshot(tmp_path,monkeypatch)
    with storage.db() as conn:
        conn.execute("UPDATE records SET data=json_remove(data,'$.source_offset') WHERE kind='documents'")
    report=verify_database(path,records_only=True)
    assert not report['accepted'] and report['attachment_metadata_error_count']==1
    with storage.db() as conn:conn.execute("UPDATE records SET deleted=1 WHERE kind='documents'")
    report=verify_database(path,records_only=True)
    assert not report['accepted'] and 'document_count_mismatch' in report['projection_errors']


def test_records_only_rejects_corrupt_rows_and_source_decimal_totals(tmp_path,monkeypatch):
    from scripts.verify_migration import verify_database
    path=complete_record_snapshot(tmp_path,monkeypatch)
    with storage.db() as conn:
        report=json.loads(conn.execute('SELECT report FROM migration_runs').fetchone()[0])
        invoice=next(t for t in report['tables'] if t['table']=='t_invoice')
        invoice['decimal_totals']['iv_amount']['source']='9007199254740993.02'
        conn.execute('UPDATE migration_runs SET report=?',(storage.encode(report),))
        conn.execute("UPDATE src__t_document SET do_file_start='' WHERE do_doc_id='doc-1'")
    report=verify_database(path,records_only=True)
    assert not report['accepted']
    assert 't_document' in report['checksum_errors']
    assert report['decimal_errors'][0]['column']=='iv_amount'


def test_resume_rebuilds_from_complete_local_snapshot_only(tmp_path,monkeypatch):
    import pytest
    from scripts.resume_projection import resume
    from scripts.verify_migration import verify_database
    partial=complete_record_snapshot(tmp_path,monkeypatch)
    output=tmp_path/'resumed.sqlite3'
    with storage.db() as conn:
        conn.execute("UPDATE migration_runs SET status='failed'")
    with pytest.raises(ValueError,match='rolled back'):
        resume(partial,output)
    with storage.db() as conn:
        conn.execute('DELETE FROM record_versions')
        conn.execute('DELETE FROM records')
        conn.execute('DROP TABLE migration_issues')
    resume(partial,output)
    assert output.exists() and not partial.exists()
    report=verify_database(output,records_only=True)
    assert report['accepted'] and report['attachments_pending']==1
    assert output.with_suffix('.report.json').exists()
