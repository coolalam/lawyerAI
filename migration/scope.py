"""Confirmed source scope: jw_db. Includes dependencies and historical detail tables."""
GROUPS = {
    '案件': ['t_case', 't_case_class', 't_case_client_ref', 't_case_client_relation_info', 't_case_court',
             't_case_lawer_ref', 't_case_linker_ref', 't_case_pay_detail', 't_case_proccess', 't_case_proccess_def',
             't_case_property_preservation', 't_case_to_check', 't_case_to_worklog', 't_caseend',
             'yw_case_process', 'yw_case_process_attachment', 'yw_case_process_emp_ref', 'yw_case_process_reply',
             'yw_case_process_reply_visit', 'yw_case_process_template', 'yw_case_process_template_class', 'yw_case_process_permission'],
    '利益冲突': ['t_conflict_search_mstr', 't_conflict_search_det'],
    '案件财务': ['t_invoice', 't_invoice_share_set', 't_invoice_sharer', 't_payment', 't_payment_sharer',
                 't_currency', 't_client_bill', 't_lawyer_fee', 't_charge', 't_charge_det', 't_charge_file', 't_charge_payment'],
    '案件文档': ['t_document', 't_doc_version', 't_document_approve', 't_doc_class', 't_case_file_class',
                 't_file_info', 't_archived_file', 't_document_code', 't_documentid_ctrl', 't_file_class', 't_file_status'],
    '用印与审批历史': ['xz_seal_request', 't_common_attach', 't_common_flow', 't_operate_history'],
    '关联基础数据': ['t_employee', 't_client', 't_client_contacts', 't_client_relation_info', 't_department',
                     't_suboffice_info', 't_general_code', 'pbcatcol', 't_work_log'],
}
CORE_TABLES = [name for tables in GROUPS.values() for name in tables]

# Source office and case keys, verified from information_schema. No name-based joins.
CASE_KEYS = {
    't_case': ('ca_office_id', 'ca_case_id'),
    't_case_lawer_ref': ('cld_office_id', 'cld_case_id'),
    't_case_client_relation_info': ('ccri_office_id', 'ccri_case_id'),
    't_case_court': ('cac_office_id', 'cac_case_id'),
    't_case_pay_detail': ('cpr_office_id', 'cpr_case_id'),
    't_caseend': ('cc_office_id', 'cc_case_id'),
    't_invoice': ('iv_office_id', 'iv_case_id'),
    't_payment': ('pay_office_id', 'pay_case_id'),
    't_payment_sharer': ('ps_office_id', 'ps_case_id'),
    't_document': ('do_office_id', 'do_relate_case'),
    't_conflict_search_mstr': ('csm_office_id', 'csm_case_id'),
}
