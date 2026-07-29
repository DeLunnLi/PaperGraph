<template>
  <a-config-provider :theme="themeConfig">
    <div v-if="isStandalone" class="standalone-root">
      <router-view />
    </div>
    <a-layout v-else class="app-layout">
      <a-layout-sider
        v-if="!isMobile"
        v-model:collapsed="collapsed"
        class="app-sider"
        :trigger="null"
        collapsible
        theme="light"
        :width="siderWidth"
      >
        <div class="logo" :class="{ 'logo--collapsed': collapsed }">
          <div class="logo__mark">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
              <circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/>
              <circle cx="18" cy="6" r="3" stroke="currentColor" stroke-width="1.6"/>
              <circle cx="18" cy="18" r="3" stroke="currentColor" stroke-width="1.6"/>
              <path d="M8.5 11L15.5 7M8.5 13L15.5 17" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
            </svg>
          </div>
          <span v-if="!collapsed" class="logo__text">知脉<span class="logo__text-sub">PaperGraph</span></span>
        </div>
        <a-menu v-model:selectedKeys="selectedKeys" class="app-menu" mode="inline" @click="handleMenuClick">
          <a-menu-item key="search">
            <template #icon><RobotOutlined /></template>
            <span>文献搜索</span>
          </a-menu-item>
          <a-menu-item key="daily">
            <template #icon><CalendarOutlined /></template>
            <span>每日论文</span>
          </a-menu-item>
          <a-menu-item key="library">
            <template #icon><BookOutlined /></template>
            <span>我的文献库</span>
          </a-menu-item>
          <a-menu-item key="graph">
            <template #icon><ShareAltOutlined /></template>
            <span>知识图谱</span>
          </a-menu-item>
        </a-menu>
        <div v-if="!collapsed" class="app-sider__calendar">
          <ReadingCalendar />
        </div>
      </a-layout-sider>
      <a-layout class="app-main">
        <a-layout-header class="app-header">
          <div class="app-header__brand" aria-label="知脉 PaperGraph">
            <div class="app-header__brand-mark">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
                <circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/>
                <circle cx="18" cy="6" r="3" stroke="currentColor" stroke-width="1.6"/>
                <circle cx="18" cy="18" r="3" stroke="currentColor" stroke-width="1.6"/>
                <path d="M8.5 11L15.5 7M8.5 13L15.5 17" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
              </svg>
            </div>
            <span>知脉</span>
          </div>
          <div class="app-header__crumb">
            <span class="app-header__dot"></span>
            <span class="app-header__title">{{ pageTitle }}</span>
          </div>
          <span class="app-header__subtitle">{{ pageSubtitle }}</span>
        </a-layout-header>
        <a-layout-content class="app-content" :class="{ 'app-content--no-scroll': contentNoScroll }">
          <div class="app-content__inner" :class="{ 'app-content__inner--no-scroll': contentNoScroll }">
            <router-view v-slot="{ Component }">
              <transition name="pg-fade" mode="out-in">
                <keep-alive include="SearchAgent,DailyArxiv">
                  <component :is="Component" />
                </keep-alive>
              </transition>
            </router-view>
          </div>
        </a-layout-content>
        <nav v-if="isMobile" class="mobile-nav" aria-label="主导航">
          <button
            v-for="item in mobileNavItems"
            :key="item.key"
            type="button"
            class="mobile-nav__item"
            :class="{ 'mobile-nav__item--active': selectedKeys.includes(item.key) }"
            :aria-current="selectedKeys.includes(item.key) ? 'page' : undefined"
            @click="router.push(`/${item.key}`)"
          >
            <component :is="item.icon" class="mobile-nav__icon" />
            <span>{{ item.label }}</span>
          </button>
        </nav>
      </a-layout>
    </a-layout>
  </a-config-provider>
