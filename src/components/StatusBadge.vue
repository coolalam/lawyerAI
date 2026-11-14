<script setup lang="ts">
defineProps<{ status?: string; label?: string }>()
</script>
<template><span class="status-badge" :class="status"><i></i>{{ label || status || '未设置' }}</span></template>
