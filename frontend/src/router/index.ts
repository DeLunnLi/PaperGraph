import { createRouter, createWebHistory } from 'vue-router'
import { clearAuth, isUsableToken } from '@/services/api/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/Login.vue'), name: 'login', meta: { public: true } },
    { path: '/', redirect: '/search' },
    { path: '/search', component: () => import('@/views/SearchAgent.vue'), name: 'search' },
    { path: '/daily', component: () => import('@/views/DailyArxiv.vue'), name: 'daily' },
    { path: '/library/read/:id', component: () => import('@/views/PaperReader.vue'), name: 'library-read' },
    { path: '/library', component: () => import('@/views/Library.vue'), name: 'library' },
    { path: '/graph', component: () => import('@/views/KnowledgeGraph.vue'), name: 'graph' },
  ],
})

router.beforeEach((to, _from, next) => {
  if (!to.meta.public && !isUsableToken()) {
    clearAuth()
    next({ path: '/login', query: { redirect: to.fullPath } })
  } else {
    next()
  }
})

export default router
