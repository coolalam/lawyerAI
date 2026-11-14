"""Read-only dictionary and structural profiling for the confirmed jw_db source."""
import getpass
import json
from pathlib import Path
import pymysql

conn = pymysql.connect(host='192.168.3.2', port=3366, user='delitest',
                       password=getpass.getpass('Source password: '), database='jw_db',
                       connect_timeout=8, read_timeout=60, charset='utf8mb4',
                       cursorclass=pymysql.cursors.DictCursor)
queries = {
    'labels': "SELECT TRIM(pbc_tnam) table_name, TRIM(pbc_cnam) column_name, pbc_labl label, pbc_hdr header, pbc_cmnt comment FROM pbcatcol",
    'codes': 'SELECT gc_office_id,gc_code_group,gc_id,gc_name,gc_type,gc_Description FROM t_general_code',
    'case_status': 'SELECT ca_status value,COUNT(*) count FROM t_case GROUP BY ca_status',
    'case_category': 'SELECT ca_category value,COUNT(*) count FROM t_case GROUP BY ca_category',
    'invoice_status': 'SELECT iv_status value,iv_app_status app_status,COUNT(*) count FROM t_invoice GROUP BY iv_status,iv_app_status',
    'payment_status': 'SELECT pay_status value,COUNT(*) count FROM t_payment GROUP BY pay_status',
    'seal_status': 'SELECT xzsr_status value,COUNT(*) count FROM xz_seal_request GROUP BY xzsr_status',
    'party_types': 'SELECT ccri_type value,COUNT(*) count FROM t_case_client_relation_info GROUP BY ccri_type',
    'lawyer_roles': 'SELECT cld_task_type value,COUNT(*) count,MIN(cld_percent) min_percent,MAX(cld_percent) max_percent FROM t_case_lawer_ref GROUP BY cld_task_type',
    'storage_shapes': "SELECT CASE WHEN do_file_path LIKE 'http%' THEN 'http' WHEN do_file_path LIKE '%:%' THEN 'drive_or_scheme' ELSE 'relative_or_empty' END path_type,COUNT(*) count FROM t_document GROUP BY path_type",
}
output = {}
with conn:
    with conn.cursor() as cur:
        for key, query in queries.items():
            cur.execute(query)
            output[key] = cur.fetchall()
Path('artifacts/jw_profile.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding='utf8')
print(json.dumps({k: v if k not in ('labels', 'codes') else f'{len(v)} rows' for k, v in output.items()}, ensure_ascii=False, default=str))
