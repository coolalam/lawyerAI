<script setup lang="ts">
import { onMounted, ref, reactive, nextTick } from 'vue'
import { api, patch, post } from '../api'
import PasswordResetAdmin from './PasswordResetAdmin.vue'
import LawyerTeam from './LawyerTeam.vue'

const emit = defineEmits(['changed'])
const tab = ref('members')
const loading = ref(false), saving = ref(false), error = ref(''), editorError = ref('')
const rows = ref<any[]>([]), departments = ref<any[]>([]), editing = ref<any>(null)
const branches = ref<any[]>([])
const branchChoice=ref(''),memberBranchChoice=ref('')
const form = reactive<any>({}), memberForm = reactive<any>({})
const accountForm = reactive({username:'', role:'lawyer'})
const roles:Record<string,string> = {admin:'系统管理员',partner:'合伙人',lawyer:'律师',finance:'财务',clerk:'行政'}
let sequence = 0
async function loadChoices() {
  let next=1, items:any[]=[], count=1
  while(items.length<count){const r=await api(`/admin/departments?page=${next++}&page_size=200`);items.push(...r.items);count=r.total;if(!r.items.length)break}
  departments.value=items
}
async function load() {
  const seq=++sequence; loading.value=true;error.value=''
  try {
    let items:any[]=[],next=1,count=1
    while(items.length<count){
      const r=await api(`/admin/members?page=${next++}&page_size=200`)
      if(seq!==sequence)return
      items.push(...r.items);count=r.total;if(!r.items.length)break
    }
    rows.value=items
  } catch(e:any){if(seq===sequence)error.value=e.message} finally{if(seq===sequence)loading.value=false}
}
async function reveal(){editorError.value='';await nextTick();document.getElementById('organization-editor')?.scrollIntoView({block:'start',behavior:'smooth'})}
function editDepartment(row?:any){tab.value='departments';editing.value=row||{};branchChoice.value=String(branches.value.findIndex(b=>b.office===row?.office&&b.code===row?.branch_code));Object.assign(form,{office:row?.office||'',branch_code:row?.branch_code||'',code:row?.code||'',name:row?.name||'',active:row?.active!==0,sort_order:row?.sort_order||0});reveal()}
function chooseBranch(target:any,value:string){const branch=branches.value[Number(value)];target.branch_code=branch?.code||'';target.office=branch?.office||''}
function selectAccount(id:string){const account=editing.value?.accounts?.find((a:any)=>a.id===id);Object.assign(memberForm,{account_id:id,account_role:account?.role||'lawyer',account_active:account?.active!==0})}
function editMember(row?:any){
  tab.value='members';memberBranchChoice.value='-1'
  editing.value=row||{};const dept=departments.value.find(d=>d.office===row?.office&&d.code===row?.department_code)
  Object.assign(memberForm,{name:row?.name||'',office:row?.office||'',branch_code:row?.branch_code||'',department_id:dept?.id||'',original_department_id:dept?.id||'',title:row?.title||'',phone:row?.phone||'',email:row?.email||'',status:row?.status||'active'})
  selectAccount(row?.accounts?.[0]?.id||'');Object.assign(accountForm,{username:row?.employee_id||'',role:'lawyer'});reveal()
}
async function saveDepartment(){
  if(saving.value)return;saving.value=true;editorError.value=''
  try{if(editing.value.id)await patch(`/admin/departments/${editing.value.id}`,form);else await post('/admin/departments',form);editing.value=null;await loadChoices();await load();emit('changed')}
  catch(e:any){editorError.value=e.message}finally{saving.value=false}
}
async function saveMember(){
  if(saving.value)return;saving.value=true;editorError.value=''
  try{
    const {name,title,phone,email,office,department_id,status}=memberForm
    if(editing.value.id){
      await patch(`/admin/members/${editing.value.id}`,{name,title,phone,email,...(department_id!==memberForm.original_department_id?{department_id}:{}),status,version:editing.value.version,
        ...(memberForm.account_id?{account:{id:memberForm.account_id,role:memberForm.account_role,active:memberForm.account_active}}:{})})
    }else{
      const dept=departments.value.find(d=>d.id===department_id)
      await post('/records/lawyers',{name,title,phone,email,office:dept?.office||office,department:dept?.name||'',department_id,branch_code:dept?.branch_code||memberForm.branch_code||''})
    }
    editing.value=null;await load();emit('changed')
  }catch(e:any){editorError.value=e.message}finally{saving.value=false}
}
async function createAccount(){
  if(saving.value)return;saving.value=true;editorError.value=''
  try{
    await post('/admin/users',{...accountForm,name:editing.value.name,lawyer_id:editing.value.id,reset_required:true})
    const r=await api(`/admin/members?q=${encodeURIComponent(editing.value.employee_id||editing.value.name)}&page_size=200`)
    const fresh=r.items.find((x:any)=>x.id===editing.value.id);if(fresh)editMember(fresh)
    await load();emit('changed')
  }catch(e:any){editorError.value=e.message}finally{saving.value=false}
}
onMounted(async()=>{try{const schema=await api('/schema');branches.value=schema.schemas.lawyers.branches||[];await loadChoices();await load()}catch(e:any){error.value=e.message}})
</script>

