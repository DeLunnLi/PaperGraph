<template>
  <div
    class="paper-reader"
    :class="{
      'paper-reader--compact': compactLayout,
      'paper-reader--coarse-pointer': coarsePointer,
      'paper-reader--hover-capable': hoverCapable,
    }"
    :data-layout="compactLayout ? 'tabs' : 'split'"
  >
    <div class="paper-reader__toolbar">
      <a-space>
        <a-button type="link" @click="backToLibrary">← 返回文献库</a-button>
        <a-button v-if="isStandalone" type="link" @click="closeTab">关闭</a-button>
      </a-space>
      <span v-if="paper" class="paper-reader__title">{{ paper.title }}</span>
      <span v-else class="paper-reader__title paper-reader__title--placeholder">文献阅读</span>
      <div class="paper-reader__mobile-switch" role="tablist" aria-label="阅读区域">
        <button type="button" role="tab" :aria-selected="mobilePane === 'pdf'" :class="{ 'is-active': mobilePane === 'pdf' }" @click="mobilePane = 'pdf'">论文</button>
        <button type="button" role="tab" :aria-selected="mobilePane === 'chat'" :class="{ 'is-active': mobilePane === 'chat' }" @click="mobilePane = 'chat'">助手</button>
      </div>
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
      <div class="paper-reader__pane paper-reader__pane--pdf" :class="{ 'paper-reader__pane--mobile-hidden': mobilePane !== 'pdf' }" :style="leftPaneStyle">
        <PdfJsViewer
          v-if="paperId != null && pdfSrc"
          ref="pdfViewerRef"
          :src="pdfSrc"
          @loaded="onPdfLoaded"
          @error="onPdfError"
          class="pdf-js-viewer-wrapper"
        />
        <div v-else class="paper-reader__pdf-placeholder">
          <a-spin v-if="acquiringPdf" tip="正在安全获取论文原文 PDF…" />
          <a-spin v-else-if="loadingPaper" tip="正在加载文献信息…" />
          <a-empty v-else-if="loadError" description="PDF 加载失败">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">{{ loadError }}</p>
            </template>
            <a-button type="primary" @click="retryLoadPaper">重新加载</a-button>
          </a-empty>
          <a-empty v-else description="该文献尚无本地 PDF">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">
                {{ pdfAcquireError || '可以立即尝试从 arXiv、DOI 或论文原文链接获取 PDF。' }}
              </p>
            </template>
            <a-space>
              <a-button type="primary" :loading="acquiringPdf" @click="acquirePdf">获取论文 PDF</a-button>
              <a-button v-if="paperExternalUrl(paper)" type="link" :href="paperExternalUrl(paper)!" target="_blank" rel="noopener noreferrer">
                打开原文链接
              </a-button>
            </a-space>
          </a-empty>
        </div>
      </div>
      <div
        ref="dividerRef"
        class="paper-reader__divider"
        role="separator"
        aria-label="调整论文与助手的宽度"
        aria-orientation="vertical"
        :aria-valuenow="dividerPercent"
        aria-valuemin="40"
        aria-valuemax="75"
        tabindex="0"
        @keydown="onDividerKeydown"
        @pointerdown="onDividerPointerDown"
      />
      <div class="paper-reader__pane paper-reader__pane--chat" :class="{ 'paper-reader__pane--mobile-hidden': mobilePane !== 'chat' }">
        <div class="paper-reader__assistant-head">
          <div class="paper-reader__assistant-mark"><RobotOutlined /></div>
          <div class="paper-reader__assistant-meta">
            <strong>论文阅读助手</strong>
            <span>基于当前论文内容回答，并提供页码依据</span>
          </div>
          <span class="paper-reader__assistant-status">全文上下文</span>
        </div>
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
              <div v-else-if="m.role === 'assistant'" class="paper-reader__bubble paper-reader__bubble--assistant">

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
        <div class="paper-reader__composer">
          <div class="paper-reader__input">
          <a-textarea
            :key="inputKey"
            v-model:value="draft"
            :rows="1"
            :auto-size="{ minRows: 1, maxRows: 6 }"
            placeholder="基于当前文献提问…"
            :disabled="sending"
            @compositionstart="onCompositionStart"
            @compositionend="onCompositionEnd"
            @press-enter.exact.prevent="send"
          />
          <a-button type="primary" :loading="sending" :disabled="!draft.trim()" aria-label="发送消息" @click="send">发送</a-button>
          </div>
          <div class="paper-reader__composer-hint">Enter 发送 · 回答将优先引用论文正文</div>
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
  getLibraryPdfStreamUrl,
  ensurePaperPdf,
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
import { formatAuthors, paperExternalUrl, paperVenue, toAPA, toBibTeX, toPlain } from '@/utils/citation'
import { isAbortError } from '@/utils/error'
import { useImeGuard } from '@/composables/useImeGuard'
import { useSplitPane } from '@/composables/useSplitPane'
const route = useRoute()
const router = useRouter()
const paper = ref<Paper | null>(null)
const loadingPaper = ref(true)
const loadError = ref('')
const pdfParsing = ref(false)
const pdfStreamUrl = ref('')
const acquiringPdf = ref(false)
const pdfAcquireError = ref('')
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
const { composing, onCompositionStart, onCompositionEnd } = useImeGuard()
const inputKey = ref(0)
let paperLoadSeq = 0
const mobilePane = ref<'pdf' | 'chat'>('pdf')
const scrollRef = ref<HTMLElement | null>(null)
const splitRef = ref<HTMLElement | null>(null)
const dividerRef = ref<HTMLElement | null>(null)
const {
  compactLayout,
  coarsePointer,
  hoverCapable,
  leftPaneStyle,
  dividerPercent,
  onDividerPointerDown,
  onDividerKeydown,
  initDefaultSplitIfNeeded,
  setup: setupSplitPane,
  cleanup: cleanupSplitPane,
} = useSplitPane(splitRef, dividerRef)
const readingSession = ref<{ paperId: number; startedAtMs: number } | null>(null)
let chatRequestSeq = 0
let activeChatAbortController: AbortController | null = null
let activeChatPaperId: number | null = null
let activeAssistantPlaceholderIndex: number | null = null
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
const pdfSrc = computed(() => pdfStreamUrl.value)
const onPdfError = (msg: string) => {
  pdfAcquireError.value = msg || 'PDF 加载失败'
  pdfReady.value = true
  void maybeStartOpening()
}
const pdfReady = ref(false)
const openingStarted = ref(false)
const onPdfLoaded = () => {
  pdfReady.value = true
  void maybeStartOpening()
}
const backToLibrary = () => {
  router.push('/library')
}
const resetPdfStreamUrl = () => {
  pdfStreamUrl.value = ''
}
const isPaperContextCurrent = (targetPaperId: number, loadSeq?: number) => (
  paperId.value === targetPaperId && (loadSeq == null || loadSeq === paperLoadSeq)
)
const loadLocalPdf = async (targetPaperId?: number, loadSeq?: number) => {
  const resolvedPaperId = targetPaperId ?? paperId.value
  if (resolvedPaperId == null || !hasLocalPdfForViewer.value) return
  resetPdfStreamUrl()
  const url = await getLibraryPdfStreamUrl(resolvedPaperId)
  // A rapid paper switch during the await would leave this stale URL
  // overwriting the new paper's pdfStreamUrl — drop it if we've moved on.
  if (loadSeq != null && loadSeq !== paperLoadSeq) return
  pdfStreamUrl.value = url
}
const acquirePdf = async (showSuccess = true, loadSeq?: number) => {
  const targetPaperId = paperId.value
  if (targetPaperId == null || acquiringPdf.value) return
  acquiringPdf.value = true
  pdfAcquireError.value = ''
  try {
    const ensuredPaper = await ensurePaperPdf(targetPaperId)
    if (!isPaperContextCurrent(targetPaperId, loadSeq)) return
    paper.value = ensuredPaper
    await loadLocalPdf(targetPaperId, loadSeq)
    if (!isPaperContextCurrent(targetPaperId, loadSeq)) return
    if (showSuccess) message.success('PDF 已获取，正在加载原文')
  } catch (e: unknown) {
    if (!isPaperContextCurrent(targetPaperId, loadSeq)) return
    pdfAcquireError.value = (e as Error).message || '未能获取论文 PDF'
    pdfReady.value = true
    void maybeStartOpening()
  } finally {
    if (isPaperContextCurrent(targetPaperId, loadSeq)) {
      acquiringPdf.value = false
    }
  }
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
const onChatWheel = (e: WheelEvent) => {
  e.stopPropagation()
}
onBeforeUnmount(() => {
  cancelActiveChat('已取消当前提问')
  resetPdfStreamUrl()
  cleanupSplitPane()
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
const cancelActiveChat = (reason = '已取消当前提问') => {
  chatRequestSeq += 1
  if (activeAssistantPlaceholderIndex != null) {
    const msg = messages.value[activeAssistantPlaceholderIndex]
    if (msg?.role === 'assistant' && msg.content === '正在思考…') {
      msg.content = reason
      msg.related_papers = undefined
      msg.citations = undefined
    }
  }
  if (activeChatAbortController) {
    activeChatAbortController.abort()
    activeChatAbortController = null
  }
  activeChatPaperId = null
  activeAssistantPlaceholderIndex = null
  sending.value = false
}
const gotoCitationPage = (page: number) => {
  if (!page || page < 1) return
  void pdfViewerRef.value?.gotoPage(page)
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
const ensureOpeningAndHistory = async (reloadHistory = false, showError = true, loadSeq?: number) => {
  if (paperId.value == null) return
  try {
    const res = await postPaperReaderOpening(paperId.value)
    if (loadSeq != null && loadSeq !== paperLoadSeq) return
    if (!res.success || !res.opening) return
    if (res.pdf_parsing) pdfParsing.value = true
    if (reloadHistory) {
      const h = await getPaperReaderHistory(paperId.value, 200)
      if (loadSeq != null && loadSeq !== paperLoadSeq) return
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
  const loadSeq = paperLoadSeq
  void ensureOpeningAndHistory(reloadHistory, showError, loadSeq)
}
const send = async () => {
  const text = draft.value.trim()
  if (!text || paperId.value == null || sending.value) return
  if (composing.value) return
  const currentPaperId = paperId.value
  const requestSeq = chatRequestSeq + 1
  chatRequestSeq = requestSeq
  const abortController = new AbortController()
  activeChatAbortController = abortController
  activeChatPaperId = currentPaperId
  sending.value = true
  messages.value.push({ role: 'user', content: text })
  const assistantIdx = messages.value.push({ role: 'assistant', content: '正在思考…' }) - 1
  activeAssistantPlaceholderIndex = assistantIdx
  draft.value = ''
  inputKey.value += 1
  await nextTick()
  await scrollBottom()
  try {
    const res = await postPaperReaderChat({
      paper_id: currentPaperId,
      messages: messages.value.slice(0, -2),
      user_message: text,
    }, { signal: abortController.signal })
    if (chatRequestSeq !== requestSeq || activeChatPaperId !== currentPaperId || paperId.value !== currentPaperId) return
    const target = messages.value[assistantIdx]
    if (!target || target.role !== 'assistant') return
    if (res.success && res.reply) {
      const rp = Array.isArray((res as any).related_papers) ? ((res as any).related_papers as Paper[]) : []
      const cites = Array.isArray((res as any).citations) ? ((res as any).citations as PaperReaderCitation[]) : []
      target.content = normalizeAssistantText(res.reply)
      target.related_papers = rp.length ? rp : undefined
      target.citations = cites.length ? cites : undefined
    } else {
      target.content = '（无回复）'
      target.related_papers = undefined
      target.citations = undefined
    }
  } catch (e: unknown) {
    const cancelled = isAbortError(e)
    if (cancelled || chatRequestSeq !== requestSeq || activeChatPaperId !== currentPaperId || paperId.value !== currentPaperId) {
      return
    }
    const target = messages.value[assistantIdx]
    if (target?.role === 'assistant') {
      target.content = '请求失败，请检查网络或 LLM 配置。'
      target.related_papers = undefined
      target.citations = undefined
    }
    message.error((e as Error).message || '发送失败')
  } finally {
    if (chatRequestSeq === requestSeq && activeChatAbortController === abortController) {
      activeChatAbortController = null
      activeChatPaperId = null
      activeAssistantPlaceholderIndex = null
      sending.value = false
    }
    await scrollBottom()
  }
}
const relatedPaperMetaLine = (p: Paper): string => {
  const parts: string[] = []
  const authorStr = formatAuthors(p, { max: 4, suffix: '…', empty: '' })
  if (authorStr) parts.push(authorStr)
  if (p.year != null) parts.push(String(p.year))
  const j = paperVenue(p)
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
const onCopyCitation = async ({ key }: { key: string }) => {
  if (!paper.value) return
  const text = key === 'bibtex' ? toBibTeX(paper.value)
    : key === 'apa' ? toAPA(paper.value)
    : toPlain(paper.value)
  try {
    await navigator.clipboard.writeText(text)
    message.success(`已复制${key === 'bibtex' ? ' BibTeX' : key === 'apa' ? ' APA' : ''}引用`)
  } catch {
    message.error('复制失败，请手动选择文本复制')
  }
}
const loadPaper = async () => {
  const loadSeq = ++paperLoadSeq
  cancelActiveChat('已取消上一篇论文的提问')
  await flushReadingSession()
  loadingPaper.value = true
  loadError.value = ''
  paper.value = null
  resetPdfStreamUrl()
  pdfAcquireError.value = ''
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
    // Fetch paper metadata and chat history in parallel — history only needs
    // the route paperId, not paper.value. The PDF branch still waits on
    // getPaper because it depends on paper.value.local_pdf_path / source_url.
    const [paperData, histData] = await Promise.all([
      getPaper(paperId.value),
      getPaperReaderHistory(paperId.value, 200).catch(() => null),
    ])
    if (loadSeq !== paperLoadSeq) return
    paper.value = paperData
    if (hasLocalPdfForViewer.value) {
      try {
        await loadLocalPdf(paperId.value, loadSeq)
        if (loadSeq !== paperLoadSeq) return
      } catch (e: unknown) {
        if (loadSeq !== paperLoadSeq) return
        pdfAcquireError.value = (e as Error).message || 'PDF 加载失败'
      }
    } else if (paperExternalUrl(paper.value)) {
      // Keep the left pane in an explicit loading state until PDF acquisition
      // succeeds or fails; otherwise the assistant appears while the paper pane
      // briefly claims that no PDF exists.
      await acquirePdf(false, loadSeq)
      if (loadSeq !== paperLoadSeq) return
    }
    try {
      const h = histData
      if (loadSeq !== paperLoadSeq) return
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
    if (loadSeq !== paperLoadSeq) return
    loadError.value = (e as Error).message || '加载失败'
  } finally {
    if (loadSeq === paperLoadSeq) {
      loadingPaper.value = false
    }
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
  setupSplitPane()
})
</script>
<style scoped>
.paper-reader {
  display: flex;
  flex-direction: column;
  gap: 0;
  flex: 1 1 0;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  color-scheme: light;
}
.paper-reader__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
  flex-wrap: nowrap;
  min-height: 60px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--pg-divider);
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
}
.paper-reader__title--placeholder {
  color: var(--pg-text-tertiary);
  font-weight: 500;
}
.paper-reader__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.paper-reader__mobile-switch {
  display: none;
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
  height: auto;
  overflow: hidden;
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
.paper-reader--hover-capable .paper-reader__divider:hover::before,
.paper-reader__divider:focus-visible::before {
  background: var(--pg-primary);
  opacity: 0.55;
}
.paper-reader__divider:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--pg-primary) 45%, transparent);
  outline-offset: -2px;
}
.paper-reader--coarse-pointer:not(.paper-reader--compact) .paper-reader__divider {
  flex-basis: 14px;
}
.paper-reader--coarse-pointer:not(.paper-reader--compact) .paper-reader__divider::before {
  left: 5px;
  width: 4px;
  border-radius: 999px;
}
.pdf-js-viewer-wrapper {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.paper-reader__pane--chat {
  min-height: 0;
  max-height: none;
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--pg-bg);
  overflow: hidden;
}
.paper-reader__assistant-head {
  flex: 0 0 auto;
  min-height: 68px;
  padding: 13px 18px;
  display: flex;
  align-items: center;
  gap: 11px;
  background: rgba(255, 255, 255, 0.95);
  border-bottom: 1px solid var(--pg-divider);
  backdrop-filter: var(--pg-glass-blur-light);
}
.paper-reader__assistant-mark {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  color: var(--pg-accent);
  font-size: 18px;
  background: var(--pg-primary-soft);
  border: 1px solid #dfe3ff;
  box-shadow: var(--pg-shadow-sm);
}
.paper-reader__assistant-meta {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.paper-reader__assistant-meta strong {
  color: var(--pg-text-heading);
  font-size: 14px;
  font-weight: 650;
}
.paper-reader__assistant-meta span {
  color: var(--pg-text-tertiary);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.paper-reader__assistant-status {
  flex: 0 0 auto;
  padding: 4px 8px;
  border: 1px solid #dfe3ff;
  border-radius: var(--pg-radius-pill);
  color: var(--pg-accent);
  background: var(--pg-primary-softer);
  font-size: 10px;
  font-weight: 650;
}
.paper-reader__messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 26px 22px 34px;
  background: transparent;
  margin: 0;
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
  margin-bottom: 26px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.paper-reader__msg--assistant {
  max-width: 100%;
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
  background: var(--pg-primary-soft);
  border: 1px solid #dfe3ff;
  color: var(--pg-primary);
  box-shadow: none;
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
  background: #6668e8;
  color: var(--pg-text-inverse);
  border-radius: 14px 14px 4px 14px;
  box-shadow: 0 4px 14px rgba(30, 27, 75, 0.18);
}
.paper-reader__bubble--assistant {
  max-width: calc(100% - 42px);
  padding: 2px 4px 2px 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  color: var(--pg-text);
}
.paper-reader__msg-body {
  line-height: 1.72;
  font-size: 14px;
  color: var(--pg-text);
}
.paper-reader__bubble--assistant .paper-reader__msg-body :deep(p:first-child) {
  margin-top: 0;
}
.paper-reader__bubble--assistant .paper-reader__msg-body :deep(p:last-child) {
  margin-bottom: 0;
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
.paper-reader__composer {
  flex: 0 0 auto;
  padding: 12px 16px 10px;
  background: rgba(255, 255, 255, 0.96);
  border-top: 1px solid var(--pg-divider);
}
.paper-reader__input {
  display: flex;
  gap: 8px;
  padding: 7px;
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: 16px;
  align-items: flex-end;
  box-shadow: 0 8px 26px rgba(12,10,29,.07);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.paper-reader__input:focus-within {
  border-color: #c7ccf8;
  box-shadow: 0 0 0 3px rgba(67, 56, 202, 0.08);
}
.paper-reader__composer-hint {
  padding: 5px 4px 0;
  color: var(--pg-text-tertiary);
  font-size: 10px;
  text-align: right;
}
.paper-reader__input :deep(.ant-input) {
  flex: 1;
  border: 0;
  border-radius: 8px;
  box-shadow: none;
  transition: none;
}
.paper-reader__input :deep(.ant-input:focus),
.paper-reader__input :deep(textarea.ant-input:focus) {
  border-color: transparent;
  box-shadow: none;
}
.paper-reader__input :deep(textarea.ant-input) {
  min-height: 36px;
  height: 36px;
  line-height: 22px;
  resize: none;
  padding: 6px 12px;
}
@media (max-width: 760px) {
  .paper-reader__toolbar {
    padding: 8px 10px;
  }
  .paper-reader__title {
    font-size: 14px;
  }
  .paper-reader__pane {
    min-width: 0;
  }
  .paper-reader__pane--pdf {
    flex: 1 1 auto !important;
    border-right: 1px solid var(--pg-divider);
  }
  .paper-reader__pane--chat {
    flex: 0 0 clamp(300px, 42vw, 334px);
    min-width: 280px;
  }
}
.paper-reader--compact .paper-reader__toolbar {
  gap: 6px;
  padding-inline: 8px;
}
.paper-reader--compact .paper-reader__toolbar > :first-child {
  flex-shrink: 0;
}
.paper-reader--compact .paper-reader__title,
.paper-reader--compact .paper-reader__toolbar-actions {
  display: none;
}
.paper-reader--compact .paper-reader__mobile-switch {
  display: inline-flex;
  margin-left: auto;
  padding: 3px;
  border-radius: 10px;
  background: var(--pg-bg-soft);
  border: 1px solid var(--pg-divider);
}
.paper-reader--compact .paper-reader__mobile-switch button {
  min-width: 52px;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 7px;
  color: var(--pg-text-secondary);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}
.paper-reader--compact .paper-reader__mobile-switch button.is-active {
  color: var(--pg-primary);
  background: var(--pg-surface);
  box-shadow: var(--pg-shadow-xs);
  font-weight: 650;
}
.paper-reader--compact .paper-reader__divider {
  display: none;
}
.paper-reader--compact .paper-reader__pane--pdf,
.paper-reader--compact .paper-reader__pane--chat {
  flex: 1 1 100% !important;
  width: 100%;
  min-width: 0;
}
.paper-reader--compact .paper-reader__pane--mobile-hidden {
  display: none;
}
@media (max-width: 560px) {
  .paper-reader__toolbar {
    gap: 6px;
    padding-inline: 8px;
  }
  .paper-reader__toolbar > :first-child {
    flex-shrink: 0;
  }
  .paper-reader__title {
    display: none;
  }
  .paper-reader__toolbar-actions {
    display: none;
  }
  .paper-reader__assistant-status {
    display: none;
  }
  .paper-reader__assistant-head {
    padding-inline: 12px;
  }
  .paper-reader__messages {
    padding: 18px 12px 22px;
  }
  .paper-reader__bubble {
    max-width: 90%;
  }
  .paper-reader__bubble--assistant {
    max-width: calc(100% - 40px);
  }
}
</style>
