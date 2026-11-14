"""Generate a versioned, metadata-only migration manifest from read-only discovery."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from migration.scope import GROUPS

schema = json.loads((ROOT/'artifacts/source_schema.json').read_text())['jw_db']
profile = json.loads((ROOT/'artifacts/jw_profile.json').read_text())
labels = {(r['table_name'], r['column_name']): r['label'] for r in profile['labels']}
manifest = {'source_database': 'jw_db', 'inspected_on': '2026-11-12', 'tables': []}
for module, names in GROUPS.items():
    for name in names:
        table = next(t for t in schema['tables'] if t['TABLE_NAME']==name)
        columns = [{'name': c['COLUMN_NAME'], 'type': c['COLUMN_TYPE'], 'nullable': c['IS_NULLABLE']=='YES',
                    'primary': c['COLUMN_KEY']=='PRI', 'comment': c['COLUMN_COMMENT'],
                    'label': labels.get((name,c['COLUMN_NAME'])) or c['COLUMN_COMMENT'] or c['COLUMN_NAME']}
                   for c in schema['columns'] if c['TABLE_NAME']==name]
        manifest['tables'].append({'name':name, 'module':module, 'estimated_rows':table['TABLE_ROWS'], 'columns':columns})
(ROOT/'migration/jw_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
(ROOT/'migration/jw_dictionaries.json').write_text(json.dumps(profile['codes'],ensure_ascii=False,indent=2),encoding='utf8')
print(f"Generated {len(manifest['tables'])} table definitions; no business rows or credentials included.")
