<script setup lang="ts">
import { ChevronRight, FileText, FolderOpen } from 'lucide-vue-next'
import StatusBadge from './StatusBadge.vue'
import { currency, dateFull } from '../api'
defineProps<{ rows: any[]; kind: string; states: Record<string, any>; lawyers: any[]; loading?: boolean }>()
defineEmits<{ open: [record: any] }>()
function lawyerName(id: string, lawyers: any[]) { return lawyers.find(x => x.id === id)?.name || '未指派' }
</script>
<template>
  <div class="table-wrap" :aria-busy="loading">
    <table v-if="rows.length">
      <thead><tr>
        <th>{{ kind === 'cases' ? '案件名称 / 编号' : kind === 'lawyers' ? '律师姓名' : '名称 / 事项' }}</th>
        <th v-if="kind === 'cases'">客户 / 案件类型</th><th v-else-if="kind === 'lawyers'">部门 / 专业领域</th><th v-else>所属案件 / 关联信息</th>
        <th v-if="['cases','invoices','receipts','payment_plans'].includes(kind)">{{ kind === 'cases' ? '服务费 / 到账进度' : '金额' }}</th>
        <th v-else-if="kind === 'seals'">印章 / 份数</th><th v-else-if="kind === 'documents'">分类 / 文件</th><th v-else-if="kind === 'conflicts'">检索结果</th><th v-else-if="kind === 'parties'">关系</th><th v-else-if="kind === 'tasks'">截止日期</th><th v-else>补充信息</th>
        <th>{{ kind === 'cases' ? '主办律师' : '更新时间' }}</th><th>状态</th><th></th>
      </tr></thead>
      <tbody><tr v-for="row in rows" :key="row.id" @click="$emit('open',row)" tabindex="0" @keydown.enter="$emit('open',row)">
        <td><div class="record-title"><span class="record-icon" :class="kind"><FolderOpen v-if="kind === 'cases'" :size="18"/><span v-else-if="kind === 'lawyers'">{{ row.name?.slice(-2) }}</span><FileText v-else :size="17"/></span><div><strong>{{ (kind === 'receipts' ? row.invoice_title : '') || row.title || row.name || '未命名' }}</strong><small>{{ (kind === 'receipts' ? (row.invoice_number || row.invoice_title) : '') || row.number || row.license || (row.demo ? '演示记录' : row.source_key ? '历史迁移记录' : '') }}</small></div></div></td>
        <td v-if="kind === 'cases'"><span class="cell-text">{{ row.client }}</span><small>{{ row.category }}<span class="dot-separator">·</span>{{ row.department || row.office }}</small></td>
        <td v-else-if="kind === 'lawyers'">{{ row.department || '—' }}<small>{{ row.specialty || row.title }}</small></td>
        <td v-else><span class="cell-text">{{ row.case_title || row.recipient || row.contact || '—' }}</span><small>{{ row.case_number || row.number }}</small></td>
        <td v-if="['cases','invoices','receipts','payment_plans'].includes(kind)"><span class="money">{{ currency(row.amount, row.currency) }}</span><div v-if="kind === 'cases'" class="mini-progress-line"><span class="mini-progress"><i :style="{ width: `${Math.min(100, Number(row.finance?.received || 0) / (Number(row.amount) || 1) * 100)}%` }"></i></span><small>{{ Math.round(Number(row.finance?.received || 0) / (Number(row.amount) || 1) * 100) }}%</small></div></td>
        <td v-else-if="kind === 'seals'">{{ row.seal_type || '历史用印' }}<small>{{ row.copies || '—' }} 份</small></td>
        <td v-else-if="kind === 'documents'">{{ row.category }}<small>{{ row.files?.length || 0 }} 个文件版本</small></td>
        <td v-else-if="kind === 'conflicts'"><span :class="row.source_key?'muted':row.matches?.length ? 'text-amber' : 'text-green'">{{ row.source_key ? row.snapshot_count!==undefined?`${row.snapshot_count} 条历史快照明细`:'历史审批摘要' : row.matches?.length ? `${row.matches.length} 项潜在冲突` : '未发现匹配' }}</span></td>
        <td v-else-if="kind === 'parties'">{{ row.role }}</td>
        <td v-else-if="kind === 'tasks'">{{ row.due_on || '—' }}<small>{{ row.priority }} · {{ lawyerName(row.assignee_id,lawyers) }}</small></td>
        <td v-else>{{ row.archive_number || row.phone || '—' }}</td>
        <td v-if="kind === 'cases'"><div class="person"><span class="avatar tiny">{{ lawyerName(row.lead_lawyer_id,lawyers).slice(-2) }}</span>{{ lawyerName(row.lead_lawyer_id,lawyers) }}</div></td>
        <td v-else class="muted nowrap">{{ dateFull(row.updated_at) }}</td>
        <td><StatusBadge :status="row.status" :label="states[kind]?.[row.status] || row.legacy_status_label || row.status"/></td><td><ChevronRight :size="16" class="muted"/></td>
      </tr></tbody>
    </table>
    <div v-else class="empty-state"><FolderOpen :size="38"/><h3>{{ loading ? '正在加载…' : '暂无记录' }}</h3><p>{{ loading ? '正在获取最新信息' : '使用右上方操作创建第一条记录，所有数据都会关联至案件。' }}</p></div>
  </div>
</template>
