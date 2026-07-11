<template>
  <div class="pdf-viewer">
    <iframe
      v-if="viewerUrl"
      ref="iframeRef"
      :src="viewerUrl"
      class="pdf-iframe"
      frameborder="0"
      allowfullscreen
      @load="onIframeLoad"
      @error="onIframeError"
    />
    <div v-else-if="loading" class="pdf-loading">
      <a-spin tip="加载 PDF 中…" />
    </div>
    <div v-else-if="error" class="pdf-error">{{ error }}</div>
  </div>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
const props = withDefaults(defineProps<{ src: string; page?: number }>(), {
  page: 1,
})
const emit = defineEmits<{ loaded: []; error: [message: string] }>()
const viewerUrl = ref('')
const loading = ref(false)
const error = ref('')
const iframeRef = ref<HTMLIFrameElement | null>(null)
const viewerPath = '/pdfjs/web/viewer.html'
const iframeReady = ref(false)

const buildViewerUrl = (src: string, page: number) => {
  const p = page && page > 0 ? page : 1
  return `${viewerPath}?file=${encodeURIComponent(src)}#page=${p}`
}

const loadPdf = async () => {
  if (!props.src) return
  loading.value = true; error.value = ''
  iframeReady.value = false
  try {
    viewerUrl.value = buildViewerUrl(props.src, props.page || 1)
  } catch (e) {
    error.value = 'PDF 加载失败: ' + (e as Error).message
    loading.value = false; emit('error', error.value)
  }
}

/**
 * Jump to a page without reloading the iframe. Prefers pdf.js's
 * PDFViewerApplication.page when the iframe is loaded (smooth); falls back to
 * rebuilding the viewer URL with a #page=N hash (reload, but always works).
 */
const gotoPage = (page: number) => {
  const n = page && page > 0 ? Math.floor(page) : 1
  const win = iframeRef.value?.contentWindow as any
  if (iframeReady.value && win && win.PDFViewerApplication) {
    try {
      win.PDFViewerApplication.page = n
      return
    } catch {
      /* fall through to hash rebuild */
    }
  }
  if (props.src) {
    viewerUrl.value = buildViewerUrl(props.src, n)
  }
}

defineExpose({ gotoPage })

const onIframeLoad = () => {
  loading.value = false
  iframeReady.value = true
  emit('loaded')
}
const onIframeError = () => {
  error.value = 'PDF 加载失败'; loading.value = false; emit('error', error.value)
}
watch(() => props.src, loadPdf, { immediate: true })
// External page prop changes (e.g. initial load with page>1) use gotoPage.
watch(
  () => props.page,
  (n) => {
    if (n && n > 0 && iframeReady.value) gotoPage(n)
  },
)
</script>
<style scoped>
.pdf-viewer { width: 100%; height: 100%; background: var(--pg-bg-soft); }
.pdf-iframe { width: 100%; height: 100%; border: none; display: block; }
.pdf-loading, .pdf-error { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--pg-text-secondary); }
.pdf-error { color: #cf1322; }
</style>
