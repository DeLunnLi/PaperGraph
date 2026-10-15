import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import KnowledgeBase from './KnowledgeBase.vue'

const mocks = vi.hoisted(() => ({
  getKnowledgeMemories: vi.fn(), getKnowledgeStats: vi.fn(), getKnowledgeEvolution: vi.fn(),
  getTraceSessions: vi.fn(), getTraceEvents: vi.fn(),
}))
vi.mock('@/services/api/knowledge', () => mocks)
vi.mock('@/services/api/traces', () => mocks)
vi.mock('ant-design-vue/es/button', () => ({ default: { template: '<button><slot /></button>' } }))
vi.mock('ant-design-vue/es/drawer', () => ({ default: { template: '<div data-test="drawer"><slot /></div>' } }))
vi.mock('ant-design-vue/es/space', () => ({ default: { template: '<div data-test="space"><slot /></div>' } }))
vi.mock('ant-design-vue/es/select', () => ({ default: { template: '<div><slot /></div>' }, SelectOption: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/spin', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/tag', () => ({ default: { template: '<span><slot /></span>' } }))
vi.mock('@ant-design/icons-vue', () => ({
  DatabaseOutlined: { template: '<i />' }, ThunderboltOutlined: { template: '<i />' },
  RiseOutlined: { template: '<i />' }, SyncOutlined: { template: '<i />' },
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
async function settle() { for (let i = 0; i < 8; i++) await nextTick() }
let app: App | undefined
let host: HTMLDivElement
function mountKnowledgeBase() {
  host = document.createElement('div')
  document.body.appendChild(host)
  let instance: any
  app = createApp(defineComponent({ setup: () => () => h(KnowledgeBase, { ref: (value: unknown) => { instance = value } }) }))
  app.component('router-link', { template: '<a><slot /></a>' })
  app.mount(host)
  return () => instance.$.setupState
}
beforeEach(() => {
  vi.resetAllMocks()
  mocks.getKnowledgeMemories.mockResolvedValue({ items: [] })
  mocks.getKnowledgeEvolution.mockResolvedValue({ items: [] })
  mocks.getKnowledgeStats.mockResolvedValue({ total: 0, avg_utility: 0, total_accesses: 0, evolution_events_24h: 0 })
  mocks.getTraceSessions.mockResolvedValue({ sessions: [] })
  mocks.getTraceEvents.mockResolvedValue({ events: [] })
})
afterEach(() => { app?.unmount(); app = undefined; host?.remove() })

describe('knowledge page', () => {
  it('uses all-event daily aggregates and supports a server without the new field', async () => {
    const state = mountKnowledgeBase()
    await settle()
    expect(state().dailyActivity).toEqual([])
    state().stats = {
      ...state().stats,
      activity_7d: [{ date: '2026-10-13', count: 1200 }, { date: '2026-10-14', count: 50 }],
    }
    expect(state().dailyActivity).toEqual([
      { label: '10/13', count: 1200, pct: 100 }, { label: '10/14', count: 50, pct: 8 },
    ])
    state().evolution = [{ id: 1, created_at: Date.now() / 1000 }]
    expect(state().dailyActivity[0].count).toBe(1200)
    expect(host.querySelector('[data-test="drawer"]')).not.toBeNull()
    expect(host.querySelector('[data-test="space"]')).not.toBeNull()
  })

  it('keeps the newest memory filter result and loading state when old requests finish', async () => {
    const state = mountKnowledgeBase()
    await settle()
    const old = deferred<any>()
    const latest = deferred<any>()
    mocks.getKnowledgeMemories.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
    state().kind = 'long'
    const first = state().loadMemories()
    state().kind = 'reflection'
    const second = state().loadMemories()
    old.reject(new Error('Old request failed'))
    await first
    expect(state().loadingMemories).toBe(true)
    const memory = { memory_id: 'newest', importance: 0.5, utility: 0, kind: 'reflection', content: 'Newest memory', scope: 'global' }
    latest.resolve({ items: [memory] })
    await second
    expect(state().memories).toEqual([memory])
    expect(state().loadingMemories).toBe(false)
  })

  it('drops stale trace details when switching sessions or returning to the list', async () => {
    const state = mountKnowledgeBase()
    await settle()
    await state().openTraces()
    const old = deferred<any>()
    const latest = deferred<any>()
    mocks.getTraceEvents.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
    const first = state().openTrace('first')
    const second = state().openTrace('second')
    latest.resolve({ events: [{ event: 'second-event', payload: {} }] })
    await second
    old.resolve({ events: [{ event: 'first-event', payload: {} }] })
    await first
    expect(state().activeTraceEvents[0].event).toBe('second-event')
    const pending = deferred<any>()
    mocks.getTraceEvents.mockReturnValueOnce(pending.promise)
    const last = state().openTrace('third')
    state().closeTrace()
    pending.resolve({ events: [{ event: 'third-event', payload: {} }] })
    await last
    expect(state().activeTrace).toBeNull()
    expect(state().activeTraceEvents).toEqual([])
    expect(state().loadingTraces).toBe(false)
  })
})
