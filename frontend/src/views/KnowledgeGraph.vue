<template>
  <div class="kg-page">
    <div class="kg-toolbar">
      <a-space wrap>
        <a-button type="primary" :loading="loading" @click="load">刷新图谱</a-button>
        <a-button @click="exportPNG" :disabled="rawNodes.length === 0">导出 PNG</a-button>
        <a-input
          v-model:value="filterText"
          allow-clear
          placeholder="过滤节点（标题/作者/关键词）"
          style="width: 280px"
        />
        <a-select v-model:value="nodeType" style="width: 120px" allow-clear placeholder="类型">
          <a-select-option value="paper">论文</a-select-option>
          <a-select-option value="author">作者</a-select-option>
          <a-select-option value="keyword">关键词</a-select-option>
        </a-select>
        <a-slider
          v-if="yearExtent[0] < yearExtent[1]"
          range
          :min="yearExtent[0]"
          :max="yearExtent[1]"
          v-model:value="yearRange"
          :tip-formatter="(v: number) => String(v)"
          style="width: 200px; margin: 0 8px;"
        />
      </a-space>
      <div class="kg-meta">
        <a-tag color="blue">nodes {{ filteredNodes.length }}</a-tag>
        <a-tag color="purple">edges {{ filteredEdges.length }}</a-tag>
      </div>
    </div>
    <div class="kg-body">
      <div ref="svgWrap" class="kg-canvas">
        <div
          v-show="hoverTip.visible"
          class="kg-hover-tip"
          :style="{ left: hoverTip.x + 'px', top: hoverTip.y + 'px' }"
        >
          <div class="kg-hover-tip__title">{{ hoverTip.title }}</div>
          <div class="kg-hover-tip__meta">{{ hoverTip.meta }}</div>
        </div>
      </div>
      <div class="kg-side">
        <a-card size="small" :title="selectedEdge ? '边信息' : '节点信息'" :bordered="false">
          <div v-if="selected == null && selectedEdge == null" class="kg-empty">
            <template v-if="!loading && rawNodes.length === 0">
              图谱还是空的。先去「文献搜索」保存几篇论文，知识图谱会自动构建论文间的关系网络。
            </template>
            <template v-else>点击图中节点或边查看详情</template>
          </div>
          <div v-else-if="selectedEdge" class="kg-info">
            <div class="kg-info__title">{{ selectedEdge.sourceLabel }} → {{ selectedEdge.targetLabel }}</div>
            <div class="kg-info__kv"><strong>relation</strong>：{{ selectedEdge.type }}</div>
            <div class="kg-info__kv"><strong>含义</strong>：{{ edgeTypeLabel(selectedEdge.type) }}</div>
            <div v-if="selectedEdge.weight != null" class="kg-info__kv">
              <strong>weight</strong>：{{ selectedEdge.weight }}
            </div>
            <div v-if="selectedEdge.evidence" class="kg-info__kv">
              <strong>构建理由</strong>：
              <div class="kg-evidence">{{ selectedEdge.evidence }}</div>
            </div>
            <div class="kg-info__kv"><strong>source</strong>：{{ selectedEdge.source }}</div>
            <div class="kg-info__kv"><strong>target</strong>：{{ selectedEdge.target }}</div>
            <a-button
              v-if="selectedEdge.sourcePaperId"
              type="link"
              @click="openPaper(selectedEdge.sourcePaperId)"
            >
              打开 source 阅读页
            </a-button>
            <a-button
              v-if="selectedEdge.targetPaperId"
              type="link"
              @click="openPaper(selectedEdge.targetPaperId)"
            >
              打开 target 阅读页
            </a-button>
          </div>
          <div v-else class="kg-info">
            <div class="kg-info__title">{{ selected!.label }}</div>
            <div class="kg-info__kv"><strong>type</strong>：{{ selected!.type }}</div>
            <div v-if="selected!.year != null" class="kg-info__kv"><strong>year</strong>：{{ selected!.year }}</div>
            <div v-if="selected!.category" class="kg-info__kv"><strong>category</strong>：{{ selected!.category }}</div>
            <template v-if="selected!.type === 'paper' && selected!.paper_id">
              <div v-if="paperDetailLoading" class="kg-info__kg-info__kv" style="color: var(--pg-text-tertiary);">加载论文详情中…</div>
              <template v-else-if="paperDetail">
                <div v-if="paperDetail.authors?.length" class="kg-info__kv"><strong>authors</strong>：{{ paperDetail.authors.map((a: any) => a.name).join(', ') }}</div>
                <div v-if="paperDetail.journal" class="kg-info__kv"><strong>{{ paperDetail.venue_type === 'conference' ? 'conference' : paperDetail.venue_type === 'preprint' ? 'preprint' : 'journal' }}</strong>：{{ paperDetail.journal }}</div>
                <div v-if="paperDetail.doi" class="kg-info__kv"><strong>doi</strong>：{{ paperDetail.doi }}</div>
                <div v-if="paperDetail.abstract" class="kg-info__kv">
                  <strong>abstract</strong>：
                  <div class="kg-abstract">{{ paperDetail.abstract.slice(0, 400) }}{{ paperDetail.abstract.length > 400 ? '…' : '' }}</div>
                </div>
              </template>
              <a-button type="link" @click="openPaper(selected!.paper_id)">打开阅读页</a-button>
            </template>
            <div v-if="selected!.paper_id" class="kg-info__kv"><strong>paper_id</strong>：{{ selected!.paper_id }}</div>
          </div>
        </a-card>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import * as d3 from 'd3'
