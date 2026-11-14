"""Explicit, local-only sample content. Never reads or writes the source database."""
from datetime import date, timedelta
from .db import DATA, audit, now, save_record, uid
from .service import conflict_identity


def seed_demo(conn, actor):
    if conn.execute("SELECT 1 FROM records WHERE kind='cases' LIMIT 1").fetchone():
        raise ValueError('已有案件，不再自动混入演示数据')
    lawyers = []
    for name, title, department, specialty in [
        ('林知远', '高级合伙人', '争议解决部', '商事争议、公司诉讼'),
        ('陈以宁', '合伙人', '公司业务部', '投融资、并购重组'),
        ('周慕白', '执业律师', '知识产权部', '商标、著作权'),
        ('许书瑶', '执业律师', '争议解决部', '建设工程、房地产'),
        ('沈亦舟', '执业律师', '公司业务部', '劳动合规、公司治理'),
    ]:
        lawyers.append(save_record(conn, 'lawyers', {'name': name, 'title': title, 'department': department,
            'specialty': specialty, 'office': '上海总部', 'status': 'active', 'demo': True}, actor['id']))
    sample_cases = [
        ('远景科技股权转让争议案', '上海远景科技有限公司', '启航创投有限公司', '民事诉讼', '280000.00', 'active', 0, '一审'),
        ('恒信集团年度法律顾问', '恒信实业集团有限公司', '', '常年顾问', '360000.00', 'active', 1, '非诉服务'),
        ('云帆医疗 A 轮融资专项', '云帆医疗科技有限公司', '', '非诉专项', '180000.00', 'active', 1, '非诉服务'),
        ('青禾文创商标侵权纠纷', '青禾文化创意有限公司', '森原文化传播有限公司', '民事诉讼', '120000.00', 'submitted', 2, '收案'),
        ('启明建设工程合同纠纷', '启明建设有限公司', '华宸置业有限公司', '商事仲裁', '240000.00', 'active', 3, '一审'),
        ('星河数字劳动合规专项', '星河数字技术有限公司', '', '非诉专项', '80000.00', 'closed', 4, '非诉服务'),
        ('海越贸易货款追偿案', '海越国际贸易有限公司', '上海远景科技有限公司', '民事诉讼', '95000.00', 'intake', 0, '收案'),
        ('澄明投资股权激励方案', '澄明投资管理有限公司', '', '非诉专项', '150000.00', 'active', 1, '非诉服务'),
    ]
    today = date.today()
    for i, (title, client, opponent, category, amount, status, lead, stage) in enumerate(sample_cases):
        case = save_record(conn, 'cases', {'title': title, 'number': f'LX-{today.year}-{1086-i:04}', 'office': '上海总部',
            'department': lawyers[lead]['department'], 'client': client, 'opponent': opponent,
            'category': category, 'amount': amount, 'target_amount': '5000000.00', 'currency': 'CNY',
            'lead_lawyer_id': lawyers[lead]['id'], 'opened_on': (today-timedelta(days=30+i*7)).isoformat(),
            'status': status, 'stage': stage, 'cause': '股权转让纠纷' if i==0 else category,
            'court': '上海市浦东新区人民法院' if category=='民事诉讼' else '',
            'description': f'本案为虚构演示数据。受{client}委托，为其提供{category}法律服务，协同推进材料整理、法律分析及相关业务办理。',
            'fee_method': '固定收费', 'confidential': False, 'history': [], 'demo': True,
            'result': '专项法律服务完成，已向客户交付工作成果。' if status=='closed' else ''}, actor['id'])
        shares = [{'lawyer_id': lawyers[lead]['id'], 'lawyer_name': lawyers[lead]['name'], 'ratio': '70.0000', 'role': '主办律师'},
                  {'lawyer_id': lawyers[(lead+1)%len(lawyers)]['id'], 'lawyer_name': lawyers[(lead+1)%len(lawyers)]['name'], 'ratio': '30.0000', 'role': '协办律师'}]
        from .service import split_amount
        for share in split_amount(amount, shares):
            save_record(conn, 'allocations', {**share, 'scope_id': case['id'], 'status': 'active', 'demo': True}, actor['id'], case_id=case['id'])
        save_record(conn, 'parties', {'name': client, 'role': '委托方', 'status': 'active', 'demo': True}, actor['id'], case_id=case['id'])
        if opponent:
            save_record(conn, 'parties', {'name': opponent, 'role': '对方当事人', 'status': 'active', 'demo': True}, actor['id'], case_id=case['id'])
        save_record(conn, 'conflicts', {'title': client+(' / '+opponent if opponent else ''), 'status': 'pending' if status=='intake' else 'cleared',
            'terms': [client,opponent], 'matches': [{'name':opponent,'relation':'历史委托方','case_title':'远景科技股权转让争议案'}] if i==6 else [],
            'fingerprint': conflict_identity(conn, case), 'review_note': '' if status=='intake' else '演示审批：完成主体核查。',
            'history': [], 'demo': True, 'applicant_name': lawyers[lead]['name']}, actor['id'], case_id=case['id'])
        if status=='active':
            receipt_amount = str(int(float(amount)/2)) + '.00'
            invoice = save_record(conn, 'invoices', {'title': client, 'number': f'DEMO-2026-{i+1:04}', 'amount': receipt_amount,
                'tax_id':'演示税号', 'tax_rate':'6.0000', 'issued_on':(today-timedelta(days=8)).isoformat(), 'status':'issued',
                'type':'增值税专用发票', 'allocation_snapshot':split_amount(receipt_amount,shares),'demo':True,'history':[]},actor['id'],case_id=case['id'])
            save_record(conn,'receipts',{'title':'首期法律服务费','amount':receipt_amount,'received_on':(today-timedelta(days=4)).isoformat(),
                'reference':f'DEMO-BANK-{i+1}','payer':client,'method':'银行转账','invoice_id':invoice['id'],'status':'confirmed',
                'allocation_snapshot':split_amount(receipt_amount,shares),'demo':True,'history':[]},actor['id'],case_id=case['id'])
            save_record(conn,'payment_plans',{'title':'第二期法律服务费','amount':receipt_amount,'due_on':(today+timedelta(days=15+i)).isoformat(),'status':'active','demo':True},actor['id'],case_id=case['id'])
        if i in (0,2,4):
            save_record(conn, 'tasks', {'title': ['整理庭审证据目录','融资文件法律核查','提交仲裁答辩材料'][[0,2,4].index(i)],
                'type':'举证期限' if i==0 else '工作任务','due_on':(today+timedelta(days=i+1)).isoformat(),
                'priority':'重要','assignee_id':lawyers[lead]['id'],'status':'open','demo':True},actor['id'],case_id=case['id'])
        if i==0:
            blob=uid(); upload_dir=DATA/'uploads';upload_dir.mkdir(exist_ok=True)
            content='律序平台演示文件\n此文件仅用于演示合同上传、用印及归档流程，不是真实合同。\n'.encode()
            (upload_dir/blob).write_bytes(content)
            import hashlib
            document=save_record(conn,'documents',{'title':'委托合同（演示说明）','category':'委托合同','status':'active','demo':True,
                'files':[{'blob':blob,'filename':'委托合同-演示说明.txt','size':len(content),'sha256':hashlib.sha256(content).hexdigest(),'uploaded_at':now(),'uploaded_by':actor['name']}]},actor['id'],case_id=case['id'])
            contract=save_record(conn,'contracts',{'title':'远景科技委托代理合同','number':'HT-DEMO-001','document_id':document['id'],'status':'stamping','demo':True,'history':[]},actor['id'],case_id=case['id'])
            seal=save_record(conn,'seals',{'title':'远景科技委托代理合同','seal_type':'合同专用章','copies':2,'recipient':client,'reason':'委托代理合同签署（演示）',
                'document_id':document['id'],'contract_id':contract['id'],'status':'submitted','demo':True,'applicant_name':lawyers[lead]['name'],'history':[]},actor['id'],case_id=case['id'])
            contract['seal_id']=seal['id'];save_record(conn,'contracts',contract,actor['id'],record_id=contract['id'])
    audit(conn,actor,'载入本地演示数据','cases',detail='8 件虚构案件；不涉及源库业务数据')
