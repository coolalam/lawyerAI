<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, currency } from '../api'
import PersonPicker from './PersonPicker.vue'
import { decimal, units } from '../invoiceAllocation'
const props = defineProps<{ people: any[]; caseId?: string; states: Record<string, string> }>()
const emit = defineEmits<{ open: [id: string] }>()
const today = new Date().toLocaleDateString('sv-SE')
const lawyer = ref(''), start = ref(`${today.slice(0, 4)}-01-01`), end = ref(today), basis = ref('issued_on'), unit = ref('CNY'), dimension = ref('lawyer'), page = ref(1), tab = ref('charts')
const loading = ref(false), error = ref(''), report = ref<any>(null)
let sequence = 0
async function load() {
  const request = ++sequence; loading.value = true; error.value = ''; report.value = null
  if (start.value && end.value && start.value > end.value) { error.value = '开始日期不能晚于结束日期'; loading.value = false; return }
  const query = new URLSearchParams({ lawyer_id: lawyer.value, case_id: props.caseId || '', date_basis: basis.value, currency: unit.value, dimension: dimension.value, page: String(page.value) })
  if (start.value) query.set('date_from', start.value)
  if (end.value) query.set('date_to', end.value)
  try { const data = await api(`/finance/revenue?${query}`); if (request === sequence) report.value = data }
  catch (e: any) { if (request === sequence) error.value = e.message }
  finally { if (request === sequence) loading.value = false }
}
watch([lawyer, start, end, basis, unit, dimension, () => props.caseId], () => { if (page.value !== 1) page.value = 1; else load() }, { immediate: true })
watch(page, load)
const groups = computed(() => {
  const rows = report.value?.breakdown || []
  if (rows.length <= 12) return rows
  return [...rows.slice(0, 11), { id: '__others', label: `其他 ${rows.length - 11} 项`, amount: decimal(rows.slice(11).reduce((sum: bigint, row: any) => sum + (units(row.amount, 2) || 0n), 0n), 2) }]
})
function scale(value: string, rows: any[]) { const max = Math.max(...rows.map(row => Number(row.amount)), 0); return max ? Number(value) / max * 100 : 0 }
</script>
<template>
  <div class="revenue-analysis">
    <p class="hint">按已开票发票的律师分配金额统计；包含冲红申请中的发票，已冲红和作废发票不计入。各币种单独核算。</p>
    <div class="revenue-filters">
      <div><PersonPicker v-model="lawyer" :people="people" label="律师" placeholder="全部可见律师"/><button v-if="lawyer" class="text-button" @click="lawyer = ''">清除律师筛选</button></div>
      <label class="form-field"><span>日期口径</span><select v-model="basis"><option value="issued_on">开票日期</option><option value="created_at">申请创建日期</option></select></label>
      <label class="form-field"><span>开始日期</span><input v-model="start" type="date"></label>
      <label class="form-field"><span>结束日期</span><input v-model="end" type="date"></label>
      <label class="form-field"><span>币种</span><select v-model="unit"><option v-for="code in ['CNY','USD','HKD','EUR','JPY']" :key="code">{{ code }}</option></select></label>
      <label class="form-field"><span>分析维度</span><select v-model="dimension"><option value="lawyer">律师</option><option value="case">案件</option><option value="department">律师部门</option><option value="category">案件类型</option><option value="office">律师机构</option></select></label>
    </div>
    <p v-if="loading" role="status">正在统计创收数据…</p>
    <div v-else-if="error" class="error-banner" role="alert">{{ error }} <button class="text-button" @click="load">重试</button></div>
    <template v-else-if="report">
      <div class="stats-grid compact-stats"><div class="stat-card"><span>分配创收总额 · {{ unit }}</span><strong>{{ currency(report.summary.amount, unit) }}</strong></div><div class="stat-card"><span>发票数量</span><strong>{{ report.summary.invoice_count }}</strong></div><div class="stat-card"><span>参与律师</span><strong>{{ report.summary.lawyer_count }}</strong></div></div>
      <p v-if="report.summary.unresolved_count" class="notice">当前日期及币种范围内有 {{ report.summary.unresolved_count }} 张可见发票的日期或分配金额缺失、合计不符，未计入统计（此计数不按律师筛选）。</p>
      <div class="sub-tabs"><button :class="{active:tab==='charts'}" @click="tab='charts'">图表</button><button :class="{active:tab==='details'}" @click="tab='details'">详情（{{ report.total }}）</button></div>
      <div v-if="!report.total" class="compact-empty">此筛选条件下暂无可统计的发票分配。</div>
      <div v-else-if="tab==='charts'" class="revenue-charts">
        <section><h3>月度创收趋势</h3><p class="hint">{{ basis === 'issued_on' ? '按开票月份' : '按申请创建月份' }} · {{ unit }}</p>
          <div class="revenue-trend" aria-label="月度创收柱状图"><div v-for="row in report.trend" :key="row.label" class="revenue-column"><span>{{ currency(row.amount, unit) }}</span><div class="revenue-track"><i :style="{height:scale(row.amount,report.trend)+'%'}"></i></div><strong>{{ row.label }}</strong></div></div>
        </section>
        <section><h3>创收分布</h3><p class="hint">按所选维度汇总 · {{ unit }}</p><div class="revenue-bars"><div v-for="row in groups" :key="row.id" class="revenue-bar"><div><span>{{ row.label }}</span><strong>{{ currency(row.amount,unit) }}</strong></div><div class="revenue-bar-track"><i :style="{width:scale(row.amount,groups)+'%'}"></i></div></div></div><details v-if="report.breakdown.length>12"><summary>查看全部 {{ report.breakdown.length }} 项</summary><div v-for="row in report.breakdown" :key="row.id" class="key-value"><span>{{ row.label }}</span><strong>{{ currency(row.amount,unit) }}</strong></div></details></section>
      </div>
      <template v-else>
        <div class="table-wrap"><table><thead><tr><th>日期</th><th>发票</th><th>案号</th><th>律师 / 部门</th><th>状态</th><th>分配金额 · {{ unit }}</th></tr></thead><tbody><tr v-for="row in report.items" :key="row.invoice_id+row.lawyer_id"><td>{{ row.date }}</td><td><button class="text-button" @click="emit('open',row.invoice_id)">{{ row.invoice_number || '查看发票' }}</button></td><td :title="row.case_title">{{ row.case_number }}</td><td>{{ row.lawyer_name }}<small>{{ row.department }}</small></td><td>{{ states[row.status] || row.status }}</td><td>{{ currency(row.amount,unit) }}</td></tr></tbody></table></div>
        <div class="pagination"><span>共 {{ report.total }} 条律师分配 · 第 {{ page }} 页</span><button class="btn small" :disabled="page<=1" @click="page--">上一页</button><button class="btn small" :disabled="page*50>=report.total" @click="page++">下一页</button></div>
      </template>
    </template>
  </div>
</template>
