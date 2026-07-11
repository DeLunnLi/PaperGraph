import { createApp } from 'vue'
import {
  Alert, Button, Card, Collapse, ConfigProvider, Empty, Input, Layout,
  List, Menu, Select, Space, Spin, Table, Tag, Tooltip,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import './styles/theme.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(router)
const comps = [Alert, Button, Card, Collapse, ConfigProvider, Empty, Input, Layout,
  List, Menu, Select, Space, Spin, Table, Tag, Tooltip]
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