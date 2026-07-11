<template>
  <div class="paper-reader">
    <div class="paper-reader__toolbar">
      <a-space>
        <a-button type="link" @click="backToLibrary">← 返回文献库</a-button>
        <a-button v-if="isStandalone" type="link" @click="closeTab">关闭</a-button>
      </a-space>
      <span v-if="paper" class="paper-reader__title">{{ paper.title }}</span>
      <span v-else class="paper-reader__title paper-reader__title--placeholder">文献阅读</span>
      <a-space v-if="paper" class="paper-reader__toolbar-actions">
        <a-dropdown @click.stop>
          <a-button size="small" type="text"><CopyOutlined /> 复制引用</a-button>
          <template #overlay>
            <a-menu @click="onCopyCitation">
              <a-menu-item key="bibtex">BibTeX</a-menu-item>
              <a-menu-item key="apa">APA</a-menu-item>
              <a-menu-item key="plain">纯文本</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </a-space>
    </div>
    <div v-if="loadError" class="paper-reader__err">{{ loadError }}</div>
    <div v-if="pdfParsing" class="paper-reader__notice">PDF 正在解析中，论文全文内容将在稍后可用。你可先基于摘要提问。</div>
    <div ref="splitRef" class="paper-reader__split">
      <div class="paper-reader__pane paper-reader__pane--pdf" :style="leftPaneStyle">
        <PdfJsViewer
          v-if="paperId != null && pdfSrc"
          ref="pdfViewerRef"
          :src="pdfSrc"
          @loaded="onPdfLoaded"
          @error="onPdfError"
          class="pdf-js-viewer-wrapper"
        />
        <div v-else class="paper-reader__pdf-placeholder">
          <a-spin v-if="loadingPaper" tip="正在加载文献信息…" />
          <a-empty v-else-if="loadError" description="PDF 加载失败">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">{{ loadError }}</p>
            </template>
            <a-button type="primary" @click="retryLoadPaper">重新加载</a-button>
          </a-empty>
          <a-empty v-else description="该文献尚无本地 PDF">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">
                可基于摘要与助手对话；可回检索/文献库重新保存以重试下载。
              </p>
            </template>
            <a-button type="link" @click="backToLibrary">返回文献库</a-button>
          </a-empty>
        </div>
      </div>
      <div
        ref="dividerRef"
        class="paper-reader__divider"
        role="separator"
        aria-label="Resize"
        @pointerdown="onDividerPointerDown"
      />
      <div class="paper-reader__pane paper-reader__pane--chat" :style="rightPaneStyle">
        <div
          ref="scrollRef"
          class="paper-reader__messages"
          @wheel.stop="onChatWheel"
          @scroll.stop
        >
          <div v-if="loadingPaper && messages.length === 0" class="paper-reader__msg paper-reader__msg--assistant">
            <div class="paper-reader__avatar paper-reader__avatar--assistant">
              <RobotOutlined />
            </div>
            <div class="paper-reader__bubble paper-reader__bubble--assistant">
              <div class="paper-reader__msg-role">论文阅读助手</div>
              <a-skeleton active :paragraph="{ rows: 3 }" :title="{ width: '60%' }" />
            </div>
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="paper-reader__msg"
            :class="'paper-reader__msg--' + m.role"
          >
            <div v-if="m.role === 'assistant'" class="paper-reader__avatar paper-reader__avatar--assistant">
              <RobotOutlined />
            </div>
            <div v-if="m.role === 'user'" class="paper-reader__bubble paper-reader__bubble--user">
              <div class="paper-reader__msg-body">{{ m.content }}</div>
            </div>
            <div v-else class="paper-reader__bubble paper-reader__bubble--assistant">
              <div class="paper-reader__msg-role">论文阅读助手</div>
              <div class="paper-reader__msg-body" v-html="renderMarkdown(normalizeAssistantText(m.content))"></div>
              <div v-if="m.citations && m.citations.length" class="paper-reader__citations">
                <span class="paper-reader__citations-title">引用锚点</span>
                <div class="paper-reader__citations-list">
                  <button
                    v-for="(c, ci) in m.citations"
                    :key="ci"
                    type="button"
                    class="paper-reader__citation-chip"
                    :title="c.snippet || `跳转到第 ${c.page} 页`"
                    @click.stop="gotoCitationPage(c.page)"
                  >
                    {{ c.marker }}
                  </button>
                </div>
              </div>
              <div v-if="m.related_papers && m.related_papers.length" class="paper-reader__related">
                <div class="paper-reader__related-title">推荐论文</div>
                <ul class="paper-reader__related-cards">
                  <li v-for="(item, index) in m.related_papers" :key="index" class="paper-reader__related-card">
                    <div class="paper-reader__related-card-head">
                      <span class="paper-reader__related-idx">{{ index + 1 }}.</span>
                      <a
                        v-if="paperExternalUrl(item)"
                        class="paper-reader__related-title-link"
                        :href="paperExternalUrl(item)!"
                        target="_blank"
                        rel="noopener noreferrer"
                        @click.stop
                      >
                        {{ item.title || '（无标题）' }}
                      </a>
                      <span v-else class="paper-reader__related-title-link paper-reader__related-title-link--text">
                        {{ item.title || '（无标题）' }}
                      </span>
                    </div>
                    <div v-if="relatedPaperMetaLine(item)" class="paper-reader__related-card-meta">
                      {{ relatedPaperMetaLine(item) }}
                    </div>
                    <div class="paper-reader__related-card-actions">
                      <a-button
                        v-if="paperExternalUrl(item)"
                        type="link"
                        size="small"
                        class="paper-reader__related-act"
                        :href="paperExternalUrl(item)!"
                        target="_blank"
                        rel="noopener noreferrer"
                        @click.stop
                      >
                        打开链接
                      </a-button>
                      <a-button
                        type="primary"
                        size="small"
                        ghost
                        class="paper-reader__related-act"
                        @click.stop="saveRelatedPaperToLibrary(item)"
                      >
                        保存到库
                      </a-button>
                    </div>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>
        <div class="paper-reader__input">
          <a-textarea
            :key="inputKey"
            v-model:value="draft"
            :rows="1"
            :auto-size="{ minRows: 1, maxRows: 6 }"
            placeholder="基于当前文献提问…"
            :disabled="sending"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @press-enter.exact.prevent="send"
          />
          <a-button type="primary" :loading="sending" :disabled="!draft.trim()" aria-label="发送消息" @click="send">发送</a-button>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, watch, nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { RobotOutlined, CopyOutlined } from '@ant-design/icons-vue'
