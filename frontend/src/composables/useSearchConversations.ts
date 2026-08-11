import { onScopeDispose, ref, shallowRef, toRaw, type Ref } from 'vue'
interface SearchConversation<TMessage> {
  id: string
  title: string
  messages: TMessage[]
  timestamp: number
  messageCount: number
}
interface UseSearchConversationsOptions<TMessage> {
  messages: Ref<TMessage[]>
  hasSearched: Ref<boolean>
  userInput: Ref<string>
  titleFromMessages: (messages: TMessage[]) => string
  stateStorageKey?: string
  conversationsStorageKey?: string
}
export function useSearchConversations<TMessage>({
  messages,
  hasSearched,
  userInput,
  titleFromMessages,
  stateStorageKey = 'searchAgentState',
  conversationsStorageKey = 'searchAgentConversations',
}: UseSearchConversationsOptions<TMessage>) {
  // shallowRef: conversation entries are replaced wholesale (never mutated in
  // place), so deep reactivity would only cost proxy overhead. It also keeps the
  // generic TMessage intact, which ref() would erode via UnwrapRefSimple.
  const conversations = shallowRef<SearchConversation<TMessage>[]>([])
  const currentConversationId = ref<string | null>(null)
  const PERSIST_DEBOUNCE_MS = 900
  const CONVERSATION_TTL_MS = 30 * 24 * 60 * 60 * 1000
  const STATE_TTL_MS = 24 * 60 * 60 * 1000
  const MAX_CONVERSATIONS = 50
  let conversationsPersistTimer: ReturnType<typeof setTimeout> | null = null
  let statePersistTimer: ReturnType<typeof setTimeout> | null = null

  function cloneMessages(value: TMessage[]): TMessage[] {
    // structuredClone is native and faster than a JSON round-trip, but it throws
    // DataCloneError on Proxy objects — and `messages` is a deep reactive ref, so
    // every element is a Proxy. toRaw() unwraps the outer array; the per-item
    // toRaw handles nested reactive objects. JSON stays as the last-resort
    // fallback (e.g. values holding functions/DOM nodes that can't be cloned).
    const raw = toRaw(value).map((item) => toRaw(item))
    if (typeof structuredClone === 'function') {
      try {
        return structuredClone(raw)
      } catch {
        // fall through to JSON
      }
    }
    try {
      return JSON.parse(JSON.stringify(raw)) as TMessage[]
    } catch (e) {
      console.error('Failed to clone messages:', e)
      return []
    }
  }
  function generateId(): string {
    return Date.now().toString(36) + Math.random().toString(36).slice(2)
  }
  function flushConversationsPersist() {
    if (conversationsPersistTimer) {
      clearTimeout(conversationsPersistTimer)
      conversationsPersistTimer = null
    }
    try {
      const recent = conversations.value
        .filter((item) => Date.now() - Number(item.timestamp || 0) < CONVERSATION_TTL_MS)
        .slice(0, MAX_CONVERSATIONS)
      conversations.value = recent
      localStorage.setItem(conversationsStorageKey, JSON.stringify(recent))
    } catch (e) {
      console.error('Failed to save conversations:', e)
    }
  }
  function saveConversations(immediate = false) {
    if (immediate) {
      flushConversationsPersist()
      return
    }
    if (conversationsPersistTimer) clearTimeout(conversationsPersistTimer)
    conversationsPersistTimer = setTimeout(flushConversationsPersist, PERSIST_DEBOUNCE_MS)
  }
  function loadConversations() {
    try {
      const saved = localStorage.getItem(conversationsStorageKey)
      if (saved) {
        const parsed = JSON.parse(saved) as SearchConversation<TMessage>[]
        conversations.value = Array.isArray(parsed)
          ? parsed
              .filter((item) => item && Date.now() - Number(item.timestamp || 0) < CONVERSATION_TTL_MS)
              .slice(0, MAX_CONVERSATIONS)
          : []
      }
    } catch (e) {
      console.error('Failed to load conversations:', e)
      conversations.value = []
    }
  }
  function flushStatePersist() {
    if (statePersistTimer) {
      clearTimeout(statePersistTimer)
      statePersistTimer = null
    }
    try {
      localStorage.setItem(
        stateStorageKey,
        JSON.stringify({
          messages: messages.value,
          hasSearched: hasSearched.value,
          currentConversationId: currentConversationId.value,
          timestamp: Date.now(),
        })
      )
    } catch (e) {
      // Most likely a QuotaExceededError (or Safari private mode, where
      // setItem always throws). Silently dropping it meant search state just
      // stopped being restored with no clue why; the sibling persist/load
      // helpers all log, so match them.
      console.error('Failed to save search state:', e)
    }
  }
  function saveState(immediate = false) {
    if (immediate) {
      flushStatePersist()
      return
    }
    if (statePersistTimer) clearTimeout(statePersistTimer)
    statePersistTimer = setTimeout(flushStatePersist, PERSIST_DEBOUNCE_MS)
  }
  function loadState() {
    try {
      const saved = localStorage.getItem(stateStorageKey)
      if (!saved) return
      const state = JSON.parse(saved) as {
        messages?: TMessage[]
        hasSearched?: boolean
        currentConversationId?: string | null
        timestamp?: number
      }
      if (!state.timestamp || Date.now() - state.timestamp >= STATE_TTL_MS) {
        clearState()
        return
      }

      const restoredMessages = Array.isArray(state.messages) ? cloneMessages(state.messages) : []
      messages.value = restoredMessages
      hasSearched.value = Boolean(state.hasSearched) && restoredMessages.length > 0
      currentConversationId.value = hasSearched.value && typeof state.currentConversationId === 'string' && state.currentConversationId
        ? state.currentConversationId
        : null
      userInput.value = ''
    } catch (e) {
      console.error('Failed to load search state:', e)
      clearState()
    }
  }
  function clearState() {
    if (statePersistTimer) {
      clearTimeout(statePersistTimer)
      statePersistTimer = null
    }
    localStorage.removeItem(stateStorageKey)
  }
  function saveCurrentConversation() {
    if (!hasSearched.value || messages.value.length === 0) return
    const convId = currentConversationId.value || generateId()
    if (!currentConversationId.value) currentConversationId.value = convId
    const next: SearchConversation<TMessage> = {
      id: convId,
      title: titleFromMessages(messages.value),
      messages: cloneMessages(messages.value),
      timestamp: Date.now(),
      messageCount: messages.value.length,
    }
    const existingIndex = conversations.value.findIndex((c) => c.id === convId)
    const merged =
      existingIndex >= 0
        ? conversations.value.map((item, i) => (i === existingIndex ? next : item))
        : [next, ...conversations.value]
    conversations.value = merged.slice(0, MAX_CONVERSATIONS)
    saveConversations()
  }
  function ensureCurrentConversationId(): string {
    if (!currentConversationId.value) currentConversationId.value = generateId()
    return currentConversationId.value
  }
  function loadConversation(id: string) {
    const conversation = conversations.value.find((c) => c.id === id)
    if (!conversation) return
    saveCurrentConversation()
    messages.value = cloneMessages(conversation.messages)
    hasSearched.value = true
    currentConversationId.value = id
    saveState()
  }
  function createNewConversation(saveCurrent = true) {
    if (saveCurrent && hasSearched.value && messages.value.length > 0) {
      if (!currentConversationId.value) currentConversationId.value = generateId()
      saveCurrentConversation()
    }
    messages.value = []
    hasSearched.value = false
    userInput.value = ''
    currentConversationId.value = null
    clearState()
  }
  function removeConversation(id: string) {
    if (!conversations.value.some((c) => c.id === id)) return
    conversations.value = conversations.value.filter((c) => c.id !== id)
    saveConversations()
    if (currentConversationId.value === id) createNewConversation(false)
  }
  function persistConversationAndState(immediate = false) {
    if (!hasSearched.value) return
    saveCurrentConversation()
    saveState(immediate)
    if (immediate) saveConversations(true)
  }
  function initFromStorage() {
    loadConversations()
    loadState()
  }
  function formatTime(timestamp: number): string {
    const date = new Date(timestamp)
    const now = new Date()
    if (date.toDateString() === now.toDateString()) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  }

  // Safety net: clear debounce timers if the owning component forgets to flush.
  onScopeDispose(() => {
    if (conversationsPersistTimer) clearTimeout(conversationsPersistTimer)
    if (statePersistTimer) clearTimeout(statePersistTimer)
  })

  return {
    conversations,
    currentConversationId,
    saveCurrentConversation,
    ensureCurrentConversationId,
    saveState,
    persistConversationAndState,
    loadConversation,
    createNewConversation,
    removeConversation,
    initFromStorage,
    formatTime,
  }
}
