import { createRouter, createWebHistory } from 'vue-router'

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

// Auth guard: redirect to login if no token
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('pg_token')
  if (!to.meta.public && !token) {
    next('/login')
  } else {
    next()
  }
})

export default router
