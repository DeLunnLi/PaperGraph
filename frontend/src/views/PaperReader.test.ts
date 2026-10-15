import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import PaperReader from './PaperReader.vue'

const mocks = vi.hoisted(() => ({
  getPaper: vi.fn(),
  getPaperReaderHistory: vi.fn(),
  ensurePaperPdf: vi.fn(),
  postPaperReaderChat: vi.fn(),
  postPaperReaderOpening: vi.fn(),
  postReaderFeedback: vi.fn(),
  getLibraryPdfStreamUrl: vi.fn(),
}))
let route = reactive({ params: { id: '1' }, query: {} })
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/services/api', () => ({
  ...mocks,
  postReadingLog: vi.fn(async () => ({ success: true })),
  savePapers: vi.fn(),
}))
vi.mock('@/components/PdfJsViewer.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/composables/useSplitPane', () => ({ useSplitPane: () => ({
  compactLayout: false, coarsePointer: false, hoverCapable: true, leftPaneStyle: {}, dividerPercent: 60,
  onDividerPointerDown: vi.fn(), onDividerKeydown: vi.fn(), initDefaultSplitIfNeeded: vi.fn(),
  setup: vi.fn(), cleanup: vi.fn(),
}) }))
vi.mock('ant-design-vue/es/message', () => ({ default: { error: vi.fn(), success: vi.fn() } }))
vi.mock('ant-design-vue/es/button', () => ({ default: { template: '<button :disabled="disabled"><slot /></button>', props: ['disabled'] } }))
vi.mock('ant-design-vue/es/dropdown', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/empty', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/skeleton', () => ({ default: { template: '<div />' } }))
vi.mock('ant-design-vue/es/space', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/spin', () => ({ default: { template: '<div><slot /></div>' } }))
vi.mock('ant-design-vue/es/input', () => ({ Textarea: { template: '<textarea :disabled="disabled" />', props: ['disabled'] } }))
vi.mock('@ant-design/icons-vue', () => ({ RobotOutlined: { template: '<i />' }, CopyOutlined: { template: '<i />' } }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
async function settle() {
  for (let i = 0; i < 12; i++) await nextTick()
}
let app: App | undefined
let host: HTMLDivElement
function mountReader() {
  host = document.createElement('div')
  document.body.appendChild(host)
  let instance: any
  app = createApp(defineComponent({ setup: () => () => h(PaperReader, { ref: (value: unknown) => { instance = value } }) }))
  app.component('a-menu', { template: '<div><slot /></div>' })
  app.component('a-menu-item', { template: '<div><slot /></div>' })
  app.mount(host)
  return () => instance.$.setupState
}

beforeEach(() => {
  vi.resetAllMocks()
  route = reactive({ params: { id: '1' }, query: {} })
  mocks.getPaper.mockImplementation(async (id: number) => ({ id, title: `Paper ${id}` }))
  mocks.getLibraryPdfStreamUrl.mockImplementation(async (id: number) => `pdf-${id}`)
  mocks.getPaperReaderHistory.mockResolvedValue({ success: true, turns: [{ id: 10, role: 'assistant', content: 'History' }] })
  mocks.postPaperReaderOpening.mockResolvedValue({ success: true, opening: 'Opening' })
  mocks.postReaderFeedback.mockResolvedValue({ success: true })
})
afterEach(() => { app?.unmount(); app = undefined; host?.remove() })

describe('reader request ownership', () => {
  it('cancels the old PDF request and keeps the next paper loading until its own request finishes', async () => {
    const first = deferred<any>()
    const second = deferred<any>()
    mocks.getPaper.mockImplementation(async (id: number) => ({ id, title: `Paper ${id}`, source_url: `https://example.com/${id}` }))
    mocks.ensurePaperPdf.mockImplementation((id: number) => id === 1 ? first.promise : second.promise)
    const state = mountReader()
    await settle()
    expect(state().acquiringPdf).toBe(true)
    const oldSignal = mocks.ensurePaperPdf.mock.calls[0][1].signal as AbortSignal
    route.params.id = '2'
    await settle()
    expect(oldSignal.aborted).toBe(true)
    expect(mocks.ensurePaperPdf).toHaveBeenCalledTimes(2)
    first.resolve({ id: 1, title: 'Old paper', local_pdf_path: 'old.pdf' })
    await settle()
    expect(state().acquiringPdf).toBe(true)
    expect(state().paper.id).toBe(2)
    second.resolve({ id: 2, title: 'Current paper', local_pdf_path: 'current.pdf' })
    await settle()
    expect(state().acquiringPdf).toBe(false)
    expect(state().pdfSrc).toBe('pdf-2')
  })

  it('blocks sends until history is restored and updates a reply by message identity', async () => {
    const history = deferred<any>()
    const reply = deferred<any>()
    mocks.getPaperReaderHistory.mockReturnValue(history.promise)
    mocks.postPaperReaderChat.mockReturnValue(reply.promise)
    const state = mountReader()
    await settle()
    state().draft = 'Question'
    await state().send()
    expect(mocks.postPaperReaderChat).not.toHaveBeenCalled()
    expect(host.querySelector('textarea')?.disabled).toBe(true)
    history.resolve({ success: true, turns: [{ id: 10, role: 'assistant', content: 'Restored' }] })
    await settle()
    expect(state().messages.map((m: any) => m.content)).toEqual(['Restored'])
    expect(host.querySelector('textarea')?.disabled).toBe(false)
    const request = state().send()
    await settle()
    state().messages.unshift({ _uid: 999, role: 'assistant', content: 'Inserted message' })
    reply.resolve({ success: true, reply: 'Answer', assistant_turn_id: 42 })
    await request
    expect(state().messages.map((m: any) => m.content)).toEqual(['Inserted message', 'Restored', 'Question', 'Answer'])
    expect(state().messages.at(-1).turn_id).toBe(42)
  })

  it('ignores delayed history from a previous paper', async () => {
    const first = deferred<any>()
    mocks.getPaperReaderHistory.mockImplementation((id: number) => id === 1 ? first.promise : Promise.resolve({
      success: true, turns: [{ id: 20, role: 'assistant', content: 'Paper 2 history' }],
    }))
    const state = mountReader()
    await settle()
    route.params.id = '2'
    await settle()
    first.resolve({ success: true, turns: [{ id: 10, role: 'assistant', content: 'Paper 1 history' }] })
    await settle()
    expect(state().messages.map((m: any) => m.content)).toEqual(['Paper 2 history'])
  })

  it('credits the clicked answer, deduplicates concurrent feedback and retries failures', async () => {
    const pending = deferred<any>()
    mocks.postReaderFeedback.mockReturnValueOnce(pending.promise)
    const state = mountReader()
    await settle()
    state().messages = [
      { _uid: 101, role: 'assistant', content: 'Older answer', turn_id: 11 },
      { _uid: 102, role: 'assistant', content: 'Newer answer', turn_id: 22 },
    ]
    const older = state().messages[0]
    const click = state().gotoCitationPage(2, older)
    await state().gotoCitationPage(2, older)
    expect(mocks.postReaderFeedback).toHaveBeenCalledTimes(1)
    expect(mocks.postReaderFeedback).toHaveBeenCalledWith({ paper_id: 1, turn_id: 11, signal: 'citation_click' })
    pending.reject(new Error('Temporary failure'))
    await click
    expect(older.citationClickSent).toBeUndefined()
    await state().gotoCitationPage(2, older)
    await state().gotoCitationPage(2, older)
    expect(mocks.postReaderFeedback).toHaveBeenCalledTimes(2)
    expect(older.citationClickSent).toBe(true)
  })
})
