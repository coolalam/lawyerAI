"""Read-only source discovery. Password stays in memory; no business rows are exported."""
import argparse
import getpass
import json
import os
from pathlib import Path

import pymysql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=3306)
    parser.add_argument('--user', required=True)
    parser.add_argument('--database')
    parser.add_argument('--output', default='artifacts/source_schema.json')
    args = parser.parse_args()
    password = os.environ.get('SOURCE_MYSQL_PASSWORD') or getpass.getpass('Source password: ')
    connection = pymysql.connect(host=args.host, port=args.port, user=args.user,
                                 password=password, connect_timeout=8, read_timeout=30,
                                 charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    result = {}
    with connection:
        with connection.cursor() as cur:
            cur.execute('SHOW DATABASES')
            databases = [r['Database'] for r in cur.fetchall() if r['Database'] not in
                         ('information_schema', 'mysql', 'performance_schema', 'sys')]
            if args.database:
                databases = [args.database]
            for db in databases:
                cur.execute('SELECT TABLE_NAME,TABLE_TYPE,TABLE_ROWS,TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (db,))
                tables = cur.fetchall()
                cur.execute('SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,COLUMN_KEY,EXTRA,COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION', (db,))
                columns = cur.fetchall()
                cur.execute('SELECT TABLE_NAME,COLUMN_NAME,CONSTRAINT_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s', (db,))
                keys = cur.fetchall()
                result[db] = {'tables': tables, 'columns': columns, 'keys': keys}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({db: {'table_count': len(meta['tables']), 'tables': meta['tables']} for db, meta in result.items()}, ensure_ascii=False, default=str))
    print(f'Schema saved: {path}')


if __name__ == '__main__':
    main()
