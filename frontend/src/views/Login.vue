<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { apiClient } from '@/services/api/client'

const router = useRouter()
const route = useRoute()
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
      const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/search'
      await router.replace(redirect)
    } else {
      message.error(data.message || '操作失败')
    }
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : '请求失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-ambient login-ambient--one"></div>
    <div class="login-ambient login-ambient--two"></div>
    <section class="login-intro">
      <div class="login-intro__badge">PAPERGRAPH · AI RESEARCH OS</div>
      <h1>让每一篇论文，<br />连接成你的知识脉络。</h1>
      <p>从智能检索、每日推荐到精读问答和知识图谱，在同一个研究工作空间完成发现、理解与沉淀。</p>
      <div class="login-intro__features">
        <span>跨源文献发现</span><span>全文精读助手</span><span>个人知识图谱</span>
      </div>
    </section>
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
        <span class="login-logo__text">知脉<small>PaperGraph</small></span>
      </div>
      <div class="login-card__welcome">
        <strong>{{ mode === 'login' ? '欢迎回来' : '创建研究空间' }}</strong>
        <span>{{ mode === 'login' ? '登录后继续你的文献探索' : '注册即可开始构建个人知识脉络' }}</span>
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
      <p class="login-hint">{{ mode === 'login' ? '请使用已注册的账号登录' : '注册即表示你同意安全保存个人研究数据' }}</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: clamp(48px, 8vw, 120px);
  padding: 48px clamp(24px, 6vw, 96px);
  box-sizing: border-box;
  position: relative;
  overflow: hidden;
  background: #f7f7fb;
  background-image: var(--pg-bg-aurora);
}
.login-ambient { position: absolute; border-radius: 50%; filter: blur(10px); pointer-events: none; }
.login-ambient--one { width: 420px; height: 420px; left: -160px; top: -170px; background: rgba(99,102,241,.08); }
.login-ambient--two { width: 340px; height: 340px; right: -120px; bottom: -160px; background: rgba(67,56,202,.07); }
.login-intro { width: min(560px, 45vw); position: relative; z-index: 1; }
.login-intro__badge { display: inline-flex; padding: 7px 11px; border: 1px solid rgba(67,56,202,.14); border-radius: 999px; color: var(--pg-accent); background: rgba(255,255,255,.56); font-size: 10px; font-weight: 750; letter-spacing: .14em; }
.login-intro h1 { margin: 26px 0 18px; color: var(--pg-text-heading); font: 700 clamp(38px, 4vw, 58px)/1.14 var(--pg-font-serif); letter-spacing: -.025em; }
.login-intro p { max-width: 520px; margin: 0; color: var(--pg-text-secondary); font-size: 16px; line-height: 1.85; }
.login-intro__features { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 28px; }
.login-intro__features span { padding: 7px 12px; border-radius: 999px; color: var(--pg-text-secondary); background: rgba(255,255,255,.68); border: 1px solid rgba(255,255,255,.9); box-shadow: var(--pg-shadow-xs); font-size: 12px; }
.login-card {
  width: 400px;
  max-width: 90vw;
  position: relative;
  z-index: 1;
  background: rgba(255,255,255,.82);
  backdrop-filter: saturate(150%) blur(18px);
  border: 1px solid rgba(255,255,255,.95);
  border-radius: var(--pg-radius-xl);
  box-shadow: var(--pg-shadow-lg);
  padding: 38px 36px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 2px;
}
.login-logo__mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--pg-primary-soft);
  color: var(--pg-accent);
  border: 1px solid #dfe3ff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--pg-shadow-primary);
}
.login-logo__text { display: flex; flex-direction: column; font-family: var(--pg-font-serif); font-size: 24px; font-weight: 700; color: var(--pg-text-heading); line-height: 1.05; }
.login-logo__text small { margin-top: 4px; color: var(--pg-text-tertiary); font: 600 9px/1 var(--pg-font); letter-spacing: .16em; text-transform: uppercase; }
.login-card__welcome { display: flex; flex-direction: column; align-items: center; gap: 4px; margin: 8px 0 4px; }
.login-card__welcome strong { font-family: var(--pg-font-serif); font-size: 21px; color: var(--pg-text-heading); }
.login-card__welcome span { color: var(--pg-text-tertiary); font-size: 12px; }
.login-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 6px;
  padding: 4px;
  border-radius: 12px;
  background: var(--pg-bg-soft);
}
.login-tabs button {
  flex: 1;
  padding: 8px;
  border: 0;
  background: transparent;
  border-radius: var(--pg-radius);
  cursor: pointer;
  font-size: 14px;
  color: var(--pg-text-secondary);
  transition: all 0.15s ease;
}
.login-tabs button.active {
  background: var(--pg-surface);
  border-color: transparent;
  box-shadow: var(--pg-shadow-sm);
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
@media (max-width: 860px) {
  .login-page { padding: 32px 20px; }
  .login-intro { display: none; }
  .login-card { width: min(400px, 100%); padding: 32px 24px 28px; }
}
</style>