import apiClient, { getPaper } from '@/services/api'
type GraphNode = {
  id: string
  type: 'paper' | 'author' | 'keyword' | string
  label: string
  paper_id?: number | null
  year?: number | null
  category?: string | null
  weight?: number
}
type GraphEdge = { source: string; target: string; type: string; weight?: number; evidence?: string }
type SelectedEdge = GraphEdge & {
  sourceLabel: string
  targetLabel: string
  sourcePaperId?: number | null
  targetPaperId?: number | null
}
type GraphResponse = { success: boolean; nodes: GraphNode[]; edges: GraphEdge[]; message?: string }
const router = useRouter()
const svgWrap = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const rawNodes = ref<GraphNode[]>([])
const rawEdges = ref<GraphEdge[]>([])
const selected = ref<GraphNode | null>(null)
const selectedEdge = ref<SelectedEdge | null>(null)
const paperDetail = ref<any>(null)
const paperDetailLoading = ref(false)
const hoverTip = ref<{ visible: boolean; x: number; y: number; title: string; meta: string }>({
  visible: false,
  x: 0,
  y: 0,
  title: '',
  meta: '',
})
const filterText = ref('')
const nodeType = ref<string | undefined>(undefined)
const hideHoverTip = () => { hoverTip.value.visible = false }
const moveHoverTip = (ev: any) => {
  if (!hoverTip.value.visible) return
  hoverTip.value.x = (ev?.offsetX ?? hoverTip.value.x) + 12
  hoverTip.value.y = (ev?.offsetY ?? hoverTip.value.y) + 12
}
const yearRange = ref<[number, number]>([1990, new Date().getFullYear()])
const yearExtent = computed(() => {
  const ys = rawNodes.value.map((n) => n.year).filter((y) => y != null && y > 1900) as number[]
  return ys.length ? [Math.min(...ys), Math.max(...ys)] as [number, number] : [1990, new Date().getFullYear()] as [number, number]
})
const filteredNodes = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  const [yl, yh] = yearRange.value
  return rawNodes.value.filter((n) => {
    if (nodeType.value && n.type !== nodeType.value) return false
    if (n.year != null && (n.year < yl || n.year > yh)) return false
    if (!q) return true
    return (n.label || '').toLowerCase().includes(q)
  })
})
const filteredNodeSet = computed(() => new Set(filteredNodes.value.map((n) => n.id)))
const filteredEdges = computed(() => {
  const s = filteredNodeSet.value
  return rawEdges.value.filter((e) => s.has(e.source) && s.has(e.target))
})
let cleanup: (() => void) | null = null
const exportPNG = () => {
  const wrap = svgWrap.value
  if (!wrap) return
  const svg = wrap.querySelector('svg')
  if (!svg) {
    message.warning('图谱尚未渲染')
    return
  }
  try {
    const svgData = new XMLSerializer().serializeToString(svg)
    const canvas = document.createElement('canvas')
    const rect = svg.getBoundingClientRect()
    canvas.width = rect.width * 2
    canvas.height = rect.height * 2
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    const img = new Image()
    img.onload = () => {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      canvas.toBlob((blob) => {
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `knowledge_graph_${new Date().toISOString().slice(0, 10)}.png`
        a.click()
        URL.revokeObjectURL(url)
        message.success('图谱已导出为 PNG')
      })
    }
    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)))
  } catch (e) {
    message.error('导出失败')
  }
}
const openPaper = (id: number) => {
  const href = router.resolve({ path: `/library/read/${id}`, query: { standalone: '1' } }).href
  window.open(href, '_blank', 'noopener,noreferrer')
}
const edgeTypeLabel = (t: string): string => {
  const k = String(t || '').trim()
  if (!k) return '（未知）'
  if (k === 'authored_by') return '论文 → 作者（作者关系）'
  if (k === 'has_keyword') return '论文 → 关键词（主题/术语关联）'
  if (k === 'co_keyword') return '关键词 ↔ 关键词（共现关系）'
  if (k.startsWith('paper_')) {
    const rel = k.slice('paper_'.length).trim().toLowerCase()
    if (!rel) return '论文 ↔ 论文（关系）'
    const map: Record<string, string> = {
      related: '相关/相似',
      similar: '相似',
      cites: '引用',
      cited_by: '被引用',
      extends: '扩展/继承',
      improves: '改进',
      uses: '使用/基于',
      compares: '对比',
      surveys: '综述/系统总结',
      references: '提及/参考',
      dataset_overlap: '数据集交集（弱边）',
      method_overlap: '方法交集（弱边）',
      task_overlap: '任务交集（弱边）',
      supports: '支持',
      contradicts: '反驳/矛盾',
      background: '背景/基础',
    }
    return `论文 ↔ 论文（${map[rel] || rel}）`
  }
  return '（自定义关系）'
}
async function load() {
  loading.value = true
  selected.value = null
  selectedEdge.value = null
  try {
    const r = await apiClient.get<GraphResponse>('/api/papers/graph/library', {
      params: { limit: 250, include_authors: false, include_keywords: false, relation_edge_limit: 450 },
    })
    if (!r.data?.success) throw new Error(r.data?.message || '加载失败')
    rawNodes.value = r.data.nodes || []
    rawEdges.value = (r.data.edges || []).map((e) => ({ ...e }))
  } catch (e: unknown) {
    message.error((e as Error).message || '图谱加载失败（请确认后端已启动且通过 Vite 访问以走 /api 代理）')
    rawNodes.value = []
    rawEdges.value = []
  } finally {
    render()
    loading.value = false
  }
}
async function expandPaper(paperId: number) {
  loading.value = true
  try {
    const r = await apiClient.get<GraphResponse>('/api/papers/graph/library', {
      params: {
        limit: 250,
        focus_paper_id: paperId,
        include_authors: true,
        include_keywords: true,
        relation_edge_limit: 800,
      },
    })
    if (!r.data?.success) return
    const existingN = new Set(rawNodes.value.map((n) => n.id))
    for (const n of r.data.nodes || []) {
      if (!existingN.has(n.id)) rawNodes.value.push(n)
    }
    const existingE = new Set(rawEdges.value.map((e) => `${e.source}|${e.target}|${e.type}`))
    for (const e of r.data.edges || []) {
      const k = `${e.source}|${e.target}|${e.type}`
      if (!existingE.has(k)) rawEdges.value.push(e)
    }
    render()
  } finally {
    loading.value = false
  }
}
watch(selected, (node) => {
  paperDetail.value = null
  if (node?.type === 'paper' && node?.paper_id) {
    paperDetailLoading.value = true
    getPaper(Number(node.paper_id))
      .then((d) => { paperDetail.value = d })
      .catch(() => { paperDetail.value = null })
      .finally(() => { paperDetailLoading.value = false })
  } else {
    paperDetailLoading.value = false
  }
})
function colorFor(t: string) {
  if (t === 'paper') return '#6366f1'
  if (t === 'author') return '#14b8a6'
  if (t === 'keyword') return '#8b5cf6'
  return 'var(--pg-text-tertiary)'
}
function render() {
  if (!svgWrap.value) return
  if (cleanup) cleanup()
  const tipEl = svgWrap.value.querySelector('.kg-hover-tip')
  svgWrap.value.innerHTML = ''
  if (tipEl) svgWrap.value.appendChild(tipEl)
  const wrap = svgWrap.value
  const width = wrap.clientWidth || 900
  const height = wrap.clientHeight || 640
  const svg = d3
    .select(wrap)
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .style('background', 'transparent')
    .on('click', () => {
      selected.value = null
      selectedEdge.value = null
      paperDetail.value = null
      hideHoverTip()
    })
  const g = svg.append('g')
  const zoom = d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.2, 4]).on('zoom', (ev) => {
    g.attr('transform', ev.transform)
  })
  svg.call(zoom as any)
  const centerOnNode = (d: any, opts?: { zoomIn?: boolean }) => {
    if (!d) return
    const svgEl = svg.node()
    if (!svgEl) return
    const cur = d3.zoomTransform(svgEl)
    const targetK = opts?.zoomIn ? Math.max(cur.k, 1.15) : cur.k
    const tx = width / 2 - (d.x ?? 0) * targetK
    const ty = height / 2 - (d.y ?? 0) * targetK
    svg
      .transition()
      .duration(350)
      .call(zoom.transform as any, d3.zoomIdentity.translate(tx, ty).scale(targetK))
  }
  const nodes = filteredNodes.value.map((n) => ({ ...n }))
  const nodeById = new Map(nodes.map((n) => [n.id, n]))
  const links = filteredEdges.value
    .map((e) => ({
      source: nodeById.get(e.source) ?? e.source,
      target: nodeById.get(e.target) ?? e.target,
      type: e.type,
      weight: e.weight ?? 1,
      evidence: e.evidence ?? '',
    }))
    .filter((l) => typeof l.source !== 'string' && typeof l.target !== 'string') as any[]
  const sim = d3
    .forceSimulation<any>(nodes as any)
    .force('link', d3.forceLink<any, any>(links).id((d: any) => d.id).distance(60).strength(0.4))
    .force('charge', d3.forceManyBody().strength(-180))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide().radius((d: any) => 6 + Math.sqrt(d.weight ?? 1) * 2))
  const link = g
    .append('g')
    .attr('stroke', 'var(--pg-divider)')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke-width', (d: any) => Math.max(1, Math.min(4, (d.weight ?? 1) * 0.6)))
    .on('click', (ev: any, d: any) => {
      ev.stopPropagation()
      const s = d?.source
      const t = d?.target
      if (!s || !t) return
      selected.value = null
      selectedEdge.value = {
        source: String(s.id),
        target: String(t.id),
        type: String(d.type || ''),
        weight: Number(d.weight ?? 1),
        evidence: String(d.evidence || '').trim() || undefined,
        sourceLabel: String(s.label || s.id || ''),
        targetLabel: String(t.label || t.id || ''),
        sourcePaperId: s.type === 'paper' ? (s.paper_id ?? null) : null,
        targetPaperId: t.type === 'paper' ? (t.paper_id ?? null) : null,
      }
      centerOnNode(s, { zoomIn: false })
    })
    .on('mouseenter', (ev: any, d: any) => {
      const s = d?.source
      const t = d?.target
      if (!s || !t) return
      const meta = [edgeTypeLabel(String(d?.type || ''))]
      if (d?.weight != null) meta.push(`w=${d.weight}`)
      hoverTip.value = {
        visible: true,
        x: (ev?.offsetX ?? 0) + 12,
        y: (ev?.offsetY ?? 0) + 12,
        title: `${String(s.label || s.id)} → ${String(t.label || t.id)}`,
        meta: meta.join(' · '),
      }
    })
    .on('mousemove', moveHoverTip)
    .on('mouseleave', hideHoverTip)
  const node = g
    .append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', (d: any) => 5 + Math.sqrt(d.weight ?? 1) * 1.5)
    .attr('fill', (d: any) => colorFor(d.type))
    .attr('stroke', 'var(--pg-surface)')
    .attr('stroke-width', 1.5)
    .call(
      d3
        .drag<any, any>()
        .on('start', (ev, d) => {
          if (!ev.active) sim.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (ev, d) => {
          d.fx = ev.x
          d.fy = ev.y
        })
        .on('end', (ev, d) => {
          if (!ev.active) sim.alphaTarget(0)
          d.fx = null
          d.fy = null
        }) as any
    )
    .on('click', (ev: any, d: any) => {
      ev.stopPropagation()
      selected.value = d
      selectedEdge.value = null
      centerOnNode(d, { zoomIn: d?.type === 'paper' })
      if (d?.type === 'paper' && d?.paper_id) {
        void expandPaper(Number(d.paper_id))
      }
    })
    .on('mouseenter', (ev: any, d: any) => {
      const t = String(d?.label || '').trim()
      const metaParts: string[] = []
      if (d?.type) metaParts.push(String(d.type))
      if (d?.year != null) metaParts.push(String(d.year))
      if (d?.category) metaParts.push(String(d.category))
      hoverTip.value = {
        visible: true,
        x: (ev?.offsetX ?? 0) + 12,
        y: (ev?.offsetY ?? 0) + 12,
        title: t || '（无标题）',
        meta: metaParts.join(' · '),
      }
    })
    .on('mousemove', moveHoverTip)
    .on('mouseleave', hideHoverTip)
  sim.on('tick', () => {
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)
    node.attr('cx', (d: any) => d.x).attr('cy', (d: any) => d.y)
  })
  cleanup = () => {
    sim.stop()
  }
}
watch([filterText, nodeType], () => {
  render()
})
onMounted(() => {
  load()
})
onBeforeUnmount(() => {
  if (cleanup) cleanup()
})
</script>
<style scoped>
.kg-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.kg-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 16px;
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg);
  box-shadow: var(--pg-shadow-xs);
}
.kg-meta {
  display: flex;
  gap: 6px;
}
.kg-meta :deep(.ant-tag) {
  margin-inline-end: 0;
  border-radius: var(--pg-radius-pill);
}
.kg-body {
  display: grid;
  grid-template-columns: 1fr clamp(260px, 25vw, 340px);
  gap: 14px;
  min-height: 70vh;
}
.kg-canvas {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg);
  overflow: hidden;
  min-height: 70vh;
  position: relative;
  background: var(--pg-surface);
  box-shadow: var(--pg-shadow-xs);
}
.kg-side {
  min-height: 70vh;
}
.kg-side :deep(.ant-card) {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg);
  box-shadow: var(--pg-shadow-xs);
}
.kg-side :deep(.ant-card-head) {
  border-bottom: 1px solid var(--pg-border-soft);
  min-height: 44px;
}
.kg-empty {
  color: var(--pg-text-tertiary);
  line-height: 1.6;
  font-size: 13px;
}
.kg-info__title {
  font-weight: 700;
  margin-bottom: 8px;
  color: var(--pg-text);
  line-height: 1.4;
}
.kg-abstract {
  font-size: 12px;
  line-height: 1.55;
  color: var(--pg-text-secondary);
  max-height: 200px;
  overflow-y: auto;
  margin-top: 4px;
}
.kg-info__kv {
  margin: 5px 0;
  font-size: 13px;
  color: var(--pg-text-secondary);
  line-height: 1.5;
}
.kg-info__kv strong {
  color: var(--pg-text);
  font-weight: 600;
}
.kg-evidence {
  padding: 10px 12px;
  border-radius: var(--pg-radius);
  background: var(--pg-bg-soft);
  border: 1px solid var(--pg-border-soft);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
  color: var(--pg-text);
  margin-top: 6px;
}
.kg-hover-tip {
  position: absolute;
  z-index: 5;
  pointer-events: none;
  max-width: 420px;
  background: rgba(255, 255, 255, 0.97);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius);
  box-shadow: var(--pg-shadow-lg);
  padding: 10px 12px;
  backdrop-filter: blur(8px);
}
.kg-hover-tip__title {
  font-weight: 700;
  font-size: 13px;
  line-height: 1.35;
  color: var(--pg-text);
  word-break: break-word;
  margin-bottom: 4px;
}
.kg-hover-tip__meta {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  line-height: 1.3;
  word-break: break-word;
}
@media (max-width: 1000px) {
  .kg-body {
  grid-template-columns: 1fr;
  }
  .kg-side {
  min-height: auto;
  }
}
@media (max-width: 640px) {
  .kg-toolbar {
    padding: 10px 12px;
  }
  .kg-canvas {
    min-height: 50vh;
  }
}
</style>