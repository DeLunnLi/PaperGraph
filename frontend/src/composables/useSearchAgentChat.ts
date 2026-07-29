import { nextTick, type Ref } from 'vue'
import { SearchAgentCancelledError, searchAgentChatStream, type SearchAgentStreamEvent } from '@/services/api'
type ToolCall = {
  name: string
  status: 'running' | 'success' | 'error'
  params?: Record<string, any>
  result_summary?: string
}
type SearchStep = {
  name: string
  label: string
  status: 'running' | 'done' | 'error'
  detail?: string
}
type SearchAgentMessage = {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  searchParams?: any
  toolCalls?: ToolCall[]
  results?: any[]
  total?: number
  isError?: boolean
  searchSteps?: SearchStep[]
  deepSubQueries?: string[]
}
interface UseSearchAgentChatOptions {
  messages: Ref<SearchAgentMessage[]>
  userInput: Ref<string>
  isLoading: Ref<boolean>
  hasSearched: Ref<boolean>
  deepSearch?: Ref<boolean>
  ensureCurrentConversationId: () => string
  scrollToBottom: () => void
  onConversationDirty?: () => void
  onRequestStateChange?: (active: boolean) => void
}
export function useSearchAgentChat({
  messages,
  userInput,
  isLoading,
  hasSearched,
  deepSearch,
  ensureCurrentConversationId,
  scrollToBottom,
  onConversationDirty,
  onRequestStateChange,
}: UseSearchAgentChatOptions) {
  let activeRequestSeq = 0
  let activeAbortController: AbortController | null = null
  let activeAssistantIndex: number | null = null

  const finishActiveRequest = (requestSeq: number) => {
    if (activeRequestSeq !== requestSeq) return
    activeAbortController = null
    activeAssistantIndex = null
    isLoading.value = false
    onRequestStateChange?.(false)
  }

  const abortActiveRequest = () => {
    if (activeAssistantIndex != null) {
      const msg = messages.value[activeAssistantIndex]
      if (msg) {
        msg.content = '已取消本次搜索'
        msg.timestamp = Date.now()
        msg.isError = true
        msg.results = undefined
        msg.total = 0
        if (msg.searchSteps?.length) {
          msg.searchSteps.forEach((step: SearchStep) => {
            if (step.status === 'running') step.status = 'error'
          })
        }
      }
    }
    activeRequestSeq += 1
    if (activeAbortController) {
      activeAbortController.abort()
      activeAbortController = null
    }
    activeAssistantIndex = null
    if (isLoading.value) {
      isLoading.value = false
      onRequestStateChange?.(false)
    }
    onConversationDirty?.()
  }

  const sendMessage = async () => {
    const input = userInput.value.trim()
    if (!input || isLoading.value) return
    const requestSeq = activeRequestSeq + 1
    activeRequestSeq = requestSeq
    const abortController = new AbortController()
    activeAbortController = abortController
    ensureCurrentConversationId()
    userInput.value = ''
    await nextTick()
    messages.value.push({
      role: 'user',
      content: input,
      timestamp: Date.now(),
    })
    onConversationDirty?.()
    hasSearched.value = true
    await nextTick()
    userInput.value = ''
    isLoading.value = true
    onRequestStateChange?.(true)
    const pIdx = messages.value.push({
      role: 'assistant', content: '正在搜索文献…', timestamp: Date.now(),
      toolCalls: [], results: [], total: 0, isError: false,
      searchSteps: [], deepSubQueries: [],
    }) - 1
    activeAssistantIndex = pIdx
    scrollToBottom()
    try {
      const req = {
        message: input,
        deep_search: deepSearch?.value ?? false,
        history: messages.value.slice(0, -2).map((m) => ({ role: m.role, content: m.content })),
      }
      const ensureSteps = (msg: any) => {
        if (!msg.searchSteps) msg.searchSteps = []
        return msg.searchSteps
      }
      const addOrUpdateStep = (msg: any, name: string, label: string, status: 'running' | 'done' | 'error', detail?: string) => {
        const steps = ensureSteps(msg)
        const existing = steps.find((s: SearchStep) => s.name === name)
        if (existing) {
          existing.status = status
          if (detail) existing.detail = detail
        } else {
          steps.push({ name, label, status, detail })
        }
      }
      const applyStreamEvent = (ev: SearchAgentStreamEvent) => {
        if (activeRequestSeq !== requestSeq) return
        const msg = messages.value[pIdx]
        if (!msg) return
        const steps = ensureSteps(msg)
        if (ev.type === 'status' && ev.message) {
          msg.content = ev.message
          msg.isError = false
          return
        }
        if (ev.type === 'tool_call' && ev.tool) {
          // Map tool calls to human-readable steps
          const toolLabels: Record<string, string> = {
            understand_intent: '理解搜索意图',
            search_pipeline: '多源检索',
          }
          const label = toolLabels[ev.tool] || ev.tool
          const status = ev.result ? 'done' : 'running'
          const detail = ev.result ? (typeof ev.result === 'string' ? ev.result : JSON.stringify(ev.result).slice(0, 120)) : undefined
          addOrUpdateStep(msg, ev.tool, label, status as any, detail)
          msg.content = `${label}…`
          return
        }
        if (ev.type === 'deep:decompose') {
          if (ev.sub_queries?.length) {
            msg.deepSubQueries = ev.sub_queries
            addOrUpdateStep(msg, 'decompose', '子问题分解', 'done', `${ev.sub_queries.length} 个子问题`)
            msg.content = `已分解为 ${ev.sub_queries.length} 个子问题`
          } else {
            addOrUpdateStep(msg, 'decompose', '子问题分解', 'running')
            msg.content = '正在分解子问题…'
          }
          return
        }
        if (ev.type === 'deep:round') {
          const r = (ev.round ?? 0) + 1
          const total = ev.total_rounds ?? 1
          const n = ev.n_subqueries ?? 0
          addOrUpdateStep(msg, `round_${r}`, `第 ${r}/${total} 轮检索`, 'running', `${n} 个子问题并行`)
          msg.content = `第 ${r}/${total} 轮检索中（${n} 个子问题并行）…`
          return
        }
        if (ev.type === 'deep:expand') {
          if (ev.new_subqueries?.length) {
            msg.deepSubQueries = [...(msg.deepSubQueries || []), ...ev.new_subqueries]
          }
          return
        }
        if (ev.type === 'deep:rrf') {
          addOrUpdateStep(msg, 'rrf', 'RRF 融合', 'done', `${ev.fused_count ?? 0} 篇候选`)
          msg.content = `RRF 融合完成（${ev.fused_count ?? 0} 篇候选）`
          return
        }
        if (ev.type === 'deep:rank') {
          addOrUpdateStep(msg, 'rank', 'LLM 精排', 'running')
          msg.content = 'LLM 精排中…'
          return
        }
        if (ev.type === 'deep:synthesis') {
          // Mark rank as done, start synthesis
          addOrUpdateStep(msg, 'rank', 'LLM 精排', 'done')
          addOrUpdateStep(msg, 'synthesis', '综述生成', 'running')
          msg.content = '生成综述段落…'
          return
        }
        if (ev.type === 'error' && ev.message) {
          msg.content = `抱歉，搜索出现了问题：${ev.message}`
          msg.isError = true
          msg.results = undefined
          msg.total = 0
        }
      }
      const data = await searchAgentChatStream(req, applyStreamEvent, { signal: abortController.signal })
      if (activeRequestSeq !== requestSeq) return
      const msg = messages.value[pIdx]
      if (!msg) return
      if (data.success) {
        msg.content = data.response
        msg.searchParams = data.search_params
        msg.toolCalls = data.tool_calls
        msg.results = data.papers
        msg.total = data.total || data.papers?.length || 0
        msg.isError = false
        msg.timestamp = Date.now()
        // Mark all remaining steps as done
        if (msg.searchSteps) {
          msg.searchSteps.forEach((s: SearchStep) => { if (s.status === 'running') s.status = 'done' })
        }
      } else {
        msg.content = `抱歉，搜索出现了问题：${data.message || '未知错误'}`
        msg.timestamp = Date.now()
        msg.isError = true
        msg.results = undefined
        msg.total = 0
      }
    } catch (error: any) {
      if (error instanceof SearchAgentCancelledError || activeRequestSeq !== requestSeq) {
        return
      }
      const errText = String(error?.message || '请稍后重试')
      if (messages.value[pIdx]) {
        const msg = messages.value[pIdx]
        msg.content = `抱歉，出现了错误：${errText}`
        msg.timestamp = Date.now()
        msg.isError = true
        msg.results = undefined
        msg.total = 0
      } else {
        messages.value.push({
          role: 'assistant',
          content: `抱歉，出现了错误：${errText}`,
          timestamp: Date.now(),
          isError: true,
          results: undefined,
          total: 0,
        })
      }
    } finally {
      finishActiveRequest(requestSeq)
      userInput.value = ''
      onConversationDirty?.()
      scrollToBottom()
    }
  }
  return { sendMessage, abortActiveRequest }
}
