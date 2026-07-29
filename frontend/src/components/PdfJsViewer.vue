<template>
  <div class="pdf-viewer">
    <div class="pdf-toolbar">
      <div class="pdf-toolbar__group">
        <span class="pdf-toolbar__label">连续阅读</span>
        <span class="page-indicator">
          <a-input-number
            v-model:value="pageInput"
            size="small"
            :min="1"
            :max="pageCount || 1"
            :disabled="loading || pageCount === 0"
            @press-enter="applyPageInput"
            @blur="applyPageInput"
          />
          <span>/ {{ pageCount || '—' }} 页</span>
        </span>
      </div>
      <div class="pdf-toolbar__group pdf-toolbar__zoom">
        <a-button size="small" type="text" :disabled="loading" aria-label="缩小" @click="changeZoom(-0.15)">−</a-button>
        <span class="zoom-label">{{ Math.round(scale * 100) }}%</span>
        <a-button size="small" type="text" :disabled="loading" aria-label="放大" @click="changeZoom(0.15)">＋</a-button>
        <a-button size="small" :disabled="loading" @click="fitWidth">适应宽度</a-button>
      </div>
    </div>

    <div ref="stageRef" class="pdf-stage" @scroll.passive="onStageScroll">
      <div v-if="loading" class="pdf-state">
        <a-spin tip="正在加载论文 PDF…" />
      </div>
      <div v-else-if="error" class="pdf-state pdf-error">
        <div>{{ error }}</div>
        <a-button size="small" type="primary" @click="loadDocument">重新加载</a-button>
      </div>
      <div v-else class="pages-flow">
        <section
          v-for="item in pages"
          :key="item.number"
          :ref="(el) => setPageElement(item.number, el as HTMLElement | null)"
          class="pdf-page"
          :data-page="item.number"
          :style="{ width: `${item.width}px`, minHeight: `${item.height}px` }"
        >
          <canvas
            :ref="(el) => setCanvasElement(item.number, el as HTMLCanvasElement | null)"
            class="pdf-canvas"
            :class="{ 'pdf-canvas--ready': item.rendered }"
          />
          <div v-if="!item.rendered" class="pdf-page__loading">
            <a-spin size="small" />
          </div>
          <span class="pdf-page__number">{{ item.number }}</span>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

// Lazy-load pdfjs-dist (~364KB) only when a document actually loads, instead of
// evaluating it eagerly with the route chunk. The worker URL is a tiny string
// asset resolved at build time.
let getDocument: typeof import('pdfjs-dist')['getDocument']
let pdfjsReady: Promise<void> | null = null
function ensurePdfjs(): Promise<void> {
  if (!pdfjsReady) {
    pdfjsReady = import('pdfjs-dist').then((mod) => {
      getDocument = mod.getDocument
      mod.GlobalWorkerOptions.workerSrc = workerUrl
    })
  }
  return pdfjsReady
}

interface PageState {
  number: number
  width: number
  height: number
  rendered: boolean
  rendering: boolean
}

const props = withDefaults(defineProps<{ src: string; page?: number }>(), { page: 1 })
const emit = defineEmits<{ loaded: []; error: [message: string] }>()

const stageRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const currentPage = ref(1)
const pageInput = ref(1)
const pageCount = ref(0)
const scale = ref(1.15)
const pages = ref<PageState[]>([])

let pdfDocument: PDFDocumentProxy | null = null
let loadingTask: PDFDocumentLoadingTask | null = null
let observer: IntersectionObserver | null = null
let loadGeneration = 0
let loadedEmitted = false
let scrollRaf = 0
let resizeObserver: ResizeObserver | null = null
let fitWidthTimer: ReturnType<typeof setTimeout> | null = null
const pageElements = new Map<number, HTMLElement>()
const canvasElements = new Map<number, HTMLCanvasElement>()
const renderTasks = new Map<number, RenderTask>()

const normalizePage = (page: number) => {
  const upper = Math.max(1, pageCount.value || 1)
  return Math.max(1, Math.min(upper, Math.floor(Number(page) || 1)))
}

const setPageElement = (page: number, el: HTMLElement | null) => {
  if (el) pageElements.set(page, el)
  else pageElements.delete(page)
}
const setCanvasElement = (page: number, el: HTMLCanvasElement | null) => {
  if (el) canvasElements.set(page, el)
  else canvasElements.delete(page)
}

