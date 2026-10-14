import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import PdfJsViewer from './PdfJsViewer.vue'

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }))
vi.mock('pdfjs-dist', () => ({ getDocument: pdf.getDocument, GlobalWorkerOptions: {} }))
vi.mock('ant-design-vue/es/button', () => ({ default: { render: () => h('button') } }))
vi.mock('ant-design-vue/es/input-number', () => ({ default: { render: () => null } }))
vi.mock('ant-design-vue/es/spin', () => ({ default: { render: () => null } }))

type Viewer = { gotoPage: (page: number, smooth?: boolean) => Promise<void> }
let app: App | undefined
let container: HTMLDivElement
const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')

const flush = async () => {
  for (let i = 0; i < 12; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  vi.stubGlobal('IntersectionObserver', class {
    observe() {}
    disconnect() {}
  })
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform() {} } as unknown as CanvasRenderingContext2D)
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
})

afterEach(async () => {
  app?.unmount()
  await flush()
  container.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScrollIntoView)
  else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollIntoView
})

function mountViewer(pageCount = 30) {
  const renders = new Map<number, number>()
  const doc = {
    numPages: pageCount,
    getPage: vi.fn(async (number: number) => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
      render: () => {
        renders.set(number, (renders.get(number) || 0) + 1)
        return { promise: Promise.resolve(), cancel: vi.fn() }
      },
    })),
    destroy: vi.fn(async () => {}),
  }
  pdf.getDocument.mockReturnValue({ promise: Promise.resolve(doc), destroy: vi.fn(async () => {}) })
  return { ...mountSource(), renders }
}

function mountSource() {
  const src = ref(`${window.location.origin}/paper.pdf`)
  let viewer: Viewer
  const loaded = vi.fn()
  const error = vi.fn()
  app = createApp({
    setup: () => () => h(PdfJsViewer, {
      src: src.value,
      ref: (instance: unknown) => { if (instance) viewer = instance as Viewer },
      onLoaded: loaded,
      onError: error,
    }),
  })
  app.mount(container)
  return { get viewer() { return viewer! }, loaded, error, src }
}

describe('PDF canvas retention', () => {
  it('releases old pixel buffers while preserving every page placeholder', async () => {
    const mounted = mountViewer()
    await flush()
    for (let page = 2; page <= 30; page++) await mounted.viewer.gotoPage(page, false)
    await flush()
    const canvases = [...container.querySelectorAll('canvas')]
    expect(canvases).toHaveLength(30)
    expect(canvases.filter((canvas) => canvas.width > 0)).toHaveLength(8)
    expect(canvases[0].width).toBe(0)
    expect(canvases[0].height).toBe(0)
    expect(canvases[29].width).toBeGreaterThan(0)
    expect(mounted.loaded).toHaveBeenCalledTimes(1)
    expect(mounted.error).not.toHaveBeenCalled()
  })

  it('rerenders an evicted page when the reader returns to it', async () => {
    const mounted = mountViewer(12)
    await flush()
    for (let page = 2; page <= 12; page++) await mounted.viewer.gotoPage(page, false)
    expect(mounted.renders.get(1)).toBe(1)
    await mounted.viewer.gotoPage(1, false)
    expect(mounted.renders.get(1)).toBe(2)
    expect(container.querySelector('canvas')!.width).toBeGreaterThan(0)
    expect([...container.querySelectorAll('canvas')].filter((canvas) => canvas.width > 0).length).toBeLessThanOrEqual(8)
  })

  it('frees pixel buffers when the source is cleared', async () => {
    const mounted = mountViewer(12)
    await flush()
    const canvases = [...container.querySelectorAll('canvas')]
    expect(canvases[0].width).toBeGreaterThan(0)
    mounted.src.value = ''
    await flush()
    expect(canvases.every((canvas) => canvas.width === 0 && canvas.height === 0)).toBe(true)
    expect(container.querySelectorAll('canvas')).toHaveLength(0)
  })

  it('waits for delayed cancellation across consecutive zooms before reusing the canvas', async () => {
    let busy = false
    let finishCancellation!: () => void
    const cancelled = new Promise<void>((_, reject) => {
      finishCancellation = () => {
        busy = false
        reject(Object.assign(new Error('cancelled'), { name: 'RenderingCancelledException' }))
      }
    })
    const cancel = vi.fn()
    let renderCount = 0
    const render = vi.fn(() => {
      if (busy) throw new Error('Cannot use the same canvas during multiple render() operations.')
      if (++renderCount === 1) {
        busy = true
        return { promise: cancelled, cancel }
      }
      return { promise: Promise.resolve(), cancel: vi.fn() }
    })
    const doc = {
      numPages: 1,
      getPage: vi.fn(async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render,
      })),
      destroy: vi.fn(async () => {}),
    }
    pdf.getDocument.mockReturnValue({ promise: Promise.resolve(doc), destroy: vi.fn(async () => {}) })
    const mounted = mountSource()
    await flush()
    const zoomIn = container.querySelector<HTMLButtonElement>('[aria-label="放大"]')!
    try {
      expect(render).toHaveBeenCalledTimes(1)
      zoomIn.click()
      await flush()
      zoomIn.click()
      await flush()
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(render).toHaveBeenCalledTimes(1)
    } finally {
      finishCancellation()
    }
    await flush()
    expect(render).toHaveBeenCalledTimes(2)
    expect(container.querySelector('.zoom-label')!.textContent).toBe('145%')
    expect(container.querySelector('canvas')!.classList.contains('pdf-canvas--ready')).toBe(true)
    expect(container.querySelector('.pdf-error')).toBeNull()
    expect(mounted.loaded).toHaveBeenCalledTimes(1)
    expect(mounted.error).not.toHaveBeenCalled()
  })

  it('keeps the latest document when an older source is still being destroyed', async () => {
    const makeDoc = (numPages: number) => ({
      numPages,
      getPage: vi.fn(async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
      })),
      destroy: vi.fn(async () => {}),
    })
    const oldDocument = makeDoc(1)
    const latestDocument = makeDoc(3)
    let finishDestroy!: () => void
    const destroying = new Promise<void>((resolve) => { finishDestroy = resolve })
    const oldTask = { promise: Promise.resolve(oldDocument), destroy: vi.fn(() => destroying) }
    const latestTask = { promise: Promise.resolve(latestDocument), destroy: vi.fn(async () => {}) }
    pdf.getDocument.mockReset()
    pdf.getDocument.mockImplementation(({ url }: { url: string }) => url.endsWith('/paper.pdf') ? oldTask : latestTask)
    const mounted = mountSource()
    await flush()
    try {
      mounted.src.value = `${window.location.origin}/intermediate.pdf`
      await flush()
      expect(oldTask.destroy).toHaveBeenCalledTimes(1)
      mounted.src.value = `${window.location.origin}/latest.pdf`
      await flush()
      expect(container.querySelectorAll('canvas')).toHaveLength(3)
    } finally {
      finishDestroy()
    }
    await flush()
    expect(pdf.getDocument.mock.calls.map(([options]) => options.url)).toEqual([
      `${window.location.origin}/paper.pdf`, `${window.location.origin}/latest.pdf`,
    ])
    expect(oldDocument.destroy).toHaveBeenCalledTimes(1)
    expect(latestDocument.destroy).not.toHaveBeenCalled()
    expect(container.querySelectorAll('canvas')).toHaveLength(3)
    expect(container.querySelector('canvas')!.width).toBeGreaterThan(0)
    expect(container.querySelector('.pdf-error')).toBeNull()
    expect(mounted.error).not.toHaveBeenCalled()
  })
})
