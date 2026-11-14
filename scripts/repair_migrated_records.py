"""Apply identity/dictionary supplement and repair explicit conflict relationships."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db as storage
from scripts.migrate_mysql import quote,verify_table
from migration.repair import import_accounts,repair_conflicts
from migration.references import References


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--supplement',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args()
    target=Path(args.database).resolve();source=Path(args.supplement).resolve();report_path=Path(args.report)
    if not target.is_file() or not source.is_file() or report_path.exists():raise SystemExit('Require existing database/supplement and unused report path')
    storage.DB_PATH=target;storage.init_db()
    report={'started_at':storage.now(),'database':str(target),'supplemental_tables':[]}
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as incoming,storage.db() as conn:
        incoming.row_factory=sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS supplemental_tables(name TEXT PRIMARY KEY,schema_json TEXT,row_count INTEGER,checksum TEXT,captured_at TEXT,excluded_columns TEXT)')
        for row in incoming.execute('SELECT * FROM supplemental_tables'):
            table=row['name'];columns=[c['name'] for c in json.loads(row['schema_json'])['columns']]
            count,checksum=verify_table(incoming,table,columns)
            if count!=row['row_count'] or checksum!=row['checksum']:raise ValueError('Supplement verification failed')
            old=conn.execute('SELECT checksum FROM supplemental_tables WHERE name=?',(table,)).fetchone()
            if old:
                if old['checksum']!=checksum:raise ValueError('Existing supplemental snapshot differs; refusing overwrite')
            else:
                conn.execute('CREATE TABLE '+quote('src__'+table)+' (_migration_row_id INTEGER PRIMARY KEY,'+','.join(quote(c)+' TEXT' for c in columns)+')')
                conn.executemany('INSERT INTO '+quote('src__'+table)+' VALUES('+','.join('?' for _ in range(len(columns)+1))+')',incoming.execute('SELECT * FROM '+quote('src__'+table)))
                conn.execute('INSERT INTO supplemental_tables VALUES(?,?,?,?,?,?)',tuple(row))
            if verify_table(conn,table,columns)!=(count,checksum):raise ValueError('Target supplement verification failed')
            report['supplemental_tables'].append({'table':table,'rows':count,'sha256':checksum,'excluded_columns':json.loads(row['excluded_columns'])})
        report['accounts']=import_accounts(conn)
        print(json.dumps(report['accounts'],ensure_ascii=False),flush=True)
        report['conflicts']=repair_conflicts(conn)
        refs=References(conn)
        report['reference_labels']={'departments':len(refs.departments),'case_causes':len(refs.causes),'interface_causes':len(refs.interface_causes)}
        report['finished_at']=storage.now()
        storage.audit(conn,{'id':'migration-repair','name':'迁移补全'},'补全字典、账号及历史利冲关联','migration',detail=storage.encode(report))
        conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('migration_repair_20261113',storage.encode(report)))
    with report_path.open('x',encoding='utf8') as output:json.dump(report,output,ensure_ascii=False,indent=2)
    report_path.chmod(0o600)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
