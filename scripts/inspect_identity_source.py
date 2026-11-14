"""Read-only aggregate inspection of account and cause dictionaries; no credentials/PII output."""
import getpass
import os
import pymysql

with pymysql.connect(host='192.168.3.2', port=3366, user='delitest',
                     password=os.environ.get('SOURCE_MYSQL_PASSWORD') or getpass.getpass('Source password: '),
                     database='jw_db', charset='utf8mb4', connect_timeout=10, read_timeout=60) as conn:
    with conn.cursor() as cur:
        cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        queries = {
            'user_states': 'SELECT us_status,us_is_group,COUNT(*) FROM t_user GROUP BY 1,2',
            'user_identifiers': "SELECT COUNT(*),SUM(us_empl_id IS NULL OR us_empl_id=''),COUNT(DISTINCT us_user_id),COUNT(DISTINCT us_user_name) FROM t_user",
            'user_employee_links': 'SELECT COUNT(*) FROM t_user u JOIN t_employee e ON u.us_empl_id=e.emp_empl_id AND u.us_office_id=e.emp_office_id',
            'reason_sample': "SELECT itr_code_group,itr_id,itr_code,itr_name,itr_parent_code FROM t_interface_reason WHERE itr_code IN ('MS4','M4_1','M4_1_25') OR itr_id IN ('MS4','M4_1','M4_1_25') LIMIT 10",
            'reason_count': 'SELECT COUNT(*) FROM t_interface_reason',
            'employee_user_links': 'SELECT COUNT(*),SUM(u.us_empl_id=e.emp_empl_id) FROM t_user u JOIN t_employee e ON u.us_user_id=e.emp_user_id',
            'employee_id_links': 'SELECT COUNT(*) FROM t_user u JOIN t_employee e ON u.us_empl_id=e.emp_empl_id',
            'identity_shape': "SELECT LEFT(us_user_id,2),LENGTH(us_user_id),us_office_id,COUNT(*) FROM t_user GROUP BY 1,2,3",
            'reason_examples': 'SELECT itr_code_group,itr_id,itr_code,itr_name FROM t_interface_reason LIMIT 5',
        }
        for label, query in queries.items():
            cur.execute(query)
            print(label,cur.fetchall(),flush=True)
    conn.rollback()