const renderPage = async (pageNumber: number) => {
  const document = pdfDocument
  const item = pages.value[pageNumber - 1]
  if (!document || !item || item.rendered || item.rendering) return
  const generation = loadGeneration
  item.rendering = true
  try {
    const page = await document.getPage(pageNumber)
    if (generation !== loadGeneration) return
    const viewport = page.getViewport({ scale: scale.value })
    const canvas = canvasElements.get(pageNumber)
    if (!canvas) return
    const ratio = Math.max(1, Math.min(1.5, window.devicePixelRatio || 1))
    canvas.width = Math.ceil(viewport.width * ratio)
    canvas.height = Math.ceil(viewport.height * ratio)
    canvas.style.width = `${Math.ceil(viewport.width)}px`
    canvas.style.height = `${Math.ceil(viewport.height)}px`
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器无法创建 PDF 画布')
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    const task = page.render({ canvasContext: context, viewport })
    renderTasks.set(pageNumber, task)
    await task.promise
    if (generation !== loadGeneration) return
    item.rendered = true
    if (!loadedEmitted && pageNumber === normalizePage(props.page || 1)) {
      loadedEmitted = true
      emit('loaded')
    }
  } catch (e) {
    if ((e as Error).name !== 'RenderingCancelledException' && generation === loadGeneration) {
      error.value = `第 ${pageNumber} 页渲染失败：${(e as Error).message || '未知错误'}`
    }
  } finally {
    item.rendering = false
    renderTasks.delete(pageNumber)
  }
}

const connectObserver = () => {
  observer?.disconnect()
  const root = stageRef.value
  if (!root) return
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const page = Number((entry.target as HTMLElement).dataset.page)
        if (page > 0) void renderPage(page)
      }
    },
    { root, rootMargin: '650px 0px', threshold: 0.01 },
  )
  for (const el of pageElements.values()) observer.observe(el)
}

const buildPageLayout = async () => {
  if (!pdfDocument) return
  const first = await pdfDocument.getPage(1)
  const viewport = first.getViewport({ scale: scale.value })
  pages.value = Array.from({ length: pdfDocument.numPages }, (_, index) => ({
    number: index + 1,
    width: Math.ceil(viewport.width),
    height: Math.ceil(viewport.height),
    rendered: false,
    rendering: false,
  }))
  await nextTick()
  connectObserver()
  await gotoPage(props.page || 1, false)
}

const disposeDocument = async () => {
  loadGeneration += 1
  observer?.disconnect()
  observer = null
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
  if (fitWidthTimer) clearTimeout(fitWidthTimer)
  scrollRaf = 0
  fitWidthTimer = null
  for (const task of renderTasks.values()) {
    try { task.cancel() } catch { /* completed task */ }
  }
  renderTasks.clear()
  pageElements.clear()
  for (const canvas of canvasElements.values()) {
    canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
  }
  canvasElements.clear()
  try { await loadingTask?.destroy() } catch { /* no-op */ }
  loadingTask = null
  if (pdfDocument) {
    try { await pdfDocument.destroy() } catch { /* no-op */ }
  }
  pdfDocument = null
  pages.value = []
}

const loadDocument = async () => {
  const src = String(props.src || '').trim()
  await disposeDocument()
  error.value = ''
  pageCount.value = 0
  loadedEmitted = false
  if (!src) return

  let url: URL
  try {
    url = new URL(src, window.location.origin)
    if (url.origin !== window.location.origin) throw new Error('仅允许加载同源 PDF')
  } catch (e) {
    error.value = `PDF 地址无效：${(e as Error).message}`
    emit('error', error.value)
    return
  }

  loading.value = true
  const generation = loadGeneration
  try {
    await ensurePdfjs()
    if (generation !== loadGeneration) return
    loadingTask = getDocument({ url: url.href, withCredentials: false })
    const document = await loadingTask.promise
    if (generation !== loadGeneration) {
      await document.destroy()
      return
    }
    pdfDocument = document
    pageCount.value = document.numPages
    currentPage.value = normalizePage(props.page || 1)
    pageInput.value = currentPage.value
    loading.value = false
    await buildPageLayout()
  } catch (e) {
    if (generation !== loadGeneration) return
    loading.value = false
    error.value = `PDF 加载失败：${(e as Error).message || '文件不可用'}`
    emit('error', error.value)
  }
}

const gotoPage = async (page: number, smooth = true) => {
  const target = normalizePage(page)
  currentPage.value = target
  pageInput.value = target
  await renderPage(target)
  pageElements.get(target)?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' })
}
const applyPageInput = () => void gotoPage(pageInput.value)

const updateCurrentPageFromScroll = () => {
  const root = stageRef.value
  if (!root || pages.value.length === 0) return
  const focusY = root.scrollTop + 24
  let nearestPage = currentPage.value
  let nearestDistance = Number.POSITIVE_INFINITY
  for (const [page, el] of pageElements) {
    const distance = Math.abs(el.offsetTop - focusY)
    if (distance < nearestDistance) {
      nearestDistance = distance
      nearestPage = page
    }
  }
  if (nearestPage !== currentPage.value) {
    currentPage.value = nearestPage
    pageInput.value = nearestPage
  }
}
const onStageScroll = () => {
  if (scrollRaf) return
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0
    updateCurrentPageFromScroll()
  })
}

