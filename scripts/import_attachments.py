"""Import original documents (including explicitly mapped container byte ranges).

The source root is read-only. A file manifest must explicitly specify relative
paths, offsets and lengths; Windows paths and packed-file formats are never guessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db as storage


def copy_range(source_root,entry,destination):
    relative=Path(entry['path'])
    if relative.is_absolute():raise ValueError('Attachment path must be relative to --source-root')
    path=(source_root/relative).resolve()
    if not path.is_relative_to(source_root) or not path.is_file():raise ValueError('Source file missing or outside authorized root')
    offset=int(entry.get('offset',0));length=entry.get('length')
    if offset<0 or (length is not None and int(length)<=0):raise ValueError('Invalid byte range')
    length=int(length) if length is not None else path.stat().st_size-offset
    if length<=0 or offset+length>path.stat().st_size:raise ValueError('Byte range exceeds source file')
    digest=hashlib.sha256();size=0
    try:
        with path.open('rb') as source,destination.open('xb') as output:
            source.seek(offset)
            while size<length:
                chunk=source.read(min(1024*1024,length-size))
                if not chunk:raise ValueError('Unexpected end of source file')
                size+=len(chunk);digest.update(chunk);output.write(chunk)
        if entry.get('sha256') and digest.hexdigest()!=entry['sha256']:
            raise ValueError('Source SHA-256 does not match the manifest')
        local=hashlib.sha256()
        with destination.open('rb') as source:
            while chunk:=source.read(1024*1024):local.update(chunk)
        if local.hexdigest()!=digest.hexdigest():raise ValueError('Copied file checksum differs')
        return {'sha256':digest.hexdigest(),'size':size}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--data-dir',required=True,help='Same LAW_DATA_DIR used by the application')
    parser.add_argument('--source-root',help='Authorized original file storage root')
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--make-manifest',action='store_true',help='Generate a template; does not copy files')
    args=parser.parse_args()
    storage.DB_PATH=Path(args.database).resolve()
    if not storage.DB_PATH.is_file():raise SystemExit('Database does not exist')
    manifest_path=Path(args.manifest).resolve()
    if args.make_manifest:
        if manifest_path.exists():raise SystemExit('Manifest already exists; use a new path')
        items=[]
        with storage.db() as conn:
            for row in conn.execute("SELECT * FROM records WHERE kind='documents' AND source_key IS NOT NULL AND deleted=0"):
                doc=storage.unpack(row)
                items.append({'record_id':doc['id'],'filename':doc.get('title') or 'document','path':'',
                    'offset':0,'length':None,'source_version':'current','is_current':True,
                    'source_path_reference':doc.get('source_path'), 'source_offset_reference':doc.get('source_offset'),
                    'source_length_reference':doc.get('source_length'),'sha256':''})
        manifest_path.parent.mkdir(parents=True,exist_ok=True)
        manifest_path.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'Created {len(items)} document entries. Fill reviewed paths and byte ranges before import.')
        return
    if not args.source_root:raise SystemExit('--source-root is required for import')
    source_root=Path(args.source_root).resolve()
    destination_dir=Path(args.data_dir).resolve()/'uploads';destination_dir.mkdir(parents=True,exist_ok=True)
    entries=json.loads(manifest_path.read_text());report={'copied':0,'skipped':0,'errors':[]}
    for entry in entries:
        blob=storage.uid();destination=destination_dir/blob
        try:
            with storage.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                doc=storage.get_record(conn,entry['record_id'],'documents')
                if not doc:raise ValueError('Document record not found')
                source_version=str(entry.get('source_version','current'))
                previous=[f for f in doc.get('files',[]) if f.get('source_version')==source_version]
                if previous:
                    if entry.get('sha256') and previous[0]['sha256']!=entry['sha256']:raise ValueError('Same historical version already exists with different checksum')
                    report['skipped']+=1;continue
                result=copy_range(source_root,entry,destination)
                file={**result,'blob':blob,'filename':Path(entry.get('filename') or entry['path']).name,
                      'uploaded_at':storage.now(),'uploaded_by':'附件迁移','source_version':source_version,
                      'is_current':bool(entry.get('is_current',source_version=='current'))}
                doc.setdefault('files',[]).append(file)
                doc['files'].sort(key=lambda f:(bool(f.get('is_current')),int(f.get('source_version','0')) if str(f.get('source_version','')).isdigit() else -1,str(f.get('source_version',''))))
                doc['status']='active'
                storage.save_record(conn,'documents',doc,'migration',record_id=doc['id'])
                expected_versions=set()
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='src__t_doc_version'").fetchone():
                    expected_versions={str(r[0]) for r in conn.execute('SELECT dv_version FROM src__t_doc_version WHERE dv_doc_id=?',(doc.get('legacy_data',{}).get('do_doc_id'),))}
                actual_versions={str(f.get('source_version')) for f in doc['files']}
                if expected_versions.issubset(actual_versions) and any(f.get('is_current') for f in doc['files']):
                    conn.execute("UPDATE migration_issues SET resolved=1 WHERE table_name='t_document' AND source_key=? AND code='attachment_pending'",(storage.encode(doc.get('legacy_key')),))
                storage.audit(conn,{'id':'migration','name':'附件迁移'},'导入附件版本','documents',doc['id'],result['sha256'])
                report['copied']+=1
        except Exception as exc:
            destination.unlink(missing_ok=True)
            report['errors'].append({'record_id':entry.get('record_id'),'error':str(exc)})
    report_path=manifest_path.with_name(manifest_path.stem+'.result-'+storage.uid()[:8]+'.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False));sys.exit(2 if report['errors'] else 0)


if __name__=='__main__':main()
