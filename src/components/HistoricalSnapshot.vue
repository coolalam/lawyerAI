<script setup lang="ts">
import { ref,watch } from 'vue'
import { api } from '../api'
const props=defineProps<{record:any}>()
const snapshot=ref<any>({items:[],total:0,page:1}),loading=ref(false),error=ref('')
let request=0
async function load(page=1){const seq=++request;loading.value=true;error.value='';try{const value=await api(`/conflicts/${props.record.id}/snapshot?page=${page}`);if(seq===request)snapshot.value=value}catch(e:any){if(seq===request)error.value=e.message}finally{if(seq===request)loading.value=false}}
const labels:Record<string,string>={csm_search_date:'检索时间',csm_result:'原审批结果编码',csm_description:'审查说明',csm_check_info:'审批意见',csm_result2:'复核结果编码',csm_check_info2:'复核意见',ca_csm1_result:'原审批结果编码',ca_csm1_result0:'原复核结果编码',ca_csm1_description:'审查说明',ca_conflictor:'原审查人',ca_check_date:'审查日期'}
watch(()=>props.record.id,()=>{snapshot.value={items:[],total:0,page:1};load()},{immediate:true})
</script>
<template>
  <div class="notice">这是原系统保存的历史检索快照，不会重新计算或自动作为当前立案审批依据。</div>
  <p v-if="error" class="error-banner">{{ error }}</p><p v-if="loading" class="muted">正在读取历史快照…</p>
  <div v-for="(label,key) in labels" :key="key"><div v-if="snapshot.summary?.[key]" class="key-value"><span>{{ label }}</span><span style="white-space:pre-wrap;overflow-wrap:anywhere">{{ snapshot.summary[key] }}</span></div></div>
  <p class="muted">共 {{ snapshot.total }} 条原始检索明细</p>
  <article v-for="(r,i) in snapshot.items" :key="i" class="conflict-match"><div><strong>{{ r.name||'未记录主体名称' }}</strong><p>{{ r.relation||'未记录关系' }} · {{ r.case_title||'未记录案件名称' }}</p><small>原案号：{{ r.case_number||'—' }} · 明细序号：{{ r.line }}</small><p v-if="r.query">检索条件：{{ r.query }}</p><p v-if="r.notes">{{ r.notes }}</p><small>原状态：{{ r.source_status||'—' }}</small></div></article>
  <p v-if="!loading&&!snapshot.total" class="muted">源记录没有对应的检索明细；已保留的审批摘要显示在上方。</p>
  <div v-if="snapshot.total>20" class="pagination"><button class="btn small" :disabled="loading||snapshot.page<=1" @click="load(snapshot.page-1)">上一页</button><span>第 {{ snapshot.page }} 页</span><button class="btn small" :disabled="loading||snapshot.page*20>=snapshot.total" @click="load(snapshot.page+1)">下一页</button></div>
</template>
