"""Shared form/API contracts. Historical fields remain separately preserved in legacy_data."""


def f(key, label, type='text', required=False, options=None, default=None):
    return dict(key=key, label=label, type=type, required=required, options=options, default=default)


SCHEMAS = {
    'cases': dict(label='案件', fields=[
        f('number', '案件编号', required=True), f('title', '案件名称', required=True),
        f('office', '分所', required=True, default='上海总部'), f('department', '业务部门', default='争议解决部'),
        f('category', '案件类型', 'select', True, ['民事诉讼', '刑事辩护', '行政诉讼', '商事仲裁', '非诉专项', '常年顾问'], '民事诉讼'),
        f('cause', '案由'), f('client', '委托人', required=True), f('opponent', '对方当事人'),
        f('lead_lawyer_id', '主办律师', 'lawyer', True), f('stage', '办理阶段', 'select', False, ['收案', '一审', '二审', '再审', '执行', '非诉服务'], '收案'),
        f('opened_on', '收案日期', 'date', True), f('court', '承办法院 / 仲裁机构'),
        f('court_number', '法院案号'), f('judge', '承办法官 / 联系方式'), f('representing', '代理何方'),
        f('amount', '合同服务费', 'money', True, default='0.00'), f('target_amount', '争议标的额', 'money', default='0.00'),
        f('currency', '币种', 'select', True, ['CNY', 'USD', 'HKD', 'EUR', 'JPY'], 'CNY'),
        f('fee_method', '收费方式', 'select', False, ['固定收费', '计时收费', '风险代理', '混合收费', '法律援助'], '固定收费'),
        f('hourly_rate', '计时费率', 'money'), f('risk_ratio', '风险代理比例 %', 'ratio'),
        f('start_on', '委托开始日期', 'date'), f('end_on', '委托到期日期', 'date'),
        f('description', '案情简介', 'textarea'), f('mandate', '委托事项', 'textarea'),
        f('confidential', '保密案件', 'boolean', default=False), f('archive_number', '归档编号'),
        f('archive_location', '卷宗存放位置'), f('closed_on', '结案日期', 'date'),
        f('result', '结案结果', 'textarea'), f('notes', '备注', 'textarea')]),
    'lawyers': dict(label='律师', fields=[f('name', '姓名', required=True), f('office', '分所', default='上海总部'),
        f('department', '部门'), f('title', '职务', default='执业律师'), f('license', '执业证号'),
        f('phone', '联系电话'), f('email', '邮箱'), f('specialty', '专业领域'), f('notes', '备注', 'textarea')]),
    'parties': dict(label='案件当事人', fields=[f('name', '当事人名称', required=True),
        f('role', '诉讼关系', 'select', True, ['委托方', '对方当事人', '第三人', '委托方关联公司', '对方当事人关联公司', '其他关联方'], '委托方'),
        f('identifier', '证件号 / 社会信用代码'), f('aliases', '曾用名 / 别名'), f('contact', '联系人'),
        f('phone', '电话'), f('email', '邮箱'), f('address', '地址'), f('notes', '备注', 'textarea')]),
    'payment_plans': dict(label='收款计划', fields=[f('title', '收款节点', required=True), f('amount', '应收金额', 'money', True),
        f('due_on', '计划收款日期', 'date'), f('condition', '收款条件', 'textarea'), f('notes', '备注', 'textarea')]),
    'invoices': dict(label='开票申请', fields=[f('title', '发票抬头', required=True), f('number', '发票号码'),
        f('tax_id', '纳税人识别号', required=True), f('type', '发票类型', 'select', True, ['增值税专用发票', '增值税普通发票'], '增值税专用发票'),
        f('amount', '含税金额', 'money', True), f('tax_rate', '税率 %', 'ratio', True, default='6'),
        f('issued_on', '开票日期', 'date'), f('bank', '开户银行'), f('bank_account', '银行账号'),
        f('address', '注册地址'), f('phone', '联系电话'), f('notes', '开票内容 / 备注', 'textarea')]),
    'receipts': dict(label='到账登记', fields=[f('title', '收款说明', required=True), f('amount', '到账金额', 'money', True),
        f('received_on', '到账日期', 'date', True), f('payer', '付款方', required=True),
        f('bank', '收款银行'), f('bank_account', '收款账号'), f('reference', '银行流水号', required=True),
        f('method', '收款方式', 'select', True, ['银行转账', '现金', '支票', '其他'], '银行转账'),
        f('invoice_id', '关联发票', 'invoice'), f('plan_id', '关联收款计划', 'plan'),
        f('lawyer_fee', '律师费', 'money'), f('case_fee', '代收办案费', 'money'), f('notes', '备注', 'textarea')]),
    'contracts': dict(label='委托合同', fields=[f('title', '合同名称', required=True), f('number', '合同编号', required=True),
        f('signed_on', '签订日期', 'date'), f('document_id', '合同原件', 'document', True),
        f('archive_document_id', '已盖章合同', 'document'), f('archive_number', '归档编号'),
        f('archive_location', '纸质合同存放位置'), f('notes', '备注', 'textarea')]),
    'documents': dict(label='案件文档', fields=[f('title', '文档名称', required=True),
        f('category', '文档分类', 'select', True, ['委托合同', '诉讼文书', '证据材料', '庭审材料', '裁判文书', '归档卷宗', '其他'], '诉讼文书'),
        f('description', '文档说明', 'textarea'), f('confidential', '保密文档', 'boolean', default=False)]),
    'seals': dict(label='用印申请', fields=[f('title', '文件名称', required=True),
        f('seal_type', '印章类型', 'select', True, ['律所公章', '合同专用章', '法人章', '财务章'], '律所公章'),
        f('copies', '用印份数', 'integer', True, default=1), f('recipient', '送达单位', required=True),
        f('reason', '用印事由', 'textarea', True), f('urgent', '紧急用印', 'boolean', default=False),
        f('document_id', '待盖章文件', 'document', True), f('archive_document_id', '已盖章归档文件', 'document'),
        f('collector', '领取人'), f('notes', '备注', 'textarea')]),
    'tasks': dict(label='日程与任务', fields=[f('title', '事项名称', required=True),
        f('type', '事项类型', 'select', True, ['工作任务', '开庭', '举证期限', '答辩期限', '上诉期限', '申请执行期限', '客户会议'], '工作任务'),
        f('assignee_id', '负责人', 'lawyer', True), f('due_on', '截止日期', 'date', True),
        f('priority', '优先级', 'select', True, ['普通', '重要', '紧急'], '普通'), f('notes', '事项说明', 'textarea')]),
    'conflicts': dict(label='利益冲突审查', fields=[f('title', '检索主体', required=True),
        f('notes', '申请说明', 'textarea'), f('review_note', '审查意见', 'textarea')]),
}

