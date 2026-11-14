<script setup lang="ts">
import { ref } from 'vue'
import { post } from '../api'
defineEmits<{ back: [] }>()
const mode=ref('request'),username=ref(''),token=ref(''),password=ref(''),confirmation=ref(''),message=ref(''),error=ref(''),busy=ref(false)
async function submit(){
  error.value='';message.value='';busy.value=true
  try{
    if(mode.value==='request')message.value=(await post('/auth/forgot-password',{username:username.value})).message
    else{
      if(password.value!==confirmation.value)throw new Error('两次输入的密码不一致')
      await post('/auth/reset-password',{token:token.value.trim(),password:password.value})
      password.value='';confirmation.value='';token.value='';message.value='密码已设置，请返回登录。'
    }
  }catch(e:any){error.value=e.message}finally{busy.value=false}
}
</script>
<template>
  <form class="auth-card" @submit.prevent="submit">
    <h2>忘记密码 / 首次设置密码</h2>
    <p>使用原系统账号编号申请。管理员核验身份后提供一次性重置码，有效期 1 小时。</p>
    <div class="sub-tabs"><button type="button" :class="{active:mode==='request'}" @click="mode='request';message='';error=''">提交申请</button><button type="button" :class="{active:mode==='reset'}" @click="mode='reset';message='';error=''">已有重置码</button></div>
    <label v-if="mode==='request'">登录账号<input v-model="username" required maxlength="200" autocomplete="username"></label>
    <template v-else><label>一次性重置码<input v-model="token" required autocomplete="off"></label><label>新密码<input v-model="password" type="password" minlength="12" maxlength="200" required autocomplete="new-password"></label><label>确认新密码<input v-model="confirmation" type="password" minlength="12" required autocomplete="new-password"></label></template>
    <p v-if="message" class="notice">{{ message }}</p><p v-if="error" class="error-banner">{{ error }}</p>
    <button class="btn primary full" :disabled="busy">{{ mode==='request'?'提交重置申请':'设置新密码' }}</button>
    <button class="text-button" type="button" @click="$emit('back')">返回登录</button>
  </form>
</template>
