"""Resume only local projection from a complete, verified raw snapshot; never reconnect to MySQL."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db as storage
from migration.project import Projector
from scripts.migrate_mysql import verify_table


def resume(partial, output):
    partial, output = Path(partial).resolve(), Path(output).resolve()
    if not partial.is_file() or output.exists() or output.with_suffix('.report.json').exists():
        raise ValueError('Require existing partial database and unused output/report paths')
    storage.DB_PATH = partial
    manifest_bytes = (ROOT / 'migration/jw_manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    with storage.db() as conn:
        run = conn.execute('SELECT * FROM migration_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        if not run or run['status'] not in ('failed', 'projecting'):
            raise ValueError('Only an interrupted projection may be resumed')
        report = json.loads(run['report'])
        if not report.get('extraction_completed'):
            raise ValueError('Extraction completion and final source schema check were not recorded')
        if report.get('source_manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest():
            raise ValueError('Manifest differs from original extraction')
        metadata = {r['name']: r for r in conn.execute('SELECT * FROM source_tables')}
        reported = {r['table']: r for r in report.get('tables', [])}
        expected = {t['name'] for t in manifest['tables']}
        if set(metadata) != expected or set(reported) != expected:
            raise ValueError('Extraction incomplete; do not resume a live source snapshot')
        if conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]:
            raise ValueError('Projection must have rolled back completely; existing business data is never overwritten')
        for entry in manifest['tables']:
            name = entry['name']
            meta, original = metadata[name], reported[name]
            if json.loads(meta['schema_json']) != entry or meta['run_id'] != run['id']:
                raise ValueError(f'{name}: schema/run mismatch')
            count, checksum = verify_table(conn, name, [c['name'] for c in entry['columns']])
            if not (count == meta['row_count'] == original['source_count'] == original['target_count'] and checksum == meta['checksum'] == original['sha256']):
                raise ValueError(f'{name}: incomplete or corrupt raw snapshot')
            print(f'Local snapshot {name}: {count} rows, checksum OK', flush=True)
        report['extraction_completed'] = True
        report['projection_resumed_at'] = storage.now()
        conn.execute('UPDATE migration_runs SET status=?,report=? WHERE id=?', ('projecting', storage.encode(report), run['id']))
    try:
        # This offline output has no concurrent readers. A rollback journal avoids
        # holding a second database-sized WAL during projection and checkpoint.
        with storage.db() as conn:
            if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise RuntimeError('Could not enable offline rollback journal')
        with storage.db() as conn:
            conn.execute('PRAGMA cache_size=-65536')
            report['projection'] = Projector(conn, report.get('rules', {})).run()
            report['raw_only_tables'] = [t['name'] for t in manifest['tables'] if t['name'] not in report['projection']['projected']]
            report['status'] = 'needs_review' if report['projection']['issue_count'] else 'verified'
            report['finished_at'] = storage.now()
            conn.execute('UPDATE migration_runs SET status=?,report=?,finished_at=? WHERE id=?',
                         (report['status'], storage.encode(report), report['finished_at'], run['id']))
            storage.audit(conn, {'id': 'migration', 'name': '数据迁移'}, '完成本地快照投影', 'migration', run['id'], report['status'])
        with storage.db() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0]:
                raise RuntimeError('Checkpoint busy; output not published')
        partial.replace(output)
        output.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({'database': str(output), 'status': report['status'], 'issues': report['projection']['issue_count']}, ensure_ascii=False), flush=True)
    except BaseException:
        if partial.exists():
            with storage.db() as conn:
                conn.execute('UPDATE migration_runs SET status=? WHERE id=?', ('failed', run['id']))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('partial')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    resume(args.partial, args.output)
