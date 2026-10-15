import { createApp } from 'vue'
// Deep per-component imports: the package root (es/index.js) re-exports every
// component through a dynamically-accessed namespace, which defeats Rollup
// tree-shaking and eagerly bundles heavy route-specific pieces (Table ~400 kB).
// Only components rendered by the eager app shell (App.vue) are registered
// globally; every view imports the rest locally so they chunk with the route.
import ConfigProvider from 'ant-design-vue/es/config-provider'
import Layout from 'ant-design-vue/es/layout'
import Menu from 'ant-design-vue/es/menu'
import 'ant-design-vue/dist/reset.css'
import './styles/theme.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(router)
const comps = [ConfigProvider, Layout, Menu]
comps.forEach((c) => app.use(c))

// Global error handler — catches unhandled errors in component lifecycle, watchers, etc.
app.config.errorHandler = (err, _instance, info) => {
  console.error('[Vue Error]', info, err)
}

// Catch unhandled Promise rejections (e.g. failed API calls not wrapped in try/catch)
window.addEventListener('unhandledrejection', (event) => {
  console.error('[Unhandled Promise]', event.reason)
  event.preventDefault()
})

app.mount('#app')