import {
  getPaper,
  getLibraryPdfHref,
  postPaperReaderOpening,
  postPaperReaderChat,
  getPaperReaderHistory,
  postReadingLog,
  savePapers,
} from '@/services/api'
import type { PaperReaderCitation } from '@/services/api/reader'
import type { Paper } from '@/types'
import PdfJsViewer from '@/components/PdfJsViewer.vue'
import { renderMarkdown } from '@/utils/markdown'
const route = useRoute()
const router = useRouter()
const paper = ref<Paper | null>(null)
const loadingPaper = ref(true)
const loadError = ref('')
const pdfParsing = ref(false)
const pdfViewerRef = ref<InstanceType<typeof PdfJsViewer> | null>(null)
const messages = ref<
  {
    role: 'user' | 'assistant'
    content: string
    related_papers?: Paper[]
    citations?: PaperReaderCitation[]
  }[]
>([])
const draft = ref('')
const sending = ref(false)
const composing = ref(false)
const inputKey = ref(0)
const scrollRef = ref<HTMLElement | null>(null)
const splitRef = ref<HTMLElement | null>(null)
const dividerRef = ref<HTMLElement | null>(null)
const leftWidthPx = ref<number | null>(null)
const dragging = ref(false)
const dragPointerId = ref<number | null>(null)
const rafPending = ref(false)
const lastClientX = ref<number | null>(null)
const readingSession = ref<{ paperId: number; startedAtMs: number } | null>(null)
const normalizeAssistantText = (s: string): string => {
  const raw = String(s || '')
  if (raw.includes('```')) return raw.replace(/\r\n/g, '\n')
  return raw
    .replace(/\r\n/g, '\n')
    .replace(/^[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
const paperId = computed(() => {
  const raw = route.params.id
  const n = typeof raw === 'string' ? parseInt(raw, 10) : Array.isArray(raw) ? parseInt(raw[0], 10) : NaN
  return Number.isFinite(n) && n > 0 ? n : null
})
const isStandalone = computed(() => String(route.query?.standalone || '') === '1')
const hasLocalPdfForViewer = computed(
  () => !!(paper.value?.local_pdf_path && String(paper.value.local_pdf_path).trim())
)
const pdfSrc = computed(() => {
  if (paperId.value == null) return ''
  return hasLocalPdfForViewer.value ? getLibraryPdfHref(paperId.value) : ''
})
const onPdfError = (msg: string) => {
  loadError.value = msg || 'PDF 加载失败'
  pdfReady.value = true
  void maybeStartOpening()
}
const pdfReady = ref(false)
const openingStarted = ref(false)
const onPdfLoaded = () => {
  pdfReady.value = true
  void maybeStartOpening()
}
const leftPaneStyle = computed(() => {
  if (leftWidthPx.value == null) return {}
  return { flex: `0 0 ${leftWidthPx.value}px` }
})
const rightPaneStyle = computed(() => ({}))
const backToLibrary = () => {
  router.push('/library')
}
const retryLoadPaper = () => {
  loadError.value = ''
  pdfReady.value = false
  void loadPaper()
}
const closeTab = () => {
  try {
    window.close()
  } catch {
  }
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const onChatWheel = (e: WheelEvent) => {
  e.stopPropagation()
}
const initDefaultSplitIfNeeded = () => {
  if (leftWidthPx.value != null) return
  const el = splitRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (!Number.isFinite(rect.width) || rect.width <= 0) return
  const desiredLeft = rect.width * 0.8
  const minSide = 320
  const maxLeft = Math.max(minSide, rect.width - minSide)
  leftWidthPx.value = clamp(Math.round(desiredLeft), minSide, maxLeft)
}
const setLeftWidthFromClientX = (clientX: number) => {
  const el = splitRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const minSide = 320
  const maxLeft = Math.max(minSide, rect.width - minSide)
  const w = clamp(clientX - rect.left, minSide, maxLeft)
  leftWidthPx.value = w
}
const scheduleDragUpdate = () => {
  if (rafPending.value) return
  rafPending.value = true
  requestAnimationFrame(() => {
    rafPending.value = false
    if (!dragging.value) return
    if (lastClientX.value == null) return
    setLeftWidthFromClientX(lastClientX.value)
  })
}
const onDividerPointerMove = (ev: PointerEvent) => {
  if (!dragging.value) return
  if (dragPointerId.value != null && ev.pointerId !== dragPointerId.value) return
  lastClientX.value = ev.clientX
  scheduleDragUpdate()
}
const endDrag = () => {
  if (!dragging.value) return
  dragging.value = false
  dragPointerId.value = null
  lastClientX.value = null
  rafPending.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', onDividerPointerMove)
  window.removeEventListener('pointerup', onDividerPointerUp)
  window.removeEventListener('pointercancel', onDividerPointerUp)
}
const onDividerPointerUp = (ev: PointerEvent) => {
  if (dragPointerId.value != null && ev.pointerId !== dragPointerId.value) return
  endDrag()
}
const onDividerPointerDown = (ev: PointerEvent) => {
  if (ev.button !== 0) return
  ev.preventDefault()
  dragging.value = true
  dragPointerId.value = ev.pointerId
  lastClientX.value = ev.clientX
  setLeftWidthFromClientX(ev.clientX)
  try {
    dividerRef.value?.setPointerCapture(ev.pointerId)
  } catch {
  }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', onDividerPointerMove)
  window.addEventListener('pointerup', onDividerPointerUp)
  window.addEventListener('pointercancel', onDividerPointerUp)
}
onBeforeUnmount(() => {
  endDrag()
  void flushReadingSession()
})
const flushReadingSession = async () => {
  const s = readingSession.value
  if (!s) return
  readingSession.value = null
  const durMs = Date.now() - s.startedAtMs
  const sec = Math.floor(durMs / 1000)
  if (!Number.isFinite(sec) || sec < 8) return
  try {
    await postReadingLog({ paper_id: s.paperId, duration_sec: Math.min(sec, 60 * 60 * 6), client_ts: Math.floor(Date.now() / 1000) })
  } catch {
  }
}
const scrollBottom = async () => {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}
const gotoCitationPage = (page: number) => {
  if (!page || page < 1) return
  pdfViewerRef.value?.gotoPage(page)
}
const mapHistoryTurns = (
  turns: { role?: string; content?: string | null; created_at?: number }[]
): { role: 'user' | 'assistant'; content: string; related_papers?: Paper[] }[] =>
  turns
    .filter((t) => t && (t.role === 'user' || t.role === 'assistant') && String(t.content || '').trim())
    .map((t) => {
      const role = t.role as 'user' | 'assistant'
      const content = role === 'assistant' ? normalizeAssistantText(String(t.content)) : String(t.content)
      return { role, content }
    })
const ensureOpeningAndHistory = async (reloadHistory = false, showError = true) => {
  if (paperId.value == null) return
  try {
    const res = await postPaperReaderOpening(paperId.value)
    if (!res.success || !res.opening) return
    if (res.pdf_parsing) pdfParsing.value = true
    if (reloadHistory) {
      const h = await getPaperReaderHistory(paperId.value, 200)
      if (h?.success && Array.isArray(h.turns) && h.turns.length > 0) {
        const restored = mapHistoryTurns(h.turns)
        if (restored.length > 0) {
          messages.value = restored
          await scrollBottom()
          return
        }
      }
    }
    const hasAssistantMessage = messages.value.some((m) => m.role === 'assistant')
    if (!hasAssistantMessage) {
      messages.value.push({ role: 'assistant', content: normalizeAssistantText(res.opening) })
      await scrollBottom()
    }
  } catch (e: unknown) {
    if (showError) {
      message.error((e as Error).message || '导读加载失败')
    }
  }
}
const maybeStartOpening = async (reloadHistory = false, showError = true) => {
  if (openingStarted.value) return
  if (paperId.value == null) return
  if (hasLocalPdfForViewer.value && !pdfReady.value) return
  openingStarted.value = true
  void ensureOpeningAndHistory(reloadHistory, showError)
}
const send = async () => {
  const text = draft.value.trim()
  if (!text || paperId.value == null || sending.value) return
  if (composing.value) return
  sending.value = true
  messages.value.push({ role: 'user', content: text })
  draft.value = ''
  inputKey.value += 1
  await nextTick()
  await scrollBottom()
  try {
    const res = await postPaperReaderChat({
      paper_id: paperId.value,
      messages: messages.value.slice(0, -1),
      user_message: text,
    })
    if (res.success && res.reply) {
      const rp = Array.isArray((res as any).related_papers) ? ((res as any).related_papers as Paper[]) : []
      const cites = Array.isArray((res as any).citations) ? ((res as any).citations as PaperReaderCitation[]) : []
      messages.value.push({
        role: 'assistant',
        content: normalizeAssistantText(res.reply),
        related_papers: rp.length ? rp : undefined,
        citations: cites.length ? cites : undefined,
      })
    } else {
      messages.value.push({ role: 'assistant', content: '（无回复）' })
    }
  } catch (e: unknown) {
    message.error((e as Error).message || '发送失败')
    messages.value.push({ role: 'assistant', content: '请求失败，请检查网络或 LLM 配置。' })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}
const paperExternalUrl = (p: Paper): string | null => {
  if (!p) return null
  const src = String(p.source_url || '').trim()
  if (src && /^https?:\/\//i.test(src)) return src
  const pdf = String(p.pdf_url || '').trim()
  if (pdf && /^https?:\/\//i.test(pdf)) return pdf
  let ax = String(p.arxiv_id || '').trim()
  if (ax) {
    ax = ax.replace(/^arxiv:/i, '').replace(/\.pdf$/i, '')
    return `https://arxiv.org/abs/${ax}`
  }
  const doiRaw = String(p.doi || '').trim()
  if (doiRaw) {
    if (/^https?:\/\//i.test(doiRaw)) return doiRaw
    return `https://doi.org/${doiRaw.replace(/^doi:/i, '')}`
  }
  return null
}
const relatedPaperMetaLine = (p: Paper): string => {
  const parts: string[] = []
  const names = (p.authors || []).map((a: { name?: string }) => a?.name).filter(Boolean) as string[]
  if (names.length) parts.push(names.slice(0, 4).join(', ') + (names.length > 4 ? '…' : ''))
  if (p.year != null) parts.push(String(p.year))
  const j = String((p as { journal?: string }).journal || (p as { venue?: string }).venue || '').trim()
  if (j) parts.push(j)
  let s = parts.join(' · ')
  if (s.length > 140) s = `${s.slice(0, 137)}…`
  return s
}
const saveRelatedPaperToLibrary = async (p: Paper) => {
  if (!p) return
  try {
    await savePapers([p], { llm_classify: false })
    message.success('已保存到文献库')
  } catch (e: unknown) {
    message.error((e as Error).message || '保存失败')
  }
}
function escapeBib(s: string): string {
  return String(s || '').replace(/([&%$#_{}~^\\])/g, '\\$1')
}
function generateBibTeX(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean).join(' and ')
  const year = p.year ?? ''
  const key = `${(p.authors?.[0]?.name || 'unknown').split(' ').pop()?.toLowerCase() || 'unknown'}${year}`
  const lines = [`@article{${key},`]
  if (authors) lines.push(`  author = {${escapeBib(authors)}},`)
  if (p.title) lines.push(`  title = {${escapeBib(p.title)}},`)
  if (p.journal || p.venue) lines.push(`  journal = {${escapeBib(String(p.journal || p.venue))}},`)
  if (year) lines.push(`  year = {${year}},`)
  if (p.doi) lines.push(`  doi = {${p.doi}},`)
  if (p.arxiv_id) lines.push(`  eprint = {${p.arxiv_id}},`)
  if (p.source_url) lines.push(`  url = {${p.source_url}},`)
  lines.push('}')
  return lines.join('\n')
}
function generateAPA(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean)
  const authorStr = authors.length > 0
    ? authors.length <= 3
      ? authors.join(', ') + (authors.length === 2 ? ' & ' : authors.length === 1 ? '' : ', & ')
      : authors[0] + ', et al.'
    : ''
  const year = p.year ? `(${p.year})` : ''
  const title = p.title || ''
  const venue = p.journal || p.venue || ''
  const doi = p.doi ? ` https://doi.org/${p.doi}` : ''
  return [authorStr, year, title, venue, doi].filter(Boolean).join('. ') + '.'
}
const onCopyCitation = async ({ key }: { key: string }) => {
  if (!paper.value) return
  let text = ''
  if (key === 'bibtex') text = generateBibTeX(paper.value)
  else if (key === 'apa') text = generateAPA(paper.value)
  else text = `${paper.value.title || ''}\n${(paper.value.authors || []).map((a) => a.name).join(', ')}\n${paper.value.journal || paper.value.venue || ''} ${paper.value.year ?? ''}\n${paper.value.doi ? 'DOI: ' + paper.value.doi : ''}`
  try {
    await navigator.clipboard.writeText(text)
    message.success(`已复制${key === 'bibtex' ? ' BibTeX' : key === 'apa' ? ' APA' : ''}引用`)
  } catch {
    message.error('复制失败，请手动选择文本复制')
  }
}
const loadPaper = async () => {
  await flushReadingSession()
  loadingPaper.value = true
  loadError.value = ''
  paper.value = null
  pdfReady.value = false
  openingStarted.value = false
  messages.value = []
  await nextTick()
  initDefaultSplitIfNeeded()
  if (paperId.value == null) {
    loadError.value = '无效的文献 ID'
    loadingPaper.value = false
    return
  }
  readingSession.value = { paperId: paperId.value, startedAtMs: Date.now() }
  try {
    paper.value = await getPaper(paperId.value)
    try {
      const h = await getPaperReaderHistory(paperId.value, 200)
      if (h?.success && Array.isArray(h.turns) && h.turns.length > 0) {
        const restored = mapHistoryTurns(h.turns)
        if (restored.length > 0) {
          messages.value = restored
          await scrollBottom()
        }
      }
    } catch {
    }
    if (messages.value.length === 0) {
      void maybeStartOpening()
    } else if (messages.value[0]?.role === 'user') {
      void maybeStartOpening(true, false)
    }
  } catch (e: unknown) {
    loadError.value = (e as Error).message || '加载失败'
  } finally {
    loadingPaper.value = false
  }
}
watch(
  () => route.params.id,
  () => {
    void loadPaper()
  },
  { immediate: true }
)
onMounted(() => {
  initDefaultSplitIfNeeded()
})
</script>
<style scoped>
.paper-reader {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100vh;
  color-scheme: light;
}
.paper-reader__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-bottom: 1px solid var(--pg-divider);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
}
.paper-reader__title--placeholder {
  color: var(--pg-text-tertiary);
  font-weight: 500;
}
.paper-reader__title {
  flex: 1;
  min-width: 120px;
  font-family: var(--pg-font-serif);
  font-weight: 600;
  font-size: 16px;
  color: var(--pg-text-heading);
  line-height: 1.4;
  letter-spacing: 0.005em;
}
.paper-reader__toolbar-actions {
  flex-shrink: 0;
}
.paper-reader__err {
  color: #cf1322;
  padding: 16px;
}
.paper-reader__pdf-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
}
.paper-reader__split {
  display: flex;
  flex-direction: row;
  flex: 1;
  min-height: 0;
  height: calc(100vh - 56px);
}
.paper-reader__pane {
  flex: 1;
  min-width: clamp(200px, 30vw, 280px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--pg-surface);
}
.paper-reader__pane--pdf {
  min-height: 0;
  border-right: 1px solid var(--pg-divider);
}
.paper-reader__divider {
  flex: 0 0 6px;
  cursor: col-resize;
  background: transparent;
  position: relative;
  touch-action: none;
}
.paper-reader__divider::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--pg-divider);
  transition: background 0.15s ease;
}
.paper-reader__divider:hover::before {
  background: var(--pg-primary);
  opacity: 0.4;
}
.pdf-js-viewer-wrapper {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.paper-reader__pane--chat {
  min-height: 0;
  max-height: calc(100vh - 56px);
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--pg-bg-soft);
  overflow: hidden;
}
.paper-reader__messages {
  flex: 1;
  min-height: 0;
  max-height: calc(100vh - 56px - 60px - 16px);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 18px;
  background: transparent;
  margin: 12px;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: rgba(91,100,114,0.25) transparent;
}
.paper-reader__messages::-webkit-scrollbar {
  width: 8px;
}
.paper-reader__messages::-webkit-scrollbar-track {
  background: transparent;
}
.paper-reader__messages::-webkit-scrollbar-thumb {
  background: rgba(91,100,114,0.22);
  border-radius: 999px;
}
.paper-reader__messages::-webkit-scrollbar-thumb:hover {
  background: rgba(91,100,114,0.4);
}
.paper-reader__messages::-webkit-scrollbar-button {
  display: none;
}
.paper-reader__msg {
  margin-bottom: 18px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.paper-reader__msg--user {
  justify-content: flex-end;
}
.paper-reader__avatar {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 9px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  margin-top: 2px;
}
.paper-reader__avatar--assistant {
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  color: var(--pg-primary);
  box-shadow: var(--pg-shadow-xs);
}
.paper-reader__bubble {
  max-width: 82%;
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.65;
  min-width: 0;
}
.paper-reader__bubble--user {
  background: var(--pg-primary);
  color: var(--pg-text-inverse);
  border-radius: 14px 14px 4px 14px;
  box-shadow: 0 4px 14px rgba(30, 27, 75, 0.18);
}
.paper-reader__bubble--assistant {
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: 4px 14px 14px 14px;
  box-shadow: var(--pg-shadow-sm);
  color: var(--pg-text);
}
.paper-reader__msg-role {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 4px;
  font-weight: 500;
}
.paper-reader__msg-body {
  line-height: 1.6;
  font-size: 14px;
  color: var(--pg-text);
}
.paper-reader__bubble--user .paper-reader__msg-body {
  color: var(--pg-text-inverse);
  white-space: pre-wrap;
}
.paper-reader__msg-body :deep(h1),
.paper-reader__msg-body :deep(h2),
.paper-reader__msg-body :deep(h3) {
  margin: 10px 0 6px;
  line-height: 1.25;
}
.paper-reader__msg-body :deep(h1) {
  font-size: 18px;
}
.paper-reader__msg-body :deep(h2) {
  font-size: 16px;
}
.paper-reader__msg-body :deep(h4),
.paper-reader__msg-body :deep(h5),
.paper-reader__msg-body :deep(h6) {
  margin: 8px 0 4px;
  line-height: 1.3;
  font-size: 14px;
  font-weight: 600;
  color: var(--pg-text-heading);
}
.paper-reader__msg-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--pg-divider);
  margin: 12px 0;
}
.paper-reader__msg-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.45;
  display: block;
  overflow-x: auto;
  max-width: 100%;
  -webkit-overflow-scrolling: touch;
}
.paper-reader__msg-body :deep(thead th) {
  background: var(--pg-bg-soft);
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.paper-reader__msg-body :deep(th),
.paper-reader__msg-body :deep(td) {
  border: 1px solid var(--pg-border);
  padding: 6px 8px;
  vertical-align: top;
  word-break: break-word;
}
.paper-reader__msg-body :deep(tbody tr:nth-child(even)) {
  background: var(--pg-bg-soft);
}
.paper-reader__msg-body :deep(ul),
.paper-reader__msg-body :deep(ol) {
  margin: 4px 0 4px 18px;
  padding: 0;
}
.paper-reader__msg-body :deep(li) {
  margin: 2px 0;
}
.paper-reader__msg-body :deep(code) {
  background: var(--pg-bg-soft);
  border-radius: 4px;
  padding: 1px 4px;
  font-size: 12px;
}
.paper-reader__msg-body :deep(pre) {
  background: var(--pg-bg-soft);
  border-radius: 8px;
  padding: 10px;
  overflow: auto;
  margin: 8px 0;
}
.paper-reader__msg-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.paper-reader__related {
  border-top: 1px solid var(--pg-divider);
  margin-top: 10px;
  padding-top: 10px;
}
.paper-reader__citations {
  margin-top: 8px;
}
.paper-reader__citations-title {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 6px;
  display: block;
}
.paper-reader__citations-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.paper-reader__citation-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--pg-primary-hover, #4f46e5);
  background: var(--pg-primary-soft, #eef0ff);
  border: 1px solid transparent;
  border-radius: var(--pg-radius-pill, 999px);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.paper-reader__citation-chip:hover {
  background: var(--pg-primary, #6366f1);
  color: var(--pg-text-inverse);
}
.paper-reader__related-title {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 8px;
}
.paper-reader__related-cards {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.paper-reader__related-card {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius);
  padding: 10px 12px;
  background: var(--pg-bg-soft);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.paper-reader__related-card:hover {
  border-color: #d9ddf5;
  box-shadow: var(--pg-shadow-sm);
}
.paper-reader__related-card-head {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.4;
}
.paper-reader__related-idx {
  flex-shrink: 0;
  color: var(--pg-primary);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}
.paper-reader__related-title-link {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--pg-text);
  text-decoration: none;
  word-break: break-word;
}
.paper-reader__related-title-link:hover {
  color: var(--pg-primary-hover);
}
.paper-reader__related-title-link--text {
  color: var(--pg-text);
  cursor: default;
}
.paper-reader__related-card-meta {
  color: var(--pg-text-tertiary);
  font-size: 12px;
  line-height: 1.35;
  margin-top: 4px;
  padding-left: 1.5em;
}
.paper-reader__related-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  margin-top: 8px;
  padding-left: 1.5em;
}
.paper-reader__related-act.ant-btn-sm {
  height: auto;
  line-height: 1.35;
}
.paper-reader__input {
  display: flex;
  gap: 8px;
  padding: 12px 14px;
  background: var(--pg-surface);
  border-top: 1px solid var(--pg-divider);
  align-items: center;
}
.paper-reader__input :deep(.ant-input) {
  flex: 1;
  border-radius: var(--pg-radius);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.paper-reader__input :deep(.ant-input:focus),
.paper-reader__input :deep(textarea.ant-input:focus) {
  border-color: var(--pg-primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
}
.paper-reader__input :deep(textarea.ant-input) {
  min-height: 36px;
  height: 36px;
  line-height: 22px;
  resize: none;
  padding: 6px 12px;
}
@media (max-width: 900px) {
  .paper-reader__split {
  flex-direction: column;
  gap: 12px;
  }
  .paper-reader__divider {
  display: none;
  }
}
</style>