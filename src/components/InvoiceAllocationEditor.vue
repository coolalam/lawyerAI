<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, currency } from '../api'
import { calculateAllocation, switchMethod, type AllocationPlan } from '../invoiceAllocation'
const props = defineProps<{ caseId: string; invoiceId?: string; amount: unknown; currencyCode?: string }>()
const emit = defineEmits<{ change: [plan: AllocationPlan | null, valid: boolean, version?: number, readOnly?: boolean] }>()
const plan = ref<AllocationPlan>({ method: 'ratio', rows: [] })
const loading = ref(false), error = ref(''), readOnly = ref(false), version = ref<number>(), source = ref('')
const loadedCurrency = ref('CNY')
const unit = computed(() => props.currencyCode || loadedCurrency.value)
let sequence = 0
const result = computed(() => calculateAllocation(plan.value, props.amount))
const displayRows = computed(() => readOnly.value ? plan.value.rows : result.value.rows)
async function load() {
  const request = ++sequence
  loading.value = true; error.value = ''; readOnly.value = false
  emit('change', null, false)
  if (!props.caseId) { loading.value = false; error.value = '请先选择所属案件'; return }
  try {
    const data = await api(`/cases/${encodeURIComponent(props.caseId)}/invoice-allocations${props.invoiceId ? `?invoice_id=${encodeURIComponent(props.invoiceId)}` : ''}`)
    if (request !== sequence) return
    plan.value = { method: data.method, rows: data.rows }; readOnly.value = data.read_only; version.value = data.version; source.value = data.source; loadedCurrency.value = data.currency
  } catch (e: any) { if (request === sequence) error.value = e.message }
  finally { if (request === sequence) loading.value = false }
}
watch(() => [props.caseId, props.invoiceId], load, { immediate: true })
watch([result, loading, error, readOnly], () => {
  const valid = !loading.value && !error.value && (readOnly.value || !result.value.error)
  emit('change', loading.value || error.value ? null : { method: plan.value.method, rows: result.value.rows }, valid, version.value, readOnly.value)
}, { deep: true })
function changeMethod(event: Event) { plan.value = switchMethod(plan.value, props.amount, (event.target as HTMLSelectElement).value as AllocationPlan['method']) }
</script>
<template>
  <section class="invoice-allocation" aria-label="发票律师分配表" :aria-busy="loading">
    <div class="allocation-heading"><div><h3>律师分配</h3><p>开票金额 {{ currency(amount, unit) }} · {{ source === 'case' ? '已带入案件承办团队' : '发票独立分配' }}</p></div>
      <label class="form-field"><span>分配方式</span><select :value="plan.method" :disabled="loading || !!error || readOnly" @change="changeMethod"><option value="ratio">按比例</option><option value="fixed">固定金额</option></select></label>
    </div>
    <p v-if="loading" role="status">正在加载案件律师及分配方案…</p>
    <div v-else-if="error" class="error-banner" role="alert">{{ error }} <button type="button" class="text-button" @click="load">重新加载</button></div>
    <template v-else>
      <p v-if="readOnly" class="notice">当前状态或账号权限仅支持查看分配明细。</p>
      <p v-if="source === 'missing'" class="notice">此历史发票未保存独立分配金额或开票快照，分配数据待核对。</p>
      <div class="table-wrap"><table><thead><tr><th>律师</th><th>承办角色</th><th>分配比例 %</th><th>分配金额</th></tr></thead><tbody>
        <tr v-for="(row, i) in displayRows" :key="row.lawyer_id"><td>{{ row.lawyer_name || row.lawyer_id }}</td><td>{{ row.role || '代理律师' }}</td>
          <td><input v-if="!readOnly && plan.method === 'ratio'" :value="plan.rows[i]!.ratio" @input="plan.rows[i]!.ratio = ($event.target as HTMLInputElement).value" :aria-label="`${row.lawyer_name}分配比例`" type="number" min="0" max="100" step="0.0001" required><span v-else>{{ row.ratio ?? '—' }}%</span></td>
          <td><input v-if="!readOnly && plan.method === 'fixed'" :value="plan.rows[i]!.amount" @input="plan.rows[i]!.amount = ($event.target as HTMLInputElement).value" :aria-label="`${row.lawyer_name}分配金额`" type="number" min="0" step="0.01" required><span v-else>{{ !readOnly && row.amount == null ? '待计算' : currency(row.amount ?? null, unit) }}</span></td></tr>
      </tbody></table></div>
      <div v-if="!readOnly" class="allocation-total" :class="result.error ? 'text-amber' : 'text-green'">合计 {{ result.total }}{{ plan.method === 'ratio' ? '%' : ` ${unit}` }}<span>{{ result.error || '分配合计与开票金额一致' }}</span></div>
      <p v-if="!readOnly" class="hint">切换方式会换算当前有效方案。按比例分配自动补齐分币尾差；开票时保存分配快照。</p>
    </template>
  </section>
</template>
