<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from '../api'
import { Copy, KeyRound, LoaderCircle, Plus, RefreshCw, ShieldOff } from 'lucide-vue-next'

const items=ref<any[]>([]), busy=ref(false), loading=ref(false), error=ref(''), name=ref(''), expires=ref(30), issued=ref<any>(null), copied=ref(false)
async function load(){loading.value=true;error.value='';try{items.value=(await api('/mcp/tokens')).items||[]}catch(e:any){error.value=e.message}finally{loading.value=false}}
async function create(){if(busy.value)return;busy.value=true;error.value='';issued.value=null;copied.value=false;try{issued.value=await post('/mcp/tokens',{name:name.value,expires_in_days:Number(expires.value)});name.value='';await load()}catch(e:any){error.value=e.message}finally{busy.value=false}}
async function revoke(item:any){if(!confirm(`确定撤销“${item.name}”吗？撤销后 MCP 客户端将立即无法访问。`))return;try{await api(`/mcp/tokens/${item.id}`,{method:'DELETE'});await load()}catch(e:any){error.value=e.message}}
async function copy(){if(!issued.value?.token)return;await navigator.clipboard.writeText(issued.value.token);copied.value=true}
function date(value:string){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'}
onMounted(load)
</script>
<template>
  <section class="panel api-key-panel">
    <div class="panel-heading"><div><h2>API Key 管理</h2><p>为 MCP 案件查询创建只读访问令牌。Key 只在创建成功时显示一次。</p></div><button class="btn" :disabled="loading" @click="load"><RefreshCw :size="15"/>刷新</button></div>
    <div class="panel-body api-key-create"><form class="form-grid" @submit.prevent="create"><label class="form-field"><span>Key 名称 *</span><input v-model="name" required maxlength="100" placeholder="例如：案件查询客户端"></label><label class="form-field"><span>有效期</span><select v-model.number="expires"><option :value="7">7 天</option><option :value="30">30 天</option><option :value="60">60 天</option><option :value="90">90 天</option></select></label><button class="btn primary" :disabled="busy"><LoaderCircle v-if="busy" class="spin" :size="15"/><Plus v-else :size="15"/>创建 API Key</button></form></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div v-if="issued" class="notice api-key-issued"><KeyRound :size="20"/><div><strong>API Key 已创建，请立即复制保存</strong><p>出于安全原因，关闭此页面后无法再次查看完整 Key。</p><div class="api-key-value"><code>{{ issued.token }}</code><button class="btn small" @click="copy"><Copy :size="14"/>{{ copied?'已复制':'复制 Key' }}</button></div><small>有效期至 {{ date(issued.expires_at) }}</small></div></div>
    <div class="table-wrap"><table><thead><tr><th>名称</th><th>创建时间</th><th>有效期至</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td><span class="person"><span class="icon-tile small"><KeyRound :size="15"/></span><strong>{{ item.name }}</strong></span></td><td>{{ date(item.created_at) }}</td><td>{{ date(item.expires_at) }}</td><td><span v-if="item.revoked_at" class="muted">已撤销</span><span v-else class="status-dot">有效</span></td><td><button v-if="!item.revoked_at" class="text-button danger" @click="revoke(item)"><ShieldOff :size="14"/>撤销</button></td></tr></tbody></table></div><p v-if="!loading&&!items.length" class="compact-empty">暂无 API Key，请创建一个用于 MCP 客户端连接。</p>
  </section>
</template>