</template>
<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { theme as antdTheme } from 'ant-design-vue'
import ReadingCalendar from '@/components/ReadingCalendar.vue'
import {
  RobotOutlined,
  CalendarOutlined,
  BookOutlined,
  ShareAltOutlined,
} from '@ant-design/icons-vue'
const router = useRouter()
const route = useRoute()
const collapsed = ref(false)
const selectedKeys = ref<string[]>(['search'])
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1280)
const siderWidth = computed(() => {
  if (windowWidth.value < 768) return 64
  if (windowWidth.value < 1024) return 200
  return 232
})
const isMobile = computed(() => windowWidth.value < 768)
const isStandalone = computed(() => {
  const n = String(route.name || '').toLowerCase()
  return n === 'login' || (n === 'library-read' && String(route.query?.standalone || '') === '1')
})
const contentNoScroll = computed(() => {
  const n = String(route.name || '').toLowerCase()
  return n === 'search' || n === 'library-read'
})
const mobileNavItems = [
  { key: 'search', label: '搜索', icon: RobotOutlined },
  { key: 'daily', label: '每日', icon: CalendarOutlined },
  { key: 'library', label: '文献库', icon: BookOutlined },
  { key: 'graph', label: '图谱', icon: ShareAltOutlined },
]
const pageTitle = computed(() => {
  const titles: Record<string, string> = {
    search: '文献搜索',
    daily: '每日论文',
    library: '我的文献库',
    'library-read': '文献阅读',
    graph: '知识图谱',
  }
  const name = (route.name as string)?.toLowerCase() || ''
  return titles[name] || '知脉'
})
const pageSubtitle = computed(() => {
  const subtitles: Record<string, string> = {
    search: '发现值得阅读的研究',
    daily: '为你筛选今日新论文',
    library: '沉淀、整理与回顾知识',
    'library-read': '精读论文并记录思考',
    graph: '探索文献之间的关联',
  }
  return subtitles[String(route.name || '').toLowerCase()] || ''
})
const themeConfig = {
  algorithm: antdTheme.defaultAlgorithm,
  token: {
    colorPrimary: '#5b5ce2',
    colorLink: '#5658d8',
    colorInfo: '#6366e8',
    colorBgLayout: 'var(--pg-bg)',
    borderRadius: 10,
    borderRadiusLG: 16,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif',
    fontSize: 14,
    controlHeight: 38,
    controlHeightSM: 30,
    controlHeightLG: 46,
    colorText: '#18181b',
    colorTextSecondary: '#52525b',
    colorBorder: '#ebebee',
    colorBgContainer: '#ffffff',
  },
  components: {
    Layout: {
      siderBg: 'rgba(255,255,255,0.72)',
      headerBg: 'rgba(255,255,255,0.8)',
      bodyBg: 'var(--pg-bg)',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: '#f0f1ff',
      itemSelectedColor: '#4f46d8',
      itemHoverBg: '#f7f7ff',
      itemColor: '#52525b',
      itemHoverColor: '#18181b',
      itemBorderRadius: 8,
    },
    Button: {
      primaryShadow: 'none',
      defaultShadow: 'none',
    },
    Card: {
      borderRadiusLG: 14,
    },
  },
}
const handleMenuClick = ({ key }: { key: string }) => {
  router.push(`/${key}`)
}
watch(
  () => route.name,
  (name) => {
    const n = String(name || '').toLowerCase()
    if (n === 'library-read') selectedKeys.value = ['library']
    else if (n) selectedKeys.value = [n]
  },
  { immediate: true }
)
const onResize = () => {
  windowWidth.value = window.innerWidth
  if (window.innerWidth < 1024 && !collapsed.value) collapsed.value = true
  if (window.innerWidth >= 1280 && collapsed.value) collapsed.value = false
}
onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>
<style scoped>
.standalone-root {
  height: 100vh;
  width: 100vw;
  min-height: 100vh;
  background: var(--pg-bg);
  background-image: var(--pg-bg-aurora);
}
.app-layout {
  height: 100vh;
  overflow: hidden;
  background: var(--pg-bg);
  background-image: var(--pg-bg-aurora);
}
.app-sider {
  height: 100vh;
  overflow: hidden;
  position: sticky;
  top: 0;
  left: 0;
  background: var(--pg-sider-bg);
  backdrop-filter: var(--pg-glass-blur);
  -webkit-backdrop-filter: var(--pg-glass-blur);
  border-right: 1px solid rgba(99, 102, 241, 0.08);
  box-shadow: 1px 0 0 rgba(99, 102, 241, 0.04), 4px 0 24px rgba(31, 27, 75, 0.03);
}
.app-sider :deep(.ant-layout-sider-children) {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 18px 14px 0;
}
.app-menu {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  background: transparent;
  border-inline-end: none !important;
  padding-top: 6px;
}
/* 菜单项:胶囊化 + 左侧激活指示条 */
.app-menu :deep(.ant-menu-item) {
  margin-inline: 0 !important;
  margin-bottom: 3px;
  border-radius: var(--pg-radius);
  font-weight: 500;
  font-size: 14px;
  height: 44px;
  line-height: 44px;
  transition: all 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
  position: relative;
}
.app-menu :deep(.ant-menu-item:hover) {
  background: rgba(99, 102, 241, 0.06) !important;
  color: var(--pg-text) !important;
}
.app-menu :deep(.ant-menu-item-selected) {
  font-weight: 600;
  background: rgba(99, 102, 241, 0.1) !important;
  color: var(--pg-primary-hover) !important;
}
.app-menu :deep(.ant-menu-item-selected)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  border-radius: 999px;
  background: var(--pg-primary);
  box-shadow: 0 0 8px rgba(99, 102, 241, 0.3);
}
.app-menu :deep(.ant-menu-item .anticon) {
  font-size: 16px;
}
.app-sider__calendar {
  flex: 0 0 auto;
  padding: 12px 6px 14px;
  border-top: 1px solid rgba(99, 102, 241, 0.06);
  background: linear-gradient(180deg, transparent, rgba(99, 102, 241, 0.02));
}
.app-main {
  min-height: 0;
  overflow: hidden;
  background: transparent;
  display: flex;
  flex-direction: column;
}
.app-header {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
  padding: 0 clamp(20px, 3vw, 40px);
  flex: 0 0 auto;
  height: 62px;
  line-height: 62px;
  border-bottom: 1px solid rgba(67, 56, 202, 0.08);
  box-shadow: 0 1px 0 rgba(255,255,255,.8), 0 8px 24px rgba(12, 10, 29, 0.025);
  display: flex;
  align-items: center;
}
.app-header__brand {
  display: none;
  align-items: center;
  gap: 8px;
  color: var(--pg-text-heading);
  font-family: var(--pg-font-serif);
  font-weight: 700;
}
.app-header__brand-mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: var(--pg-accent);
  background: var(--pg-primary-soft);
  border: 1px solid #dfe3ff;
}
.app-header__crumb {
  display: flex;
  align-items: center;
  gap: 10px;
}
.app-header__subtitle {
  margin-left: 14px;
  padding-left: 14px;
  border-left: 1px solid var(--pg-border);
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.app-header__title {
  margin: 0;
  font-family: var(--pg-font-serif);
  font-size: 17px;
  font-weight: 600;
  color: var(--pg-text-heading);
  letter-spacing: 0.01em;
}
.app-header__dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--pg-primary);
  opacity: 0.5;
}
.app-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: clamp(20px, 3vw, 34px) clamp(18px, 3.5vw, 44px) clamp(28px, 4vw, 48px);
  display: flex;
  flex-direction: column;
  background: transparent;
  position: relative;
}
.app-content::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image: radial-gradient(circle at 1px 1px, rgba(31, 27, 75, 0.025) 1px, transparent 0);
  background-size: 24px 24px;
  pointer-events: none;
  z-index: 0;
}
.app-content > * {
  position: relative;
  z-index: 1;
}
.app-content__inner {
  background: transparent;
  flex: 1 1 auto;
  width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.app-content.app-content--no-scroll {
  overflow: hidden;
  padding: 0;
  flex: 1 1 0;
  min-height: 0;
}
.app-content__inner.app-content__inner--no-scroll {
  flex: 1 1 0;
  min-height: 0;
  height: auto;
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.logo {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 6px;
  margin-bottom: 10px;
}
.logo--collapsed {
  justify-content: center;
  padding: 0;
}
.logo__mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  background: var(--pg-primary-soft);
  color: var(--pg-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #dfe3ff;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.1);
}
.logo__text {
  font-family: var(--pg-font-serif);
  font-size: 19px;
  font-weight: 700;
  color: var(--pg-text-heading);
  letter-spacing: 0.02em;
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}
.logo__text-sub {
  font-size: 10px;
  font-weight: 500;
  color: var(--pg-text-tertiary);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-top: 2px;
}
.mobile-nav {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  flex: 0 0 calc(62px + env(safe-area-inset-bottom));
  padding: 6px 8px env(safe-area-inset-bottom);
  background: rgba(255, 255, 255, 0.9);
  border-top: 1px solid var(--pg-border);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
  z-index: 20;
}
.mobile-nav__item {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--pg-text-tertiary);
  font: inherit;
  font-size: 11px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  border-radius: 10px;
  cursor: pointer;
}
.mobile-nav__item--active {
  color: var(--pg-accent);
  background: var(--pg-primary-soft);
  font-weight: 600;
}
.mobile-nav__icon {
  font-size: 18px;
}
@media (max-width: 767px) {
  .app-header {
    height: 52px;
    line-height: 52px;
    padding: 0 16px;
    justify-content: space-between;
  }
  .app-header__brand { display: flex; }
  .app-header__dot,
  .app-header__subtitle { display: none; }
  .app-header__title { font-family: var(--pg-font); font-size: 14px; }
  .app-content {
    padding: 14px 12px 18px;
  }
  .app-content.app-content--no-scroll { padding: 0; }
}
.pg-fade-enter-active,
.pg-fade-leave-active {
  transition: opacity 0.18s ease;
}
.pg-fade-enter-from,
.pg-fade-leave-to {
  opacity: 0;
}
</style>
