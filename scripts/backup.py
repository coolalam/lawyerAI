"""Create a consistent SQLite backup plus uploaded files; never overwrite an archive."""
import argparse
from pathlib import Path
import sqlite3
import tempfile
import zipfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--database',default='data/office.sqlite3')
parser.add_argument('--data-dir',default='data')
parser.add_argument('--output',required=True)
args=parser.parse_args()
source=Path(args.database).resolve();output=Path(args.output).resolve();data=Path(args.data_dir).resolve()
if not source.is_file():raise SystemExit('Database not found')
if output.exists():raise SystemExit('Output already exists')
if output.is_relative_to(data/'uploads'):raise SystemExit('Backup must not be inside uploads directory')
output.parent.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='lawyerai-backup-') as temp:
    snapshot=Path(temp)/'office.sqlite3'
    with sqlite3.connect(f'{source.as_uri()}?mode=ro',uri=True) as reader,sqlite3.connect(snapshot) as writer:
        reader.backup(writer)
        if writer.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Backup integrity check failed')
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot,'office.sqlite3')
        if (data/'uploads').exists():
            for file in (data/'uploads').iterdir():
                if file.is_file() and not file.is_symlink():archive.write(file,'uploads/'+file.name)
print(f'Backup created: {output}')
