"""jw_db -> a NEW SQLite database. Source is always read-only; no application DB overwrite."""
import argparse
import base64
import datetime
from decimal import Decimal
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pymysql
from backend import db as storage
from migration.project import Projector
from migration.scope import CASE_KEYS


def quote(name):
    if not re.fullmatch(r'[A-Za-z0-9_]+',name):
        raise ValueError(f'Invalid identifier: {name}')
    return '`'+name+'`'


def source_value(value):
    if value is None or isinstance(value,bytes):return value
    if isinstance(value,(datetime.date,datetime.datetime,datetime.time)):return value.isoformat()
    if isinstance(value,float):return repr(value)
    return str(value)


def digest_row(values):
    normalized=[{'binary':base64.b64encode(x).decode()} if isinstance(x,bytes) else x for x in values]
    return (json.dumps(normalized,ensure_ascii=False,separators=(',',':'))+'\n').encode()


def verify_table(conn,table,columns):
    checksum=hashlib.sha256();count=0
    query='SELECT '+','.join(quote(c) for c in columns)+' FROM '+quote('src__'+table)+' ORDER BY _migration_row_id'
    for row in conn.execute(query):
        checksum.update(digest_row(list(row)));count+=1
    return count,checksum.hexdigest()


def discover(conn,manifest,count=False):
    plan={'database':'jw_db','tables':[],'schema_errors':[],'non_transactional':[]}
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        for entry in manifest['tables']:
            table=entry['name']
            cur.execute('SELECT ENGINE,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',('jw_db',table))
            meta=cur.fetchone()
            if not meta:
                plan['schema_errors'].append({'table':table,'error':'missing_table'});continue
            cur.execute('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION',('jw_db',table))
            actual=cur.fetchall()
            expected=[(c['name'],c['type']) for c in entry['columns']]
            observed=[(c['COLUMN_NAME'],c['COLUMN_TYPE']) for c in actual]
            if expected!=observed:
                plan['schema_errors'].append({'table':table,'error':'schema_drift','expected':expected,'actual':observed})
            if meta['ENGINE']!='InnoDB':plan['non_transactional'].append(table)
            rows=meta['TABLE_ROWS']
            if count:
                cur.execute('SELECT COUNT(*) n FROM '+quote(table));rows=cur.fetchone()['n']
            plan['tables'].append({'name':table,'engine':meta['ENGINE'],'rows':rows,'exact_count':count})
    return plan


