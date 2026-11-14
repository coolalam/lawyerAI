<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { organizationGroups } from '../trees'
import StatusBadge from './StatusBadge.vue'
import { Building2, ChevronRight, ChevronDown, Search, Users } from 'lucide-vue-next'

const props=defineProps<{members:any[];departments:any[];states:Record<string,string>;branches?:any[];manage?:boolean}>()
const emit=defineEmits(['open','editDepartment'])
const roles:Record<string,string>={admin:'系统管理员',partner:'合伙人',lawyer:'律师',finance:'财务',clerk:'行政'}
const selected=ref('all'), query=ref(''), status=ref(''), departmentSearch=ref(''), page=ref(1), pageSize=20
const expanded=reactive<Record<string,boolean>>({})
const tree=computed(()=>organizationGroups(props.members,props.departments,props.branches))
const visibleTree=computed(()=>{
  const term=departmentSearch.value.trim().toLowerCase()
  return tree.value.map(o=>({...o,children:o.children.filter((d:any)=>(o.name+' '+o.branch_code+' '+d.name+' '+d.code).toLowerCase().includes(term))})).filter(o=>o.children.length||(o.name+' '+o.branch_code).toLowerCase().includes(term))
})
const selection=computed(()=>{
  if(selected.value==='all')return {name:'全部成员',members:props.members}
  for(const office of tree.value){if(office.key===selected.value)return {name:office.name,members:office.children.flatMap((d:any)=>d.members)};for(const dept of office.children)if(dept.key===selected.value)return dept}
  return {name:'全部成员',members:props.members}
})
const filtered=computed(()=>selection.value.members.filter((r:any)=>(!status.value||r.status===status.value)&&(!query.value.trim()||[r.name,r.employee_id,r.branch_name,r.branch_code,r.department,r.department_code,r.title,r.phone,r.email,...(r.accounts||[]).map((a:any)=>a.username)].join(' ').toLowerCase().includes(query.value.trim().toLowerCase()))))
const paged=computed(()=>filtered.value.slice((page.value-1)*pageSize,page.value*pageSize))
watch([selected,query,status],()=>{page.value=1})
watch(()=>props.members,()=>{page.value=1})
const officeCount=(office:any)=>office.children.reduce((n:number,d:any)=>n+d.members.length,0)
</script>
<template>
  <section class="panel lawyer-team-panel">
    <div class="lawyer-team-layout">
      <aside class="lawyer-team-tree" aria-label="部门树">
        <div class="team-tree-search"><input v-model="departmentSearch" aria-label="搜索分支机构或部门" placeholder="搜索分支机构、部门或编码"></div>
        <button class="team-tree-all" :class="{active:selected==='all'}" :aria-pressed="selected==='all'" @click="selected='all'"><Users :size="15"/>全部成员 <small>{{ members.length }}</small></button>
        <div v-for="office in visibleTree" :key="office.key" class="team-tree-branch">
          <div class="team-tree-office"><button class="tree-toggle-button" :aria-label="(expanded[office.key]===false?'展开':'收起')+office.name" :aria-expanded="expanded[office.key]!==false" @click="expanded[office.key]=expanded[office.key]===false"><component :is="expanded[office.key]===false?ChevronRight:ChevronDown" :size="15"/></button><button class="team-tree-node" :class="{active:selected===office.key}" :aria-pressed="selected===office.key" @click="selected=office.key"><Building2 :size="15"/><span class="team-department-label"><strong>{{ office.name }}</strong><small v-if="office.branch_code">{{ office.branch_code }}</small></span><small>{{ officeCount(office) }}</small></button></div>
          <div v-if="expanded[office.key]!==false||departmentSearch" class="team-tree-children">
            <button v-for="dept in office.children" :key="dept.key" class="team-tree-node department" :class="{active:selected===dept.key}" :aria-pressed="selected===dept.key" @click="selected=dept.key"><span class="team-department-label">{{ dept.name }}<small>{{ dept.code }}{{ dept.department?.active===0?' · 停用':'' }}</small></span><small>{{ dept.members.length }}</small></button>
          </div>
        </div><p v-if="!visibleTree.length" class="hint">没有匹配部门</p>
      </aside>
      <div class="lawyer-team-members">
        <div class="team-members-heading"><div><small v-if="selection.branch_name">{{ selection.branch_name }}</small><h2>{{ selection.name }}</h2></div><div class="inline-actions"><StatusBadge v-if="selection.department" :status="selection.department.active===0?'void':'active'" :label="selection.department.active===0?'部门停用':'部门启用'"/><span>{{ filtered.length }} 人</span><button v-if="manage&&selection.department" class="text-button" @click="emit('editDepartment',selection.department)">编辑部门</button></div></div>
        <div class="table-toolbar team-search"><div class="search-box"><Search :size="16"/><input v-model="query" :aria-label="manage?'搜索部门或成员':'搜索律师团队'" placeholder="搜索姓名、员工编号或联系方式"></div><select v-model="status" aria-label="筛选团队状态"><option value="">全部状态</option><option v-for="(label,code) in states" :key="code" :value="code">{{ label }}</option></select></div>
        <div class="table-wrap"><table><thead><tr><th>成员 / 编号</th><th>分支机构 / 部门</th><th>{{ manage?'登录账号 / 权限':'联系方式' }}</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="row in paged" :key="row.id"><td><span class="person"><span class="avatar tiny">{{ row.name?.slice(-2) }}</span><strong>{{ row.name||'未命名' }}</strong></span><small>{{ row.employee_id||'本地成员' }} · {{ row.title||'未设置职务' }}</small></td><td>{{ row.branch_name||row.office||'未分配分支机构' }}<small>{{ row.department||'未分配部门' }}</small></td><td v-if="manage"><div v-for="a in row.accounts" :key="a.id">{{ a.username }}<small>{{ roles[a.role] }} · {{ a.active?'启用':'停用' }}{{ a.needs_password_reset?' · 待设置密码':'' }}</small></div><span v-if="!row.accounts?.length">未建账号</span></td><td v-else>{{ row.phone||'—' }}<small>{{ row.email }}</small></td><td><StatusBadge :status="row.status" :label="states[row.status]||row.status"/></td><td><button class="text-button" @click="emit('open',row)">{{ manage?'编辑':'查看' }}</button></td></tr></tbody></table></div>
        <p v-if="!filtered.length" class="compact-empty">当前机构或部门暂无匹配成员</p>
        <div class="pagination"><span>共 {{ filtered.length }} 人 · 第 {{ page }} 页</span><button class="btn small" :disabled="page<=1" @click="page--">上一页</button><button class="btn small" :disabled="page*pageSize>=filtered.length" @click="page++">下一页</button></div>
      </div>
    </div>
  </section>
</template>