const rerenderForScale = async () => {
  for (const task of renderTasks.values()) {
    try { task.cancel() } catch { /* completed task */ }
  }
  renderTasks.clear()
  if (!pdfDocument) return
  const first = await pdfDocument.getPage(1)
  const viewport = first.getViewport({ scale: scale.value })
  for (const item of pages.value) {
    item.width = Math.ceil(viewport.width)
    item.height = Math.ceil(viewport.height)
    item.rendered = false
    item.rendering = false
    const canvas = canvasElements.get(item.number)
    if (canvas) canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
  }
  await nextTick()
  connectObserver()
  await gotoPage(currentPage.value, false)
}
const changeZoom = async (delta: number) => {
  scale.value = Math.max(0.5, Math.min(2.5, Number((scale.value + delta).toFixed(2))))
  await rerenderForScale()
}
const fitWidth = async () => {
  if (!pdfDocument || !stageRef.value) return
  const page = await pdfDocument.getPage(currentPage.value)
  const natural = page.getViewport({ scale: 1 })
  const available = Math.max(240, stageRef.value.clientWidth - 56)
  const nextScale = Math.max(0.5, Math.min(2.5, Number((available / natural.width).toFixed(2))))
  if (Math.abs(nextScale - scale.value) < 0.02) return
  scale.value = nextScale
  await rerenderForScale()
}
const scheduleFitWidth = () => {
  if (!pdfDocument) return
  if (fitWidthTimer) clearTimeout(fitWidthTimer)
  fitWidthTimer = setTimeout(() => {
    fitWidthTimer = null
    void fitWidth()
  }, 180)
}

defineExpose({ gotoPage })
watch(() => props.src, () => { void loadDocument() }, { immediate: true })
watch(stageRef, (stage) => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (!stage || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(scheduleFitWidth)
  resizeObserver.observe(stage)
})
watch(() => props.page, (page) => {
  if (pdfDocument && page && page !== currentPage.value) void gotoPage(page)
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  void disposeDocument()
})
</script>

<style scoped>
.pdf-viewer { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; background: #dedfe5; }
.pdf-toolbar { flex: 0 0 auto; min-height: 52px; padding: 8px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; background: rgba(255, 255, 255, 0.94); backdrop-filter: blur(14px); border-bottom: 1px solid var(--pg-divider); box-shadow: 0 1px 5px rgba(12, 10, 29, 0.08); z-index: 2; }
.pdf-toolbar__group { display: flex; align-items: center; gap: 10px; }
.pdf-toolbar__label { font-size: 12px; font-weight: 650; color: var(--pg-text-secondary); letter-spacing: 0.04em; }
.page-indicator { display: inline-flex; align-items: center; gap: 6px; color: var(--pg-text-tertiary); font-size: 12px; }
.page-indicator :deep(.ant-input-number) { width: 58px; }
.pdf-toolbar__zoom :deep(.ant-btn-text) { width: 28px; padding-inline: 0; font-size: 18px; }
.zoom-label { min-width: 42px; text-align: center; color: var(--pg-text-secondary); font-size: 12px; font-variant-numeric: tabular-nums; }
.pdf-stage { flex: 1 1 auto; min-height: 0; overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable; scroll-anchor: none; -webkit-overflow-scrolling: touch; }
.pdf-state { min-height: 100%; display: flex; flex-direction: column; gap: 14px; align-items: center; justify-content: center; color: var(--pg-text-secondary); }
.pdf-error { color: #cf1322; padding: 24px; text-align: center; }
.pages-flow { width: max-content; min-width: 100%; box-sizing: border-box; padding: 30px 32px 84px; display: flex; flex-direction: column; align-items: center; gap: 24px; }
.pdf-page { position: relative; flex: 0 0 auto; background: #fff; box-shadow: 0 6px 20px rgba(12, 10, 29, 0.14), 0 1px 5px rgba(12,10,29,.07); border-radius: 2px; overflow: hidden; scroll-margin-top: 14px; contain: layout paint style; content-visibility: auto; contain-intrinsic-size: auto 1000px; }
.pdf-canvas { display: block; opacity: 0; transition: opacity 0.12s ease; }
.pdf-canvas--ready { opacity: 1; }
.pdf-page__loading { position: absolute; inset: 0; display: grid; place-items: center; color: var(--pg-text-tertiary); background: linear-gradient(135deg, #fff, #fafafa); }
.pdf-page__number { position: absolute; right: 10px; bottom: 8px; min-width: 24px; height: 24px; padding: 0 6px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; color: #fff; background: rgba(12, 10, 29, 0.5); font-size: 11px; font-variant-numeric: tabular-nums; opacity: 0; transition: opacity 0.15s ease; }
.pdf-page:hover .pdf-page__number { opacity: 1; }
@media (max-width: 720px) {
  .pdf-toolbar { align-items: flex-start; flex-direction: column; }
  .pdf-toolbar__label { display: none; }
  .pdf-toolbar__zoom { flex-wrap: wrap; }
  .pages-flow { padding: 14px 10px 48px; gap: 12px; }
}
</style>