def mirror_table(source,target,entry,run_id):
    table=entry['name'];columns=[c['name'] for c in entry['columns']]
    binary=lambda c:any(x in c['type'].lower() for x in ('binary','blob','bit('))
    target.execute('CREATE TABLE '+quote('src__'+table)+' (_migration_row_id INTEGER PRIMARY KEY,'+
                   ','.join(quote(c['name'])+(' BLOB' if binary(c) else ' TEXT') for c in entry['columns'])+')')
    insert='INSERT INTO '+quote('src__'+table)+' ('+','.join(quote(c) for c in columns)+') VALUES('+','.join('?' for _ in columns)+')'
    primary=[c['name'] for c in entry['columns'] if c.get('primary')]
    order=' ORDER BY '+','.join(quote(c) for c in primary) if primary else ''
    checksum=hashlib.sha256();count=0
    decimal_columns=[c['name'] for c in entry['columns'] if c['type'].startswith('decimal')]
    sums={c:Decimal(0) for c in decimal_columns};nonnull={c:0 for c in decimal_columns}
    with source.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM '+quote(table));expected_count=cur.fetchone()[0]
        if decimal_columns:
            cur.execute('SELECT '+','.join('SUM('+quote(c)+')' for c in decimal_columns)+' FROM '+quote(table))
            expected_sums=dict(zip(decimal_columns,cur.fetchone()))
        else:expected_sums={}
    with source.cursor(pymysql.cursors.SSCursor) as cur:
        cur.execute('SELECT '+','.join(quote(c) for c in columns)+' FROM '+quote(table)+order)
        while batch:=cur.fetchmany(2000):
            values=[]
            for row in batch:
                converted=[source_value(v) for v in row]
                checksum.update(digest_row(converted));count+=1;values.append(converted)
                for key,value in zip(columns,row):
                    if key in sums and value is not None:
                        sums[key]+=Decimal(value);nonnull[key]+=1
            target.executemany(insert,values)
    local_count,local_hash=verify_table(target,table,columns)
    if count!=expected_count or local_count!=count or local_hash!=checksum.hexdigest():
        raise RuntimeError(f'{table}: count/checksum verification failed')
    for key,value in expected_sums.items():
        if value is not None and sums[key]!=Decimal(value):raise RuntimeError(f'{table}.{key}: decimal sum differs')
    if primary:
        target.execute('CREATE INDEX '+quote('idx_src_'+table)+' ON '+quote('src__'+table)+'('+','.join(quote(c) for c in primary)+')')
    if table in CASE_KEYS:
        office,number=CASE_KEYS[table]
        target.execute('CREATE INDEX '+quote('idx_link_'+table)+' ON '+quote('src__'+table)+'('+quote(office)+','+quote(number)+')')
    if table == 't_conflict_search_det':
        target.execute('CREATE INDEX idx_conflict_details ON src__t_conflict_search_det(csd_search_id)')
    target.execute('INSERT INTO source_tables VALUES(?,?,?,?,?)',(table,storage.encode(entry),count,local_hash,run_id))
    return {'table':table,'source_count':expected_count,'target_count':local_count,'sha256':local_hash,
            'decimal_totals':{c:{'source':str(expected_sums[c]) if expected_sums[c] is not None else None,
                                 'target':str(sums[c]) if nonnull[c] else None} for c in decimal_columns}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True);parser.add_argument('--port',type=int,default=3306);parser.add_argument('--user',required=True)
    parser.add_argument('--database',choices=['jw_db'],default='jw_db')
    parser.add_argument('--output',required=True,help='New SQLite path, or JSON report path with --plan')
    parser.add_argument('--plan',action='store_true',help='Read-only schema/engine check; no business data copied')
    parser.add_argument('--count',action='store_true',help='Exact counts in the plan')
    parser.add_argument('--rules',help='Reviewed JSON status and explicit seal/case mapping rules')
    args=parser.parse_args()
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise SystemExit('Output already exists; choose a new path. Existing databases are never overwritten.')
    manifest=json.loads((ROOT/'migration/jw_manifest.json').read_text())
    rules=json.loads(Path(args.rules).read_text()) if args.rules else {}
    password=os.environ.get('SOURCE_MYSQL_PASSWORD') or getpass.getpass('Source password: ')
    source=pymysql.connect(host=args.host,port=args.port,user=args.user,password=password,database=args.database,
        charset='utf8mb4',connect_timeout=10,read_timeout=300,autocommit=False)
    del password
    try:
        with source.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        plan=discover(source,manifest,args.count)
        if args.plan:
            output.write_text(json.dumps(plan,ensure_ascii=False,indent=2,default=str),encoding='utf8')
            print(json.dumps({'tables':len(plan['tables']),'schema_errors':len(plan['schema_errors']),'non_transactional':plan['non_transactional'],'report':str(output)},ensure_ascii=False))
            return
        if plan['schema_errors'] or plan['non_transactional']:
            raise SystemExit('Schema drift or non-InnoDB tables found. Use --plan and migrate from a consistent read-only snapshot after review.')
        staging=output.with_suffix(output.suffix+'.partial')
        if staging.exists():raise SystemExit('A partial output exists. Keep it for diagnosis and choose a new output path.')
        storage.DB_PATH=staging
        storage.init_db()
        run_id=storage.uid();report={'source':'jw_db','started_at':storage.now(),'tables':[],'rules':rules,'source_manifest_sha256':hashlib.sha256((ROOT/'migration/jw_manifest.json').read_bytes()).hexdigest()}
        try:
            with storage.db() as target:
                target.execute('INSERT INTO migration_runs VALUES(?,?,?,?,?,?)',(run_id,'jw_db','extracting','{}',storage.now(),None))
            for entry in manifest['tables']:
                with storage.db() as target:
                    result=mirror_table(source,target,entry,run_id)
                    report['tables'].append(result)
                    target.execute('UPDATE migration_runs SET report=? WHERE id=?',(storage.encode(report),run_id))
                print(f"{entry['name']}: {result['target_count']} rows, checksum OK",flush=True)
            # DDL changes during extraction invalidate the snapshot manifest.
            after=discover(source,manifest)
            if after['schema_errors']:raise RuntimeError('Source schema changed while extracting; output not published')
            source.rollback()
            report['extraction_completed']=True
            with storage.db() as target:
                target.execute('UPDATE migration_runs SET status=?,report=? WHERE id=?',('projecting',storage.encode(report),run_id))
            with storage.db() as target:
                if target.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                    raise RuntimeError('Could not enable offline projection journal')
            with storage.db() as target:
                target.execute('PRAGMA cache_size=-65536')
                report['projection']=Projector(target,rules).run()
                report['raw_only_tables']=[t['name'] for t in manifest['tables'] if t['name'] not in report['projection']['projected']]
                status='needs_review' if report['projection']['issue_count'] else 'verified'
                report['status']=status;report['finished_at']=storage.now()
                target.execute('UPDATE migration_runs SET status=?,report=?,finished_at=? WHERE id=?',(status,storage.encode(report),storage.now(),run_id))
                storage.audit(target,{'id':'migration','name':'数据迁移'},'完成只读迁移','migration',run_id,status)
            with storage.db() as target:
                target.execute('PRAGMA journal_mode=WAL')
                if target.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                    raise RuntimeError('Checkpoint busy; output not published')
            staging.replace(output)
            output.with_suffix('.report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({'database':str(output),'status':status,'issues':report['projection']['issue_count']},ensure_ascii=False))
        except BaseException:
            with storage.db() as target:
                target.execute('UPDATE migration_runs SET status=?,report=?,finished_at=? WHERE id=?',('failed',storage.encode(report),storage.now(),run_id))
            print(f'Failed output retained for diagnosis: {staging}',file=sys.stderr)
            raise
    finally:
        source.rollback();source.close()


if __name__=='__main__':main()
