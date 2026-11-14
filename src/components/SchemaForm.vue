<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ fields: any[]; model: Record<string, any>; lawyers: any[]; references?: Record<string, any[]> }>()
const visible = computed(() => props.fields || [])
function choices(field: any): { value: string; label: string }[] {
  if (field.type === 'lawyer') return props.lawyers.map(x => ({ value: x.id, label: `${x.name} · ${x.department || x.title || '律师'}` }))
  const type = ({ document: 'documents', invoice: 'invoices', plan: 'payment_plans' } as Record<string, string>)[field.type]
  if (type) return (props.references?.[type] || []).map(x => ({ value: x.id, label: x.title + (x.number ? ` · ${x.number}` : '') }))
  return (field.options || []).map((x: string) => ({ value: x, label: x }))
}
</script>
<template>
  <div class="form-grid">
    <label v-for="field in visible" :key="field.key" class="form-field" :class="{ wide: field.type === 'textarea', checkbox: field.type === 'boolean' }">
      <span>{{ field.label }}<b v-if="field.required"> *</b></span>
      <textarea v-if="field.type === 'textarea'" v-model="model[field.key]" :required="field.required" :placeholder="`请输入${field.label}`" rows="3"></textarea>
      <select v-else-if="['select', 'lawyer', 'document', 'invoice', 'plan'].includes(field.type)" v-model="model[field.key]" :required="field.required">
        <option value="">请选择{{ field.label }}</option>
        <option v-if="model[field.key] && !choices(field).some(x => x.value === model[field.key])" :value="model[field.key]">{{ model[field.key] }}（历史值）</option>
        <option v-for="option in choices(field)" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <input v-else-if="field.type === 'boolean'" type="checkbox" v-model="model[field.key]">
      <template v-else-if="field.suggestions?.length">
        <input v-model="model[field.key]" :list="'dictionary-'+field.key" :required="field.required" :placeholder="`输入或选择${field.label}`">
        <datalist :id="'dictionary-'+field.key"><option v-for="entry in field.suggestions.filter((x:any)=>!x.office||x.office===model.office)" :key="entry.id||entry.code" :value="entry.name">{{ entry.code }}</option></datalist>
      </template>
      <input v-else v-model="model[field.key]" :type="field.type === 'date' ? 'date' : ['money', 'ratio', 'integer'].includes(field.type) ? 'number' : 'text'" :step="field.type === 'ratio' ? '0.0001' : field.type === 'money' ? '0.01' : field.type === 'integer' ? '1' : undefined" :min="['money','ratio'].includes(field.type) ? 0 : field.type === 'integer' ? 1 : undefined" :required="field.required" :placeholder="`请输入${field.label}`">
    </label>
  </div>
</template>
