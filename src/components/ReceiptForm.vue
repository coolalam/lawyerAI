<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api, currency, patch, post } from '../api'
import SchemaForm from './SchemaForm.vue'
const props = defineProps<{ record?: any; invoice?: any; caseId?: string; fields: any[]; lawyers: any[] }>()
const emit = defineEmits<{ saved: [record: any]; cancel: [] }>()
const form = reactive<Record<string, any>>({}), invoice = ref<any>(null), choices = ref<any[]>([]), query = ref(''), page = ref(1), total = ref(0)
const busy = ref(false), loading = ref(true), searching = ref(false), error = ref(''), searchError = ref(''), thirdParty = ref(false), record = ref<any>(null)
const fields = computed(() => props.fields.filter(field => !['invoice_id', 'payer', 'plan_id'].includes(field.key)))
let searchSequence = 0, selectionSequence = 0
async function search(reset = true) {
  if (reset) page.value = 1
  const sequence = ++searchSequence; searching.value = true; searchError.value = ''
  try {
    const result = await api(`/finance/receipt-invoices?${new URLSearchParams({ q: query.value.trim(), case_id: props.caseId || record.value?.case_id || '', page: String(page.value) })}`)
    if (sequence === searchSequence) { choices.value = result.items; total.value = result.total }
  } catch (e: any) { if (sequence === searchSequence) searchError.value = e.message }
  finally { if (sequence === searchSequence) searching.value = false }
}
async function selectInvoice(id: string, preserve = false) {
  const sequence = ++selectionSequence; invoice.value = null; error.value = ''; loading.value = true
  try {
    const selected = await api(`/records/invoices/${encodeURIComponent(id)}`)
    const matter = await api(`/records/cases/${encodeURIComponent(selected.case_id)}`)
    if (sequence !== selectionSequence) return
    invoice.value = { ...selected, case_number: matter.number, case_title: matter.title }
    form.invoice_id = selected.id
    if (!preserve) {
      form.payer = selected.title || matter.client; form.title = `${selected.number || selected.title}到账`; thirdParty.value = false
    } else thirdParty.value = form.payer !== (selected.title || matter.client)
  } catch (e: any) { if (sequence === selectionSequence) error.value = e.message }
  finally { if (sequence === selectionSequence) loading.value = false }
}
function payerMode() { if (!thirdParty.value && invoice.value) form.payer = invoice.value.title }
async function initialize() {
  loading.value = true; error.value = ''
  try {
    const current = props.record ? await api(`/records/receipts/${props.record.id}`) : null
    record.value = current
    for (const field of props.fields) form[field.key] = current?.[field.key] ?? field.default ?? ''
    if (!current) form.received_on = new Date().toLocaleDateString('sv-SE')
    if (current?.invoice_id || props.invoice?.id) await selectInvoice(current?.invoice_id || props.invoice.id, !!current)
    await search()
  } catch (e: any) { error.value = e.message }
  finally { loading.value = false }
}
async function save() {
  if (busy.value || loading.value || !invoice.value) return
  busy.value = true; error.value = ''
  try {
    const data = { ...form, case_id: invoice.value.case_id, invoice_id: invoice.value.id }
    const saved = record.value ? await patch(`/records/receipts/${record.value.id}`, { ...data, version: record.value.version }) : await post('/records/receipts', data)
    emit('saved', saved)
  } catch (e: any) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(initialize)
</script>
<template>
  <form @submit.prevent="save">
    <div class="modal-body">
      <div class="notice">选择已开票申请，案件和付款方自动带入。填写实际到账金额后保存，再确认到账。</div>
      <div v-if="error" class="error-banner" role="alert">{{ error }} <button v-if="!record && !invoice" type="button" class="text-button" @click="initialize">重试</button></div>
      <label class="form-field mb"><span>搜索开票申请</span><div class="inline-actions"><input v-model="query" placeholder="发票号码或抬头" @keydown.enter.prevent="search()"><button type="button" class="btn" :disabled="searching || busy" @click="search()">搜索</button></div></label>
      <p v-if="searchError" class="error-banner">{{ searchError }}</p>
      <label class="form-field mb"><span>关联开票申请 *</span><select :value="invoice?.id || ''" :disabled="busy || loading || searching" required @change="selectInvoice(($event.target as HTMLSelectElement).value)"><option value="" disabled>请选择已开票申请</option><option v-if="invoice && !choices.some(row=>row.id===invoice.id)" :value="invoice.id">{{ invoice.number || '未编号' }} · {{ invoice.title }}</option><option v-for="row in choices" :key="row.id" :value="row.id">{{ row.number || '未编号' }} · {{ row.title }} · {{ row.case_number }} · {{ currency(row.amount,row.currency) }}</option></select></label>
      <div v-if="total>50" class="pagination"><span>共 {{ total }} 张 · 第 {{ page }} 页</span><button type="button" class="btn small" :disabled="page<=1 || searching" @click="page--;search(false)">上一页</button><button type="button" class="btn small" :disabled="page*50>=total || searching" @click="page++;search(false)">下一页</button></div>
      <p v-if="loading" role="status">正在加载关联信息…</p>
      <div v-if="invoice" class="notice"><div><strong>所属案件：{{ invoice.case_number }} · {{ invoice.case_title }}</strong><p>发票抬头：{{ invoice.title }} · 开票金额：{{ currency(invoice.amount,invoice.currency) }}</p></div></div>
      <label class="form-field mb"><span>付款方 *</span><input v-model="form.payer" :readonly="!thirdParty" required></label>
      <label class="inline-check mb"><input v-model="thirdParty" type="checkbox" @change="payerMode">第三方代付 / 修改付款方名称</label>
      <SchemaForm :fields="fields" :model="form" :lawyers="lawyers"/>
    </div>
    <footer><button type="button" class="btn" :disabled="busy" @click="emit('cancel')">取消</button><button class="btn primary" :disabled="busy || loading || !invoice">{{ busy ? '正在保存…' : '保存到账登记' }}</button></footer>
  </form>
</template>
