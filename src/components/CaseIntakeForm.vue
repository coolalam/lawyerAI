<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { api, patch, post } from '../api'
import SchemaForm from './SchemaForm.vue'

const props = defineProps<{ record: any; fields: any[]; lawyers: any[]; branches: any[]; departments: any[]; documents: any[]; approved: boolean }>()
const emit = defineEmits<{ saved: [submitted: boolean] }>()
const model = reactive<Record<string, any>>({})
const file = ref<File | null>(null), fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false), error = ref(''), version = ref(0)
const fields = computed(() => props.fields.filter(f => !['contract_document_id','archive_number','archive_location','closed_on','result'].includes(f.key)))
const documents = computed(() => props.documents.filter(d => d.case_id === props.record.id && d.files?.length))
watch([() => props.record.id, () => props.record.version], () => {
  for (const key of Object.keys(model)) delete model[key]
  for (const field of fields.value) model[field.key] = props.record[field.key] ?? field.default ?? (field.type === 'boolean' ? false : '')
  model.contract_document_id = props.record.contract_document_id || ''
  version.value = props.record.version
  file.value = null
  if (fileInput.value) fileInput.value.value = ''
}, { immediate: true })

async function save(event: Event) {
  if (busy.value) return
  const submit = (event as SubmitEvent).submitter?.getAttribute('value') === 'submit'
  error.value = ''
  if (submit && !props.approved) { error.value = '请先完成当前当事人信息的利冲审批'; return }
  if (submit && !file.value && !model.contract_document_id) { error.value = '提交立案审批前请上传委托合同附件'; return }
  busy.value = true
  let saved = false
  try {
    if (file.value) {
      const body = new FormData()
      body.append('case_id', props.record.id)
      body.append('category', '委托合同')
      body.append('file', file.value)
      const document = await api('/documents/upload', { method: 'POST', body })
      model.contract_document_id = document.id
      file.value = null
      if (fileInput.value) fileInput.value.value = ''
    }
    const result = await patch(`/records/cases/${props.record.id}`, { ...model, version: version.value })
    version.value = result.version
    saved = true
    if (submit) await post(`/records/cases/${props.record.id}/transition`, { status: 'submitted', version: result.version })
    emit('saved', submit)
  } catch (e: any) {
    error.value = e.message
    if (saved) emit('saved', false)
  } finally { busy.value = false }
}
</script>

<template>
  <form class="case-intake-form" @submit.prevent="save">
    <fieldset :disabled="busy">
      <SchemaForm :fields="fields" :model="model" :lawyers="lawyers" :branches="branches" :departments="departments" />
      <div class="form-grid mt">
        <label class="form-field"><span>上传合同附件</span><input ref="fileInput" type="file" aria-label="上传合同附件" @change="file=($event.target as HTMLInputElement).files?.[0] || null"><small>提交审批时须有一份合同附件，单文件上限 50MB。</small></label>
        <label class="form-field"><span>已上传的合同附件</span><select v-model="model.contract_document_id" :disabled="!!file"><option value="">请选择附件或上传新文件</option><option v-if="model.contract_document_id && !documents.some(d=>d.id===model.contract_document_id)" :value="model.contract_document_id">已关联的合同附件</option><option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.title }}</option></select></label>
      </div>
    </fieldset>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="!approved" class="hint">利冲审批通过后即可提交立案审批。</p>
    <div class="inline-actions mt"><button type="submit" value="save" class="btn" :disabled="busy">保存立案信息</button><button type="submit" value="submit" class="btn primary" :disabled="busy || !approved">提交审批</button></div>
  </form>
</template>

<style scoped>
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
.form-field small { color: var(--muted); font-size: 11px; }
</style>