STATES = {
    'cases': {'intake': '利冲审查', 'draft': '待填申请', 'submitted': '立案待审批', 'rejected': '立案退回', 'active': '办理中', 'closed': '已结案', 'archived': '已归档', 'void': '已撤销'},
    'lawyers': {'active': '在职', 'inactive': '离职'}, 'parties': {'active': '有效'},
    'payment_plans': {'active': '待收款', 'done': '已完成'},
    'invoices': {'draft': '草稿', 'submitted': '待审核', 'approved': '待开票', 'issued': '已开票', 'rejected': '已退回', 'void': '已作废'},
    'receipts': {'draft': '待核销', 'confirmed': '已确认', 'void': '已冲销'},
    'documents': {'active': '已上传', 'missing': '待补附件'},
    'contracts': {'draft': '待申请盖章', 'stamping': '盖章处理中', 'stamped': '待归档', 'archived': '已归档'},
    'seals': {'draft': '草稿', 'submitted': '待审批', 'approved': '待盖章', 'stamped': '待领取', 'collected': '已领取', 'rejected': '已退回', 'void': '已撤销'},
    'tasks': {'open': '待完成', 'done': '已完成'},
    'conflicts': {'pending': '待审查', 'cleared': '审查通过', 'waived': '豁免通过', 'blocked': '禁止承接'},
}

INITIAL = {'cases': 'intake', 'contracts': 'draft', 'invoices': 'draft', 'receipts': 'draft', 'seals': 'draft', 'tasks': 'open', 'conflicts': 'pending'}
TRANSITIONS = {
    'cases': {'intake': ['draft', 'void'], 'draft': ['submitted', 'void'], 'submitted': ['active', 'rejected'], 'rejected': ['submitted', 'void'], 'active': ['closed', 'void'], 'closed': ['active', 'archived']},
    'contracts': {'draft': ['stamping'], 'stamped': ['archived']},
    'lawyers': {'active': ['inactive'], 'inactive': ['active']},
    'payment_plans': {'active': ['done'], 'done': ['active']},
    'invoices': {'draft': ['submitted'], 'submitted': ['approved', 'rejected'], 'rejected': ['submitted'], 'approved': ['issued', 'void'], 'issued': ['void']},
    'receipts': {'draft': ['confirmed'], 'confirmed': ['void']},
    'seals': {'draft': ['submitted'], 'submitted': ['approved', 'rejected', 'void'], 'rejected': ['submitted', 'void'], 'approved': ['stamped', 'void'], 'stamped': ['collected']},
    'tasks': {'open': ['done'], 'done': ['open']},
    'conflicts': {'pending': ['cleared', 'waived', 'blocked']},
}

GLOBAL_KINDS = {'cases', 'lawyers'}
ROLES = {'admin': '系统管理员', 'partner': '合伙人', 'lawyer': '律师', 'finance': '财务', 'clerk': '行政'}
