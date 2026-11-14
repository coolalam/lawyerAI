"""Independently verify ALL scoped tables, content, issues, file versions and bytes."""
import argparse
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.migrate_mysql import verify_table, quote
from migration.project import legacy_json, stable_id


def verify_database(path,data_dir=None,records_only=False,progress=None):
    path=Path(path).resolve();data_dir=Path(data_dir).resolve() if data_dir else path.parent
    conn=sqlite3.connect(f'{path.as_uri()}?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        manifest=json.loads((ROOT/'migration/jw_manifest.json').read_text())
        schemas={t['name']:t for t in manifest['tables']}
        expected=set(schemas)
        has_source_tables=conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_tables'").fetchone()
        metadata=conn.execute('SELECT * FROM source_tables').fetchall() if has_source_tables else []
        actual={r['name'] for r in metadata}
        errors=[];tables=[];schema_errors=[]
        run=conn.execute('SELECT * FROM migration_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        migration_report=json.loads(run['report']) if run else {}
        report_tables={r['table']:r for r in migration_report.get('tables',[])}
        for row in metadata:
            meta=json.loads(row['schema_json'])
            try:
                count,checksum=verify_table(conn,row['name'],[c['name'] for c in meta['columns']])
                original=report_tables.get(row['name'],{})
                valid=(count==row['row_count']==original.get('source_count')==original.get('target_count')
                    and checksum==row['checksum']==original.get('sha256') and run and row['run_id']==run['id'])
                actual_columns=[c['name'] for c in conn.execute('PRAGMA table_info('+quote('src__'+row['name'])+')')]
                if meta!=schemas.get(row['name']) or actual_columns!=['_migration_row_id']+[c['name'] for c in meta['columns']]:
                    schema_errors.append(row['name'])
            except sqlite3.Error:
                count,valid=0,False
            tables.append({'table':row['name'],'count':count,'verified':bool(valid)})
            if not valid:errors.append(row['name'])
            if progress:progress(f"Verify {row['name']}: {count} rows, {'OK' if valid else 'FAILED'}")
        has_issues=conn.execute("SELECT 1 FROM sqlite_master WHERE name='migration_issues'").fetchone()
        issues=[dict(r) for r in conn.execute('SELECT code,COUNT(*) count FROM migration_issues WHERE resolved=0 GROUP BY code')] if has_issues else [{'code':'missing_projection_report','count':1}]
        attachment_pending_issue_count=next((int(item['count']) for item in issues if item['code']=='attachment_pending'),0)
        business_review_issues=[item for item in issues if item['code']!='attachment_pending']
        extraction_completed=bool(run and run['status'] in ('needs_review','verified'))
        decimal_errors=[]
        for row in metadata:
            schema=json.loads(row['schema_json'])
            decimal_columns=[column['name'] for column in schema.get('columns',[]) if str(column.get('type','')).lower().startswith('decimal')]
            expected_totals=report_tables.get(row['name'],{}).get('decimal_totals',{})
            for column in decimal_columns:
                target_sum=Decimal(0);nonnull=0
                try:
                    for value, in conn.execute('SELECT "'+column.replace('"','""')+'" FROM "src__'+row['name'].replace('"','""')+'"'):
                        if value is not None:
                            target_sum += Decimal(str(value));nonnull += 1
                except (sqlite3.Error, InvalidOperation, ValueError) as exc:
                    decimal_errors.append({'table':row['name'],'column':column,'error':str(exc)})
                    continue
                recorded=expected_totals.get(column,{}) if isinstance(expected_totals,dict) else {}
                computed=target_sum if nonnull else None
                try:
                    valid=isinstance(recorded,dict) and 'source' in recorded and 'target' in recorded
                    for side in ('source','target'):
                        value=recorded.get(side)
                        valid=valid and (Decimal(str(value)) if value is not None else None)==computed
                except (InvalidOperation,ValueError,TypeError):valid=False
                if not valid:
                    decimal_errors.append({'table':row['name'],'column':column,'error':'missing_or_mismatched_decimal_total'})
        file_errors=[];file_error_count=0;checked_files=0
        attachment_metadata_errors=[];documents_checked=0;documents_pending=0
        cache={}
        has_versions=conn.execute("SELECT 1 FROM sqlite_master WHERE name='src__t_doc_version'").fetchone()
        has_documents='t_document' in actual
        for row in conn.execute("SELECT id,data FROM records WHERE kind='documents' AND source_key IS NOT NULL AND deleted=0"):
            documents_checked += 1
            if progress and documents_checked % 10000 == 0:progress(f'Verify document metadata: {documents_checked} records')
            doc=json.loads(row['data']);files=doc.get('files',[])
            required_fields=('source_path','source_offset','source_length','legacy_data','legacy_table','legacy_key')
            missing_fields=[field for field in required_fields if field not in doc]
            metadata_reasons=['missing_'+field for field in missing_fields]
            source_id=doc.get('legacy_data',{}).get('do_doc_id')
            source=conn.execute('SELECT * FROM src__t_document WHERE do_doc_id=?',(source_id,)).fetchone() if has_documents else None
            if source is None:
                metadata_reasons.append('missing_source_document')
            else:
                if doc.get('legacy_data')!=legacy_json(source) or doc.get('legacy_table')!='t_document' or doc.get('legacy_key')!=[source_id] or row['id']!=stable_id('t_document',[source_id]):
                    metadata_reasons.append('source_metadata_mismatch')
                for target,original in [('source_path','do_file_path'),('source_offset','do_file_start'),('source_length','do_file_length')]:
                    if doc.get(target)!=source[original]:metadata_reasons.append(target+'_mismatch')
            if metadata_reasons:
                attachment_metadata_errors.append({'record_id':row['id'],'errors':metadata_reasons})
            expected_versions={str(v[0]) for v in conn.execute('SELECT dv_version FROM src__t_doc_version WHERE dv_doc_id=?',(source_id,))} if has_versions else set()
            actual_versions={str(f.get('source_version')) for f in files}
            reasons=[]
            if not files or not any(f.get('is_current') for f in files) or not expected_versions.issubset(actual_versions):
                documents_pending += 1
                reasons.append('missing_current_or_historical_versions')
            for file in files if not records_only else []:
                blob=str(file.get('blob',''))
                location=(data_dir/'uploads'/blob).resolve()
                if not location.is_relative_to((data_dir/'uploads').resolve()) or not location.is_file():
                    reasons.append('missing_file');continue
                if blob not in cache:
                    checksum=hashlib.sha256()
                    with location.open('rb') as stream:
                        while chunk:=stream.read(1024*1024):checksum.update(chunk)
                    cache[blob]=(location.stat().st_size,checksum.hexdigest());checked_files+=1
                if cache[blob]!=(file.get('size'),file.get('sha256')):reasons.append('file_checksum_mismatch')
            if reasons:
                file_error_count+=1
                if len(file_errors)<100:file_errors.append({'record_id':row['id'],'errors':sorted(set(reasons))})
        if records_only:
            file_errors=[];file_error_count=0;checked_files=0
        raw_document_count=conn.execute('SELECT COUNT(*) FROM src__t_document').fetchone()[0] if has_documents else 0
        projection_errors=[]
        if documents_checked!=raw_document_count:projection_errors.append('document_count_mismatch')
        if not migration_report.get('projection'):projection_errors.append('missing_projection_report')
        supplemental_tables=[];supplemental_errors=[]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='supplemental_tables'").fetchone():
            for row in conn.execute('SELECT * FROM supplemental_tables'):
                try:
                    columns=[c['name'] for c in json.loads(row['schema_json'])['columns']]
                    count,checksum=verify_table(conn,row['name'],columns)
                    valid=count==row['row_count'] and checksum==row['checksum']
                except (sqlite3.Error,ValueError,KeyError):count=0;valid=False
                supplemental_tables.append({'table':row['name'],'count':count,'verified':valid,'captured_at':row['captured_at'],'excluded_columns':json.loads(row['excluded_columns'])})
                if not valid:supplemental_errors.append(row['name'])
        if migration_report.get('source_manifest_sha256')!=hashlib.sha256((ROOT/'migration/jw_manifest.json').read_bytes()).hexdigest():schema_errors.append('manifest_checksum')
        record_accepted=bool(actual==expected and extraction_completed and not errors and not schema_errors and not decimal_errors and not attachment_metadata_errors and not projection_errors and not supplemental_errors and has_issues)
        business_accepted=record_accepted and not business_review_issues
        return {'tables':tables,'missing_tables':sorted(expected-actual),'unexpected_tables':sorted(actual-expected),
            'extraction_completed':extraction_completed,'checksum_errors':errors,'unresolved_issues':issues,
            'schema_errors':schema_errors,'projection_errors':projection_errors,
            'supplemental_tables':supplemental_tables,'supplemental_errors':supplemental_errors,
            'business_review_issues':business_review_issues,'attachment_pending_issue_count':attachment_pending_issue_count,
            'attachments_pending':documents_pending,'attachment_entities_checked':not records_only,
            'decimal_errors':decimal_errors,'documents_checked':documents_checked,'documents_pending':documents_pending,
            'attachment_metadata_error_count':len(attachment_metadata_errors),'attachment_metadata_errors':attachment_metadata_errors[:100],
            'checked_files':checked_files,'file_error_count':file_error_count,'file_errors_first_100':file_errors,
            'records_only':records_only,
            'record_migration_accepted':record_accepted,'business_projection_accepted':business_accepted,
            'accepted':record_accepted if records_only else business_accepted and not issues and not file_error_count}
    finally:conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database');parser.add_argument('--data-dir')
    parser.add_argument('--records-only',action='store_true',help='Verify database records and retained attachment locations; do not require attachment entities')
    parser.add_argument('--report',help='Write JSON acceptance report to a new file')
    args=parser.parse_args()
    report=verify_database(args.database,args.data_dir,args.records_only,lambda message:print(message,file=sys.stderr,flush=True))
    if args.report:
        with Path(args.report).open('x',encoding='utf8') as output:
            json.dump(report,output,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    sys.exit(0 if report['accepted'] else 2)


if __name__=='__main__':main()
