import { describe, it, expect, beforeEach } from 'vitest'
import { ref, nextTick, watch, effectScope } from 'vue'
import { useSearchConversations } from '@/composables/useSearchConversations'

type Msg = { role: string; text: string }

function setup() {
  const messages = ref<Msg[]>([])
  const hasSearched = ref(false)
  const userInput = ref('')
  const scope = effectScope()
  const api = scope.run(() =>
    useSearchConversations<Msg>({
      messages, hasSearched, userInput,
      titleFromMessages: (m) => m[0]?.text ?? 'untitled',
    }),
  )!
  return { messages, hasSearched, api }
}

describe('useSearchConversations', () => {
  beforeEach(() => localStorage.clear())

  it('save/remove trigger reactive updates and keep ordering', async () => {
    const { messages, hasSearched, api } = setup()
    const seen: number[] = []
    watch(api.conversations, (v) => seen.push(v.length), { flush: 'sync' })

    messages.value = [{ role: 'user', text: 'first' }]
    hasSearched.value = true
    api.saveCurrentConversation()
    expect(api.conversations.value.length).toBe(1)

    // new conversation, then save a second one
    api.createNewConversation(false)
    messages.value = [{ role: 'user', text: 'second' }]
    hasSearched.value = true
    api.saveCurrentConversation()
    expect(api.conversations.value.length).toBe(2)
    // newest first
    expect(api.conversations.value[0].title).toBe('second')

    await nextTick()
    expect(seen).toContain(1)
    expect(seen).toContain(2)

    const idToRemove = api.conversations.value[0].id
    api.removeConversation(idToRemove)
    expect(api.conversations.value.length).toBe(1)
    expect(api.conversations.value[0].title).toBe('first')
    expect(seen).toContain(1)
  })

  it('updates an existing conversation in place without duplicating', () => {
    const { messages, hasSearched, api } = setup()
    messages.value = [{ role: 'user', text: 'hello' }]
    hasSearched.value = true
    api.saveCurrentConversation()
    messages.value = [{ role: 'user', text: 'hello' }, { role: 'ai', text: 'hi' }]
    api.saveCurrentConversation()
    expect(api.conversations.value.length).toBe(1)
    expect(api.conversations.value[0].messageCount).toBe(2)
  })

  // Regression: `messages` is a deep reactive ref, so its items are Proxies and
  // structuredClone() throws DataCloneError on them. That made saving silently
  // crash. cloneMessages() must toRaw() before cloning.
  it('saves messages from a deep reactive ref without throwing', () => {
    const { messages, hasSearched, api } = setup()
    messages.value = [{ role: 'user', text: 'proxy me' }]
    hasSearched.value = true
    expect(() => api.saveCurrentConversation()).not.toThrow()
    expect(api.conversations.value[0].messages).toEqual([{ role: 'user', text: 'proxy me' }])
  })

  it('stores a detached deep copy, not a live reference', () => {
    const { messages, hasSearched, api } = setup()
    messages.value = [{ role: 'user', text: 'original' }]
    hasSearched.value = true
    api.saveCurrentConversation()
    messages.value[0].text = 'mutated afterwards'
    expect(api.conversations.value[0].messages[0].text).toBe('original')
  })

  it('removing an unknown id is a no-op', () => {
    const { messages, hasSearched, api } = setup()
    messages.value = [{ role: 'user', text: 'x' }]
    hasSearched.value = true
    api.saveCurrentConversation()
    api.removeConversation('does-not-exist')
    expect(api.conversations.value.length).toBe(1)
  })
})
