import { computed, ref, type Ref } from 'vue'

/** Split-pane layout for PaperReader: a draggable divider between the PDF pane
 *  and the chat pane, with an automatic tabbed (stacked) layout on narrow or
 *  touch-first viewports. Owns divider drag, keyboard resizing, the ResizeObserver
 *  that reflows on container resize, and pointer/hover capability detection.
 *
 *  Call ``setup()`` in ``onMounted`` and ``cleanup()`` in ``onBeforeUnmount``. */
export function useSplitPane(splitRef: Ref<HTMLElement | null>, dividerRef: Ref<HTMLElement | null>) {
  const compactLayout = ref(false)
  const coarsePointer = ref(false)
  const hoverCapable = ref(true)
  const leftWidthPx = ref<number | null>(null)
  const dragging = ref(false)
  const dragPointerId = ref<number | null>(null)
  const rafPending = ref(false)
  const lastClientX = ref<number | null>(null)
  let lastSplitWidth = 0
  let resizeObserver: ResizeObserver | null = null
  let coarseQuery: MediaQueryList | null = null
  let hoverQuery: MediaQueryList | null = null

  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

  const splitMinimums = (width: number) => ({
    pdf: width < 900 ? 360 : 420,
    chat: width < 900 ? 300 : 340,
  })

  const shouldUseTabbedLayout = (width: number) => {
    // Layout is determined by real content space and input capability, not the
    // user-agent string. Touch-first devices need more room for a usable split.
    const enterAt = coarsePointer.value ? 900 : 720
    const leaveAt = coarsePointer.value ? 940 : 760
    return compactLayout.value ? width < leaveAt : width < enterAt
  }

  const syncLayoutToAvailableWidth = (width: number) => {
    if (!Number.isFinite(width) || width <= 0) return
    compactLayout.value = shouldUseTabbedLayout(width)
    if (compactLayout.value) {
      lastSplitWidth = width
      return
    }
    const { pdf: minPdf, chat: minChat } = splitMinimums(width)
    const maxLeft = Math.max(minPdf, width - minChat)
    const previousRatio = lastSplitWidth > 0 && leftWidthPx.value != null
      ? leftWidthPx.value / lastSplitWidth
      : 0.66
    leftWidthPx.value = clamp(Math.round(width * previousRatio), minPdf, maxLeft)
    lastSplitWidth = width
  }

  const initDefaultSplitIfNeeded = () => {
    const el = splitRef.value
    if (!el) return
    syncLayoutToAvailableWidth(el.getBoundingClientRect().width)
  }

  const setLeftWidthFromClientX = (clientX: number) => {
    const el = splitRef.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    const { pdf: minPdf, chat: minChat } = splitMinimums(rect.width)
    const maxLeft = Math.max(minPdf, rect.width - minChat)
    const w = clamp(clientX - rect.left, minPdf, maxLeft)
    leftWidthPx.value = w
  }

  const leftPaneStyle = computed(() => {
    if (leftWidthPx.value == null) return {}
    return { flex: `0 0 ${leftWidthPx.value}px` }
  })

  const dividerPercent = computed(() => {
    const width = splitRef.value?.getBoundingClientRect().width || lastSplitWidth
    if (!width || leftWidthPx.value == null) return 66
    return Math.round((leftWidthPx.value / width) * 100)
  })

  const onDividerKeydown = (ev: KeyboardEvent) => {
    if (compactLayout.value) return
    const el = splitRef.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    const current = leftWidthPx.value ?? rect.width * 0.66
    const step = ev.shiftKey ? 48 : 16
    if (ev.key === 'ArrowLeft') leftWidthPx.value = current - step
    else if (ev.key === 'ArrowRight') leftWidthPx.value = current + step
    else if (ev.key === 'Home') leftWidthPx.value = rect.width * 0.5
    else if (ev.key === 'End') leftWidthPx.value = rect.width * 0.72
    else return
    ev.preventDefault()
    const { pdf: minPdf, chat: minChat } = splitMinimums(rect.width)
    leftWidthPx.value = clamp(leftWidthPx.value, minPdf, Math.max(minPdf, rect.width - minChat))
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

  const syncInputCapabilities = () => {
    coarsePointer.value = coarseQuery?.matches ?? false
    hoverCapable.value = hoverQuery?.matches ?? true
    initDefaultSplitIfNeeded()
  }

  const setup = () => {
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      coarseQuery = window.matchMedia('(pointer: coarse)')
      hoverQuery = window.matchMedia('(hover: hover)')
      coarseQuery.addEventListener('change', syncInputCapabilities)
      hoverQuery.addEventListener('change', syncInputCapabilities)
      syncInputCapabilities()
    } else {
      initDefaultSplitIfNeeded()
    }
    if (splitRef.value && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect.width || 0
        syncLayoutToAvailableWidth(width)
      })
      resizeObserver.observe(splitRef.value)
    }
  }

  const cleanup = () => {
    resizeObserver?.disconnect()
    resizeObserver = null
    coarseQuery?.removeEventListener('change', syncInputCapabilities)
    hoverQuery?.removeEventListener('change', syncInputCapabilities)
    coarseQuery = null
    hoverQuery = null
    endDrag()
  }

  return {
    compactLayout,
    coarsePointer,
    hoverCapable,
    dragging,
    leftPaneStyle,
    dividerPercent,
    onDividerPointerDown,
    onDividerKeydown,
    initDefaultSplitIfNeeded,
    setup,
    cleanup,
  }
}
