<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { apiClient } from '@/services/api/client'

const router = useRouter()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const loading = ref(false)

const submit = async () => {
  if (!username.value.trim() || !password.value.trim()) return
  loading.value = true
  try {
    const endpoint = mode.value === 'login' ? '/api/auth/login' : '/api/auth/register'
    const resp = await apiClient.post(endpoint, {
      username: username.value.trim(),
      password: password.value,
    })
    const data = resp.data
    if (data.success && data.token) {
      localStorage.setItem('pg_token', data.token)
      localStorage.setItem('pg_username', data.username || '')
      message.success(mode.value === 'login' ? '登录成功' : '注册成功')
      router.push('/')
      location.reload()
    } else {
      message.error(data.message || '操作失败')
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '请求失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <div class="login-logo__mark">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none">
            <circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/>
            <circle cx="18" cy="6" r="3" stroke="currentColor" stroke-width="1.6"/>
            <circle cx="18" cy="18" r="3" stroke="currentColor" stroke-width="1.6"/>
            <path d="M8.5 11L15.5 7M8.5 13L15.5 17" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          </svg>
        </div>
        <span class="login-logo__text">知脉</span>
      </div>
      <div class="login-tabs">
        <button :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>
      <a-input v-model:value="username" placeholder="用户名" size="large" class="login-input" @pressEnter="submit" />
      <a-input-password v-model:value="password" placeholder="密码" size="large" class="login-input" @pressEnter="submit" />
      <a-button type="primary" size="large" block :loading="loading" :disabled="!username.trim() || !password.trim()" @click="submit">
        {{ mode === 'login' ? '登录' : '注册' }}
      </a-button>
      <p class="login-hint">默认用户名 default，密码 default</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--pg-bg);
  background-image: var(--pg-bg-aurora);
}
.login-card {
  width: 380px;
  max-width: 90vw;
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-xl);
  box-shadow: var(--pg-shadow-lg);
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 8px;
}
.login-logo__mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--pg-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--pg-shadow-primary);
}
.login-logo__text {
  font-family: var(--pg-font-serif);
  font-size: 24px;
  font-weight: 700;
  color: var(--pg-text-heading);
}
.login-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
.login-tabs button {
  flex: 1;
  padding: 8px;
  border: 1px solid var(--pg-border);
  background: var(--pg-surface);
  border-radius: var(--pg-radius);
  cursor: pointer;
  font-size: 14px;
  color: var(--pg-text-secondary);
  transition: all 0.15s ease;
}
.login-tabs button.active {
  background: var(--pg-primary-soft);
  border-color: var(--pg-primary);
  color: var(--pg-primary-hover);
  font-weight: 600;
}
.login-input {
  border-radius: var(--pg-radius) !important;
}
.login-hint {
  text-align: center;
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin: 0;
}
</style>
