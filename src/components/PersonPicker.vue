<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Building2, Check, ChevronDown, ChevronRight, Search, Users, X } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue?: string
  people: any[]
  branches?: any[]
  departments?: any[]
  label?: string
  required?: boolean
  placeholder?: string
  activeOnly?: boolean
  showLabel?: boolean
}>(), { people: () => [], branches: () => [], departments: () => [], label: '人员', placeholder: '请选择人员', activeOnly: false, showLabel: true })
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const open = ref(false), query = ref(''), selectedScope = ref('all')
const selected = computed(() => props.people.find(person => person.id === props.modelValue))
const branchKey = (branch: any) => `${branch.office || ''}::${branch.code || branch.branch_code || ''}`
const departmentKey = (department: any) => department.id || `${department.office || department.branch_name || ''}::${department.code || department.department_code || department.name || ''}`
const branchName = (branch: any) => branch.name || branch.branch_name || branch.office || '未分配分支机构'
const departmentName = (department: any) => department.name || department.department || department.department_name || department.code || '未分配部门'

const branchItems = computed(() => {
  const result: any[] = [], seen = new Set<string>()
  for (const branch of props.branches) {
    const key = branchKey(branch)
    if (seen.has(key)) continue
    seen.add(key); result.push({ ...branch, key })
  }
  for (const person of props.people) {
    if (!person.office && !person.branch_code && !person.branch_name) continue
    const branch = { office: person.office, code: person.branch_code, name: person.branch_name || person.office }
    const key = branchKey(branch)
    if (!seen.has(key)) { seen.add(key); result.push({ ...branch, key }) }
  }
  return result.sort((a, b) => branchName(a).localeCompare(branchName(b), 'zh-CN'))
})

const departmentItems = computed(() => {
  const result: any[] = [], seen = new Set<string>()
  for (const department of props.departments) {
    const key = departmentKey(department)
    if (seen.has(key)) continue
    seen.add(key); result.push({ ...department, key })
  }
  for (const person of props.people) {
    if (!person.department && !person.department_code && !person.department_id) continue
    const department = { id: person.department_id, office: person.office, branch_code: person.branch_code, branch_name: person.branch_name, code: person.department_code, name: person.department }
    const key = departmentKey(department)
    if (!seen.has(key)) { seen.add(key); result.push({ ...department, key }) }
  }
  return result.sort((a, b) => departmentName(a).localeCompare(departmentName(b), 'zh-CN'))
})

const currentBranch = computed(() => {
  if (selectedScope.value.startsWith('branch:')) return branchItems.value.find(branch => selectedScope.value === `branch:${branch.key}`)
  if (selectedScope.value.startsWith('department:')) {
    const department = departmentItems.value.find(item => `department:${item.key}` === selectedScope.value)
    return branchItems.value.find(branch => department && ((department.office || department.branch_name || '') === (branch.office || branch.name || '') || (department.branch_code || '') === (branch.code || branch.branch_code || '')))
  }
  return undefined
})
const visibleDepartments = computed(() => departmentItems.value.filter(department => {
  if (!currentBranch.value) return true
  const branch = currentBranch.value
  return (department.office || department.branch_name || '') === (branch.office || branch.name || '') ||
    (department.branch_code || '') === (branch.code || branch.branch_code || '')
}))
const filteredPeople = computed(() => {
  const text = query.value.trim().toLowerCase()
  return props.people.filter(person => {
    if (props.activeOnly && person.status && person.status !== 'active') return false
    if (selectedScope.value.startsWith('branch:')) {
      const branch = currentBranch.value
      if (branch && (person.office || person.branch_name || '') !== (branch.office || branch.name || '') && (person.branch_code || '') !== (branch.code || branch.branch_code || '')) return false
    }
    if (selectedScope.value.startsWith('department:')) {
      const key = selectedScope.value.slice('department:'.length)
      const department = departmentItems.value.find(item => item.key === key)
      if (department && person.department_id !== department.id && person.department_code !== department.code && person.department !== department.name) return false
    }
    if (!text) return true
    return [person.name, person.employee_id, person.title, person.department, person.branch_name, person.office, person.username]
      .filter(Boolean).join(' ').toLowerCase().includes(text)
  }).sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'))
})
const scopeTitle = computed(() => {
  if (selectedScope.value === 'all') return '全部人员'
  if (selectedScope.value.startsWith('branch:')) return branchName(currentBranch.value || {})
  return departmentName(departmentItems.value.find(item => `department:${item.key}` === selectedScope.value) || {})
})
function choose(person: any) { emit('update:modelValue', person.id); open.value = false }
function show() { query.value = ''; selectedScope.value = 'all'; open.value = true }
function close() { open.value = false }
function selectedLabel(person: any) { return person ? `${person.name || '未命名'}${person.title ? ` · ${person.title}` : ''}` : '' }
function branchOpen(branch: any) {
  if (selectedScope.value === `branch:${branch.key}`) return true
  if (!selectedScope.value.startsWith('department:')) return false
  const department = departmentItems.value.find(item => `department:${item.key}` === selectedScope.value)
  return !!department && ((department.office || department.branch_name || '') === (branch.office || branch.name || '') || (department.branch_code || '') === (branch.code || branch.branch_code || ''))
}
watch(open, value => { if (!value) query.value = '' })
</script>

