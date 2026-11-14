"""Resolve legacy reference codes without replacing source values or manual edits."""


def text(value):
    return '' if value is None else str(value).strip()


class References:
    def __init__(self, conn):
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.departments={}
        self.causes={}
        self.interface_causes={}
        self.branches={}
        self.department_branches={}
        if 'src__t_suboffice_info' in tables:
            for r in conn.execute('SELECT soi_office_id,soi_area,soi_name FROM src__t_suboffice_info'):
                self.branches[(text(r[0]),text(r[1]))]=text(r[2])
        if 'src__t_department' in tables:
            for row in conn.execute('SELECT * FROM src__t_department'):
                r=dict(row);key=(text(r['dp_office_id']),text(r['dp_dept_id']))
                self.departments[key]=text(r['dp_description'])
                self.department_branches[key]=text(r.get('dp_area'))
        if 'src__t_case_class' in tables:
            self.causes={text(r[0]):text(r[1]) for r in conn.execute('SELECT cc_case_class,cc_class_name FROM src__t_case_class')}
        if 'src__t_interface_reason' in tables:
            values={}
            for code,name in conn.execute('SELECT itr_code,itr_name FROM src__t_interface_reason'):
                values.setdefault(text(code),set()).add(text(name))
            self.interface_causes={k:next(iter(v)) for k,v in values.items() if len(v)==1}
        self.source_departments = dict(self.departments)
        self.source_causes = dict(self.causes)
        self.source_interface_causes = dict(self.interface_causes)
        self.department_ids = {}
        if 'managed_departments' in tables:
            for r in conn.execute('SELECT * FROM managed_departments'):
                self.departments[(text(r['office']),text(r['code']))] = text(r['name'])
                self.department_ids[r['id']] = dict(r)
                if dict(r).get('branch_code') is not None:
                    self.department_branches[(text(r['office']),text(r['code']))]=text(r['branch_code'])
        if 'managed_metadata' in tables:
            # Disabled entries still label historical records, but are excluded from new selections.
            for r in conn.execute("SELECT category,code,name FROM managed_metadata WHERE category IN ('case_cause','case_cause_interface')"):
                if r['category']=='case_cause': self.causes[text(r['code'])]=text(r['name'])
                else: self.interface_causes[text(r['code'])]=text(r['name'])

    def department(self, office, code):
        return self.departments.get((text(office),text(code))) or self.departments.get(('default',text(code))) or text(code)

    def branch_options(self):
        return [dict(office=office,code=code,name=name) for (office,code),name in self.branches.items()]

    def enrich_department(self, item):
        office=text(item.get('office'))
        code=self.department_branches.get((office,text(item.get('code'))),'')
        item.update(branch_code=code,branch_name=self.branches.get((office,code),code or office))
        return item

    def cause(self, raw, source=False):
        causes = self.source_causes if source else self.causes
        interface = self.source_interface_causes if source else self.interface_causes
        codes=[text(raw.get(k)) for k in ('ca_reason','ca_reason_type','ca_reason_type1')]
        label=next((causes[c] for c in reversed(codes) if c in causes),'')
        extra=text(raw.get('ca_reasonBC'))
        if extra:
            extra=causes.get(extra) or interface.get(extra) or extra
        return '；'.join(dict.fromkeys(x for x in (label,extra) if x)) or next((c for c in reversed(codes) if c),'')

    def enrich(self, item):
        raw=item.get('legacy_data') or {}
        if item.get('legacy_table')=='t_case':
            code=text(raw.get('ca_dept_id'))
            original = self.source_departments.get((text(raw.get('ca_office_id')),code),code)
            if item.get('department') in (code,original):
                item['department']=self.department(raw.get('ca_office_id'),code)
            old=text(raw.get('ca_reasonBC') or raw.get('ca_reason'))
            if item.get('cause') in (old,self.cause(raw,source=True)):
                item['cause']=self.cause(raw)
            item['department_code']=code
            item['cause_codes']={k:raw.get(k) for k in ('ca_reason','ca_reason_type','ca_reason_type1','ca_reasonBC')}
        elif item.get('legacy_table')=='t_employee':
            item['employee_id']=text(raw.get('emp_empl_id'))
            # Older projections missed the source dictionary's X (离职) without a leave date.
            # Only correct untouched imported records; preserve subsequent manual edits.
            if item.get('version')==1 and text(raw.get('emp_status'))=='X' and item.get('status')=='active':
                item['status']='inactive'
            code=text(raw.get('emp_dept_id'))
            original = self.source_departments.get((text(raw.get('emp_office_id')),code),code)
            if item.get('department') in (code,original):
                item['department']=self.department(raw.get('emp_office_id'),code)
            item['department_code']=code
        if item.get('department_id') in self.department_ids:
            department = self.department_ids[item['department_id']]
            item.update(office=department['office'],department=department['name'],department_code=department['code'])
        if item.get('kind')=='lawyers' or item.get('legacy_table')=='t_employee' or item.get('department_id') in self.department_ids:
            office=text(item.get('office'))
            department_branch=self.department_branches.get((office,text(item.get('department_code'))),'')
            if item.get('department_id') in self.department_ids:
                branch=department_branch
            else:
                branch=text(item.get('branch_code'))
                if not branch and office==text(raw.get('emp_office_id')):
                    branch=text(raw.get('emp_area'))
                branch=branch or department_branch
            item.update(branch_code=branch,branch_name=self.branches.get((office,branch),branch or office))
        if item.get('cause_code'):
            item['cause'] = self.causes.get(item['cause_code'],item.get('cause'))
        return item
