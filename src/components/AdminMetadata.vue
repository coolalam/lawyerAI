<script setup lang="ts">
import { onMounted, ref, reactive, watch, nextTick, computed } from 'vue'
import { api, patch, post } from '../api'
import StatusBadge from './StatusBadge.vue'
import MetadataTreeNode from './MetadataTreeNode.vue'
import { metadataForest } from '../trees'
const emit=defineEmits(['changed'])
const categories=ref<any[]>([]), category=ref('case_cause'), q=ref(''), rows=ref<any[]>([]), total=ref(0), page=ref(1), pageSize=50
const loading=ref(false), saving=ref(false), editing=ref<any>(null), error=ref(''), editorError=ref(''), categorySearch=ref('')
const treeSource=ref<any[]>([])
const form=reactive<any>({category:'case_cause',code:'',parent_code:'',name:'',active:true,sort_order:0,notes:''})
const labels:Record<string,string>={case_cause:'案由',case_cause_interface:'接口案由',case_status:'案件状态',case_category:'案件类型',case_side:'代理立场',conflict_status:'利冲状态',invoice_status:'开票状态',payment_status:'到账状态',seal_status:'用印状态',department_status:'部门状态',employee_status:'员工状态',contract_status:'合同状态',document_status:'文档状态',payment_plan_status:'收款计划状态',task_status:'任务状态'}
const categoryLabel=(key:string)=>labels[key]||categories.value.find(x=>x.category===key)?.label||key
let sequence=0
const treeCategory = computed(() => category.value === 'case_cause' || category.value === 'case_cause_interface')
const treeItems = computed(() => {
  const all=treeSource.value, term=q.value.trim().toLowerCase()
  if (!term) return all
  const keep=new Set<string>(), byCode=new Map(all.map(x=>[x.code,x]))
  for(const item of all){if(`${item.code} ${item.name} ${item.parent_code||''}`.toLowerCase().includes(term)){let cur=item;while(cur){if(keep.has(cur.id))break;keep.add(cur.id);cur=byCode.get(cur.parent_code)}}}
  return all.filter(x=>keep.has(x.id))
})
const metadataTree = computed(() => metadataForest(treeItems.value))
const expanded=reactive<Record<string,boolean>>({})
function toggle(event:{id:string;open:boolean}){expanded[event.id]=event.open}
async function loadCategories(){categories.value=await api('/admin/metadata/categories')}
async function load(){
  const seq=++sequence,selectedCategory=category.value;loading.value=true;error.value=''
  try{
    if(treeCategory.value){
      let all:any[]=[],p=1,totalCount=1
      while(all.length<totalCount){
        const r=await api(`/admin/metadata?category=${encodeURIComponent(selectedCategory)}&page=${p++}&page_size=200`)
        if(seq!==sequence)return
        all.push(...r.items);totalCount=r.total;if(!r.items.length)break
      }
      if(seq===sequence){treeSource.value=all;rows.value=all;total.value=all.length}
    }else{
      const r=await api(`/admin/metadata?category=${encodeURIComponent(selectedCategory)}&q=${encodeURIComponent(q.value)}&page=${page.value}&page_size=${pageSize}`)
      if(seq===sequence){treeSource.value=[];rows.value=r.items;total.value=r.total}
    }
  }catch(e:any){if(seq===sequence)error.value=e.message}finally{if(seq===sequence)loading.value=false}
}
async function edit(row?:any){editing.value=row||{};editorError.value='';Object.assign(form,{category:row?.category||category.value||'case_cause',code:row?.code||'',parent_code:row?.parent_code||'',name:row?.name||'',active:row?.active!==0,sort_order:row?.sort_order||0,notes:row?.notes||''});await nextTick();document.getElementById('metadata-editor')?.scrollIntoView({block:'start',behavior:'smooth'})}
async function save(){if(saving.value)return;saving.value=true;editorError.value='';try{if(editing.value?.id)await patch(`/admin/metadata/${editing.value.id}`,form);else await post('/admin/metadata',form);editing.value=null;await loadCategories();await load();emit('changed')}catch(e:any){editorError.value=e.message}finally{saving.value=false}}
watch(category,()=>{editing.value=null;rows.value=[];treeSource.value=[];page.value=1;load()})
watch(q,()=>{page.value=1;if(!treeCategory.value)load()})
watch(page,()=>{if(!treeCategory.value)load()})
onMounted(async()=>{try{await loadCategories();await load()}catch(e:any){error.value=e.message}})
</script>
<template>
  <section class="panel admin-reference-panel" :aria-busy="loading">
    <div class="panel-heading"><div><h2>元数据管理</h2><p>维护案由、案件状态及业务编码。停用项保留历史显示，不再供新记录选择。</p></div><button class="btn primary" @click="edit()">新增字典项</button></div>
    <div class="metadata-layout"><aside class="metadata-categories">
      <input v-model="categorySearch" aria-label="搜索字典类别" placeholder="搜索类别" class="category-search">
      <button :class="{active:!category}" @click="category=''">全部类别 <small>{{ categories.reduce((n,x)=>n+x.count,0) }}</small></button>
      <button v-for="item in categories.filter(x=>(categoryLabel(x.category)+' '+x.category).toLowerCase().includes(categorySearch.toLowerCase()))" :key="item.category" :class="{active:category===item.category}" @click="category=item.category"><span>{{ categoryLabel(item.category) }}</span><small>{{ item.count }}</small></button>
    </aside><div class="metadata-content"><div class="table-toolbar"><div class="search-box"><input v-model="q" aria-label="搜索字典项" placeholder="搜索编码、名称或父编码"/></div><span class="result-count">共 {{ total }} 条</span></div><p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <div class="notice margin-notice">状态字典用于显示名称；审批步骤仍由现有流程规则控制，新增编码不会自动新增审批节点。</div>
      <div v-if="treeCategory" class="tree-view metadata-tree" role="tree" :aria-label="categoryLabel(category)"><MetadataTreeNode v-for="node in metadataTree.slice((page-1)*pageSize,page*pageSize)" :key="node.id" :node="node" :expanded="expanded" :searching="!!q.trim()" @toggle="toggle" @edit="edit"/></div><div v-else class="table-wrap"><table><thead><tr><th>类别</th><th>编码 / 父编码</th><th>名称</th><th>状态</th><th>来源</th><th></th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{ categoryLabel(row.category) }}</td><td><code>{{ row.code }}</code><small v-if="row.parent_code">父级 {{ row.parent_code }}</small></td><td>{{ row.name }}</td><td><StatusBadge :status="row.active?'active':'void'" :label="row.active?'启用':'停用'"/></td><td>{{ row.source_table==='application'?'系统流程':row.source_table||'本地新增' }}</td><td><button class="text-button" @click="edit(row)">编辑</button></td></tr></tbody></table></div>
      <p v-if="!loading&&!(treeCategory?treeItems.length:rows.length)" class="compact-empty">此类别暂无匹配字典项，可新增或调整搜索条件</p>
      <div class="pagination"><span>{{ treeCategory?'按根节点分页，子节点随父级展开 · ':'' }}共 {{ treeCategory?treeItems.length:total }} 项 · 第 {{ page }} 页</span><button class="btn small" :disabled="loading||page<=1" @click="page--">上一页</button><button class="btn small" :disabled="loading||page*pageSize>=(treeCategory?metadataTree.length:total)" @click="page++">下一页</button></div>
    </div></div>
  </section>
  <div v-if="editing" id="metadata-editor" class="inline-editor"><section class="panel"><div class="panel-heading"><h3>{{ editing.id?'编辑字典项':'新增字典项' }}</h3><button class="icon-button" :disabled="saving" aria-label="关闭字典编辑" @click="editing=null">×</button></div><p v-if="editorError" class="error-banner" role="alert">{{ editorError }}</p>
    <form class="panel-body form-grid" @submit.prevent="save">
      <label class="form-field"><span>类别 *</span><input v-model="form.category" required :disabled="!!editing.id" list="metadata-category-list"><datalist id="metadata-category-list"><option v-for="c in categories" :key="c.category" :value="c.category">{{ categoryLabel(c.category) }}</option></datalist></label>
      <label class="form-field"><span>编码 *</span><input v-model="form.code" required :disabled="!!editing.id"></label><label class="form-field"><span>父编码</span><input v-model="form.parent_code"></label><label class="form-field"><span>显示名称 *</span><input v-model="form.name" required></label><label class="form-field"><span>排序</span><input v-model.number="form.sort_order" type="number" step="1"></label><label class="inline-check"><input type="checkbox" v-model="form.active">启用</label><label class="form-field wide"><span>备注</span><textarea v-model="form.notes" rows="2"></textarea></label><button class="btn primary" :disabled="saving">保存字典项</button>
    </form></section></div>
</template>
<style scoped>
.metadata-categories{max-height:650px;overflow-y:auto}.metadata-categories button span{color:inherit;font-size:11px}.category-search{width:100%;font-size:11px;margin-bottom:8px}
@media(max-width:760px){.metadata-categories{max-height:180px;flex-wrap:wrap}.category-search{flex-basis:100%}}
</style>
