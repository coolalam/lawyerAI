"""Read-only supplemental dictionaries and identities, excluding obsolete password values."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pymysql
from backend.db import encode,now
from scripts.migrate_mysql import quote,source_value,digest_row,verify_table


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    path=Path(args.output).resolve()
    if path.exists():raise SystemExit('Output exists; refusing overwrite')
    password=os.environ.get('SOURCE_MYSQL_PASSWORD') or getpass.getpass('Source password: ')
    with pymysql.connect(host='192.168.3.2',port=3366,user='delitest',password=password,database='jw_db',charset='utf8mb4',connect_timeout=10,read_timeout=60) as source:
        del password
        with source.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            with sqlite3.connect(path) as target:
                path.chmod(0o600)
                target.execute('CREATE TABLE supplemental_tables(name TEXT PRIMARY KEY,schema_json TEXT,row_count INTEGER,checksum TEXT,captured_at TEXT,excluded_columns TEXT)')
                for table in ('t_user','t_interface_reason'):
                    cur.execute('SELECT COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION',('jw_db',table))
                    columns=[{'name':r[0],'type':r[1]} for r in cur.fetchall() if r[0]!='us_password']
                    names=[c['name'] for c in columns]
                    target.execute('CREATE TABLE '+quote('src__'+table)+' (_migration_row_id INTEGER PRIMARY KEY,'+','.join(quote(n)+' TEXT' for n in names)+')')
                    cur.execute('SELECT COUNT(*) FROM '+quote(table));expected=cur.fetchone()[0]
                    cur.execute('SELECT '+','.join(quote(n) for n in names)+' FROM '+quote(table))
                    checksum=hashlib.sha256();count=0
                    while rows:=cur.fetchmany(2000):
                        values=[[source_value(v) for v in r] for r in rows]
                        for row in values:checksum.update(digest_row(row))
                        target.executemany('INSERT INTO '+quote('src__'+table)+' ('+','.join(quote(n) for n in names)+') VALUES('+','.join('?' for _ in names)+')',values)
                        count+=len(values)
                    local_count,local_hash=verify_table(target,table,names)
                    if count!=expected or local_count!=count or local_hash!=checksum.hexdigest():raise RuntimeError('Supplement checksum mismatch')
                    target.execute('INSERT INTO supplemental_tables VALUES(?,?,?,?,?,?)',(table,encode({'name':table,'columns':columns}),count,local_hash,now(),encode(['us_password'] if table=='t_user' else [])))
                    print(f'{table}: {count} rows, checksum OK',flush=True)
        source.rollback()


if __name__=='__main__':main()