<template>
  <span class="form-field person-picker-field">
    <span v-if="showLabel">{{ label }}<b v-if="required"> *</b></span>
    <button type="button" class="person-picker-trigger" :class="{ empty: !selected }" @click="show">
      <span v-if="selected" class="person"><span class="avatar tiny">{{ selected.name?.slice(-2) }}</span><span>{{ selectedLabel(selected) }}</span></span>
      <span v-else>{{ placeholder }}</span><ChevronDown :size="15" />
    </button>
    <input class="person-picker-value" type="text" :value="modelValue || ''" :required="required" tabindex="-1" aria-hidden="true">
  </span>
  <Teleport to="body">
    <div v-if="open" class="person-picker-backdrop" @click.self="close">
      <section class="person-picker-dialog" role="dialog" aria-modal="true" :aria-label="`选择${label}`">
        <header><div><span class="eyebrow">PERSON DIRECTORY</span><h2>选择{{ label }}</h2><p>{{ scopeTitle }} · {{ filteredPeople.length }} 人</p></div><button type="button" class="icon-button" aria-label="关闭" @click="close"><X :size="20" /></button></header>
        <div class="person-picker-search"><Search :size="17" /><input v-model="query" autofocus :placeholder="`按姓名搜索${label}`" :aria-label="`搜索${label}`"></div>
        <div class="person-picker-content">
          <aside class="person-picker-tree" aria-label="分支机构与部门">
            <button type="button" class="person-picker-scope" :class="{ active: selectedScope === 'all' }" @click="selectedScope = 'all'"><Users :size="15" />全部人员</button>
            <div v-for="branch in branchItems" :key="branch.key" class="person-picker-branch">
              <button type="button" class="person-picker-scope branch" :class="{ active: branchOpen(branch) }" @click="selectedScope = `branch:${branch.key}`"><Building2 :size="15" /><span>{{ branchName(branch) }}</span><ChevronRight :size="14" /></button>
              <div v-if="branchOpen(branch)" class="person-picker-departments">
                <button type="button" class="person-picker-scope" :class="{ active: selectedScope === `branch:${branch.key}` }" @click="selectedScope = `branch:${branch.key}`">全部部门</button>
                <button v-for="department in visibleDepartments" :key="department.key" type="button" class="person-picker-scope department" :class="{ active: selectedScope === `department:${department.key}` }" @click="selectedScope = `department:${department.key}`"><span>{{ departmentName(department) }}</span></button>
              </div>
            </div>
            <div v-if="!branchItems.length" class="person-picker-tree-empty">暂无分支机构信息</div>
          </aside>
          <div class="person-picker-results">
            <div v-if="query" class="person-picker-result-caption">搜索结果</div>
            <button v-for="person in filteredPeople" :key="person.id" type="button" class="person-picker-person" :class="{ selected: person.id === modelValue }" @click="choose(person)"><span class="avatar small">{{ person.name?.slice(-2) }}</span><span><strong>{{ person.name || '未命名' }}</strong><small>{{ person.department || '未分配部门' }} · {{ person.title || '未设置职务' }}</small></span><Check v-if="person.id === modelValue" :size="17" /></button>
            <div v-if="!filteredPeople.length" class="person-picker-empty"><Users :size="30" /><p>没有匹配的人员</p><small>请更换姓名关键词或左侧组织范围</small></div>
          </div>
        </div>
        <footer><span class="muted">可按姓名搜索，选择后自动返回表单</span><button type="button" class="btn" @click="close">取消</button></footer>
      </section>
    </div>
  </Teleport>
</template>
