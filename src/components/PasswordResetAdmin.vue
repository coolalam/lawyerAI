<script setup lang="ts">
import { ref,onMounted } from 'vue'
import { api,post,dateFull } from '../api'
defineProps<{users:any[]}>()
const requests=ref<any[]>([]),selected=ref(''),note=ref(''),result=ref<any>(null),error=ref(''),busy=ref(false)
async function load(){try{requests.value=await api('/admin/password-reset-requests')}catch(e:any){error.value=e.message}}
async function issue(){busy.value=true;error.value='';result.value=null;try{const r=await post(`/admin/users/${selected.value}/password-reset-token`,{verification_note:note.value});result.value={...r,user_id:selected.value};await load()}catch(e:any){error.value=e.message}finally{busy.value=false}}
onMounted(load)
</script>
<template>
  <section class="panel mb"><div class="panel-heading"><div><h2>密码重置申请</h2><p>核验身份后签发一次性重置码，使用后失效。重置不会改变账号权限。</p></div><button class="btn small" @click="load">刷新申请</button></div>
    <div class="panel-body"><div v-for="r in requests" :key="r.user_id" class="key-value"><span>{{ r.name }} · {{ r.username }} · {{ r.status==='issued'?'已签发':'待核验' }}</span><button class="text-button" @click="selected=r.user_id;result=null">处理申请</button></div><p v-if="!requests.length" class="muted">暂无待处理申请；也可为首次登录的成员签发重置码。</p>
      <form class="form-grid" @submit.prevent="issue"><label class="form-field"><span>重置账号</span><select v-model="selected" required @change="result=null"><option value="">请选择已启用账号</option><option v-for="u in users.filter(x=>x.active)" :key="u.id" :value="u.id">{{ u.name }} · {{ u.username }}</option></select></label><label class="form-field"><span>身份核验说明</span><input v-model="note" required minlength="5" maxlength="500" placeholder="记录已核验身份的方式"></label><button class="btn" :disabled="busy">签发一次性重置码</button></form>
      <p v-if="error" class="error-banner">{{ error }}</p><div v-if="result" class="notice"><p>账号：{{ users.find(x=>x.id===result.user_id)?.username }}；有效期至 {{ dateFull(result.expires_at) }}。请通过已核验的联系方式交给本人，此码只显示一次。</p><label class="form-field"><span>一次性重置码</span><input :value="result.token" readonly autocomplete="off"></label></div>
    </div>
  </section>
</template>