<template>
  <section class="panel admin-reference-panel" :aria-busy="loading">
    <div class="panel-heading"><div><h2>部门与成员管理</h2><p>选择左侧分支机构或部门，查看与管理所属成员</p></div><div class="inline-actions"><button class="btn" @click="editDepartment()">新增部门</button><button class="btn primary" @click="editMember()">新增成员</button></div></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="loading" class="hint">正在加载部门与成员…</p>
    <LawyerTeam :members="rows" :departments="departments" :branches="branches" :states="{active:'在职',inactive:'离职'}" manage @open="editMember" @edit-department="editDepartment"/>
  </section>
  <div v-if="editing" id="organization-editor" class="inline-editor">
    <section class="panel"><div class="panel-heading"><h3>{{ editing.id?'编辑':'新增' }}{{ tab==='departments'?'部门':'成员' }}</h3><button class="icon-button" :disabled="saving" aria-label="关闭编辑" @click="editing=null">×</button></div>
      <p v-if="editorError" class="error-banner" role="alert">{{ editorError }}</p>
      <form v-if="tab==='departments'" class="panel-body form-grid" @submit.prevent="saveDepartment">
        <label v-if="branches.length" class="form-field"><span>分支机构</span><select v-model="branchChoice" :disabled="!!editing.id" @change="chooseBranch(form,branchChoice)"><option value="-1">其他机构 / 未分配分支机构</option><option v-for="(b,i) in branches" :key="i" :value="String(i)">{{ b.name }}（{{ b.code }}）</option></select></label>
        <label class="form-field"><span>{{ branches.length?'所属机构编号 *':'分所 *' }}</span><input v-model="form.office" :disabled="!!editing.id||branchChoice!=='-1'" required></label>
        <label class="form-field"><span>部门编码 *</span><input v-model="form.code" :disabled="!!editing.id" required></label>
        <label class="form-field"><span>部门名称 *</span><input v-model="form.name" required></label>
        <label class="form-field"><span>排序</span><input v-model.number="form.sort_order" type="number" step="1"></label>
        <label class="inline-check"><input type="checkbox" v-model="form.active">启用</label><button class="btn primary" :disabled="saving">保存部门</button>
      </form>
      <form v-else class="panel-body form-grid" @submit.prevent="saveMember">
        <label class="form-field"><span>姓名 *</span><input v-model="memberForm.name" required></label>
        <label class="form-field"><span>部门</span><select v-model="memberForm.department_id"><option value="">{{ editing.id?'保持原部门':'未分配部门' }}</option><option v-for="d in departments" :key="d.id" :value="d.id" :disabled="!d.active&&d.id!==editing.department_id">{{ d.branch_name||d.office }} · {{ d.name }}（{{ d.code }}）{{ d.active?'':' · 已停用' }}</option></select></label>
        <label v-if="!editing.id&&!memberForm.department_id&&branches.length" class="form-field"><span>分支机构（未分配部门时）</span><select v-model="memberBranchChoice" @change="chooseBranch(memberForm,memberBranchChoice)"><option value="-1">其他机构 / 未分配分支机构</option><option v-for="(b,i) in branches" :key="i" :value="String(i)">{{ b.name }}（{{ b.code }}）</option></select></label>
        <label v-if="!editing.id&&!memberForm.department_id" class="form-field"><span>分所（未分配部门时）</span><input v-model="memberForm.office" :disabled="memberBranchChoice!=='-1'"></label>
        <label class="form-field"><span>职务</span><input v-model="memberForm.title"></label><label class="form-field"><span>电话</span><input v-model="memberForm.phone"></label><label class="form-field"><span>邮箱</span><input v-model="memberForm.email" type="email"></label>
        <label v-if="editing.id" class="form-field"><span>员工状态</span><select v-model="memberForm.status"><option value="active">在职</option><option value="inactive">离职</option></select></label>
        <template v-if="editing.accounts?.length">
          <label class="form-field"><span>管理账号</span><select :value="memberForm.account_id" @change="selectAccount(($event.target as HTMLSelectElement).value)"><option v-for="a in editing.accounts" :key="a.id" :value="a.id">{{ a.username }}</option></select></label>
          <label class="form-field"><span>账号角色</span><select v-model="memberForm.account_role"><option v-for="(label,key) in roles" :key="key" :value="key">{{ label }}</option></select></label><label class="inline-check"><input type="checkbox" v-model="memberForm.account_active">账号启用</label>
        </template>
        <p v-if="editing.id" class="hint">员工状态与登录权限分别管理。离职时请同时停用对应账号。</p><button class="btn primary" :disabled="saving">保存成员</button>
      </form>
    </section>
    <section v-if="tab==='members'&&editing.id&&!editing.accounts?.length" class="panel"><div class="panel-heading"><h3>为此成员创建登录账号</h3></div><form class="panel-body form-grid" @submit.prevent="createAccount"><label class="form-field"><span>登录账号 *</span><input v-model="accountForm.username" required></label><label class="form-field"><span>账号角色</span><select v-model="accountForm.role"><option v-for="(label,key) in roles" :key="key" :value="key">{{ label }}</option></select></label><p class="hint">创建后需核验身份并签发重置码，由成员自行设置密码。</p><button class="btn primary" :disabled="saving">创建登录账号</button></form></section>
    <PasswordResetAdmin v-if="tab==='members'&&editing.accounts?.length" :key="editing.id" :users="editing.accounts"/>
  </div>
</template>
