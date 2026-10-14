<template>
  <div class="kb">
    <!-- 统计卡片 -->
    <div class="kb__stats">
      <div class="kb__stat-card kb__stat-card--total">
        <div class="kb__stat-icon">
          <DatabaseOutlined />
        </div>
        <div class="kb__stat-body">
          <div class="kb__stat-value">{{ stats?.total ?? '—' }}</div>
          <div class="kb__stat-label">记忆总数</div>
        </div>
      </div>
      <div class="kb__stat-card kb__stat-card--access">
        <div class="kb__stat-icon">
          <ThunderboltOutlined />
        </div>
        <div class="kb__stat-body">
          <div class="kb__stat-value">{{ stats?.total_accesses ?? '—' }}</div>
          <div class="kb__stat-label">累计检索命中</div>
        </div>
      </div>
      <div class="kb__stat-card kb__stat-card--utility">
        <div class="kb__stat-icon">
          <RiseOutlined />
        </div>
        <div class="kb__stat-body">
          <div class="kb__stat-value">{{ avgUtility }}</div>
          <div class="kb__stat-label">平均反馈效用</div>
        </div>
      </div>
      <div class="kb__stat-card kb__stat-card--evolution">
        <div class="kb__stat-icon">
          <SyncOutlined />
        </div>
        <div class="kb__stat-body">
          <div class="kb__stat-value">{{ stats?.evolution_events_24h ?? '—' }}</div>
          <div class="kb__stat-label">24h 进化事件</div>
        </div>
      </div>
    </div>

    <!-- 进化健康度 -->
    <div v-if="stats?.health" class="kb__health">
      <div class="kb__health-block">
        <div class="kb__health-title">
          <span class="kb__health-dot kb__health-dot--signal"></span>隐式反馈信号
        </div>
        <div class="kb__health-signals">
          <div class="kb__signal">
            <span class="kb__signal-num">{{ stats.health.feedback.citation_click }}</span>
            <span class="kb__signal-label">引用点击</span>
          </div>
          <div class="kb__signal">
            <span class="kb__signal-num">{{ stats.health.feedback.engaged_read }}</span>
            <span class="kb__signal-label">深度阅读</span>
          </div>
          <template v-if="stats.health.feedback.up || stats.health.feedback.down">
            <div class="kb__signal">
              <span class="kb__signal-num kb__health-pos">{{ stats.health.feedback.up }}</span>
              <span class="kb__signal-label">正向</span>
            </div>
            <div class="kb__signal">
              <span class="kb__signal-num kb__health-neg">{{ stats.health.feedback.down }}</span>
              <span class="kb__signal-label">负向</span>
            </div>
          </template>
        </div>
        <div class="kb__health-hint">无感采集：点击引用锚点、持续深读都会为相关记忆记功</div>
      </div>
      <div class="kb__health-block">
        <div class="kb__health-title">
          <span class="kb__health-dot kb__health-dot--dist"></span>效用分布
        </div>
        <div class="kb__dist-bar" role="img" :aria-label="`效用分布：正向 ${stats.health.utility_distribution.positive}，中性 ${stats.health.utility_distribution.neutral}，负向 ${stats.health.utility_distribution.negative}`">
          <div class="kb__dist-seg kb__dist-seg--pos" :style="{ width: distPct.positive + '%' }"></div>
          <div class="kb__dist-seg kb__dist-seg--neutral" :style="{ width: distPct.neutral + '%' }"></div>
          <div class="kb__dist-seg kb__dist-seg--neg" :style="{ width: distPct.negative + '%' }"></div>
        </div>
        <div class="kb__dist-legend">
          <span><i class="kb__dist-key kb__dist-key--pos"></i>正向 {{ stats.health.utility_distribution.positive }}</span>
          <span><i class="kb__dist-key kb__dist-key--neutral"></i>中性 {{ stats.health.utility_distribution.neutral }}</span>
          <span><i class="kb__dist-key kb__dist-key--neg"></i>负向 {{ stats.health.utility_distribution.negative }}</span>
        </div>
        <div class="kb__health-hint">隐式反馈经模型归因后的记忆效用分布</div>
      </div>
      <div class="kb__health-block">
        <div class="kb__health-title">
          <span class="kb__health-dot kb__health-dot--activity"></span>近 7 天进化活动
        </div>
        <div class="kb__health-row">
          <span v-for="(n, ev) in stats.health.evolution_7d" :key="ev" class="kb__activity-chip">
            {{ eventLabel(String(ev)) }} <b>{{ n }}</b>
          </span>
          <span v-if="!Object.keys(stats.health.evolution_7d).length" class="kb__health-none">暂无</span>
        </div>
        <div v-if="dailyActivity.some((d) => d.count > 0)" class="kb__trend">
          <div
            v-for="d in dailyActivity"
            :key="d.label"
            class="kb__trend-col"
            :title="`${d.label}: ${d.count} 事件`"
          >
            <div class="kb__trend-bar" :style="{ height: `${d.pct}%` }"></div>
            <div class="kb__trend-label">{{ d.label }}</div>
          </div>
        </div>
        <div class="kb__health-hint">周期审查、反思提炼与反馈归因产生的事件</div>
      </div>
    </div>

    <div class="kb__main">
      <!-- 记忆列表 -->
      <div class="kb__panel kb__panel--memories">
        <div class="kb__panel-head">
          <h3 class="kb__panel-title">
            记忆条目
            <span v-if="memories.length" class="kb__panel-count">{{ memories.length }}</span>
          </h3>
          <div class="kb__filters">
            <a-select v-model:value="scope" class="kb__filter" @change="onScopeChange">
              <a-select-option value="all">全部范围</a-select-option>
              <a-select-option value="global">全局偏好</a-select-option>
              <a-select-option value="paper">按论文</a-select-option>
              <a-select-option value="agent">Agent 共享</a-select-option>
            </a-select>
            <a-select
              v-if="scope === 'paper'"
              v-model:value="paperId"
              class="kb__filter kb__filter--paper"
              allow-clear
              show-search
              :filter-option="filterPaperOption"
              placeholder="全部论文"
              @change="loadMemories"
            >
              <a-select-option v-for="p in paperOptions" :key="p.id" :value="p.id" :label="p.title">
                {{ p.title }}
              </a-select-option>
            </a-select>
            <a-select v-model:value="kind" class="kb__filter" @change="loadMemories">
              <a-select-option value="">全部类型</a-select-option>
              <a-select-option value="working">working</a-select-option>
              <a-select-option value="short">short</a-select-option>
              <a-select-option value="long">long</a-select-option>
              <a-select-option value="paper_summary">paper_summary</a-select-option>
              <a-select-option value="preference">preference</a-select-option>
              <a-select-option value="episodic">episodic</a-select-option>
              <a-select-option value="procedural">procedural</a-select-option>
              <a-select-option value="reflection">reflection（元记忆）</a-select-option>
            </a-select>
            <a-select v-model:value="sort" class="kb__filter" @change="loadMemories">
              <a-select-option value="recency">最近写入</a-select-option>
              <a-select-option value="utility">反馈效用</a-select-option>
              <a-select-option value="access">命中次数</a-select-option>
              <a-select-option value="importance">重要性</a-select-option>
            </a-select>
          </div>
        </div>
        <a-spin :spinning="loadingMemories">
          <div v-if="!memories.length && !loadingMemories" class="kb__empty">
            <div class="kb__empty-icon">🌱</div>
            <div>暂无记忆 —— 去文献阅读页和论文聊几轮</div>
            <div class="kb__empty-sub">知识库会在使用中开始积累并自我进化</div>
          </div>
          <div
            v-for="m in memories"
            :key="m.memory_id"
            v-memo="[m]"
            class="kb__mem"
            :class="{ 'kb__mem--reflection': m.kind === 'reflection' }"
          >
            <div class="kb__mem-head">
              <a-tag :color="kindColor(m.kind)" class="kb__tag">{{ m.kind }}</a-tag>
              <router-link
                v-if="m.scope === 'paper' && m.paper_id"
                class="kb__mem-scope kb__mem-scope--link"
                :to="`/library/read/${m.paper_id}`"
                :title="m.paper_title || `论文 #${m.paper_id}`"
              >{{ m.paper_title || `论文 #${m.paper_id}` }}</router-link>
              <span v-else-if="m.scope === 'global'" class="kb__mem-scope">全局</span>
              <span v-else-if="m.scope === 'agent'" class="kb__mem-scope">agent</span>
              <span class="kb__mem-time">{{ formatRelativeTime(m.timestamp) }}</span>
            </div>
            <div class="kb__mem-content">{{ m.content }}</div>
            <div class="kb__mem-metrics">
              <div class="kb__gauge" :title="`重要性 ${m.importance.toFixed(2)}（写入时模型评定）`">
                <span class="kb__gauge-label">重要性</span>
                <span class="kb__gauge-track">
                  <span class="kb__gauge-fill kb__gauge-fill--imp" :style="{ width: `${m.importance * 100}%` }"></span>
                </span>
                <b class="kb__gauge-num">{{ m.importance.toFixed(2) }}</b>
              </div>
              <div class="kb__gauge" :title="`反馈效用 ${m.utility.toFixed(2)}（隐式反馈经模型归因累积，范围 -2 ~ +5）`">
                <span class="kb__gauge-label">效用</span>
                <span class="kb__gauge-track kb__gauge-track--utility">
                  <span class="kb__gauge-zero"></span>
                  <span
                    class="kb__gauge-marker"
                    :class="utilityClass(m.utility)"
                    :style="{ left: `${utilityPct(m.utility)}%` }"
                  ></span>
                </span>
                <b class="kb__gauge-num" :class="utilityClass(m.utility)">{{ m.utility >= 0 ? '+' : '' }}{{ m.utility.toFixed(2) }}</b>
              </div>
              <span class="kb__metric" title="被检索注入 prompt 的次数">
                命中 <b>{{ m.access_count }}</b>
              </span>
              <span class="kb__metric kb__metric--muted" title="最近一次被检索命中的时间">
                最近命中 {{ formatRelativeTime(m.last_used) }}
              </span>
            </div>
          </div>
        </a-spin>
      </div>

      <!-- 进化时间线 -->
      <div class="kb__panel kb__panel--evolution">
        <div class="kb__panel-head">
          <h3 class="kb__panel-title">进化时间线</h3>
          <a-space>
            <a-button size="small" type="text" @click="openTraces">追踪</a-button>
            <a-button size="small" type="text" @click="loadEvolution">刷新</a-button>
          </a-space>
        </div>
        <a-spin :spinning="loadingEvolution">
          <div v-if="!evolution.length && !loadingEvolution" class="kb__empty">
            <div class="kb__empty-icon">🧬</div>
            <div>还没有进化事件</div>
            <div class="kb__empty-sub">点击引用锚点、持续深读，或等待周期整理</div>
          </div>
          <div class="kb__timeline">
            <div v-for="e in evolution" :key="`${e.source_db}-${e.id}`" v-memo="[e]" class="kb__evo">
              <span class="kb__evo-badge" :class="`kb__evo-badge--${eventTone(e.event)}`">{{ eventIcon(e.event) }}</span>
              <div class="kb__evo-body">
                <div class="kb__evo-head">
                  <span class="kb__evo-event">{{ eventLabel(e.event) }}</span>
                  <span v-if="e.utility_delta" class="kb__evo-delta" :class="e.utility_delta > 0 ? 'kb__evo-delta--up' : 'kb__evo-delta--down'">
                    {{ e.utility_delta > 0 ? '+' : '' }}{{ e.utility_delta.toFixed(2) }}
                  </span>
                  <span class="kb__evo-time">{{ formatRelativeTime(e.created_at) }}</span>
                </div>
                <div v-if="e.reason" class="kb__evo-reason">{{ e.reason }}</div>
              </div>
            </div>
          </div>
        </a-spin>
      </div>
    </div>

    <!-- LLM trace 抽屉：会话列表 → 事件回放 -->
    <a-drawer v-model:open="tracesOpen" title="LLM 调用追踪" width="560" placement="right">
      <a-spin :spinning="loadingTraces">
        <div v-if="!traceSessions.length && !loadingTraces" class="kb__empty">
          <div class="kb__empty-icon">🔍</div>
          <div>暂无 trace 会话</div>
        </div>
        <div v-if="!activeTrace" class="kb__traces">
          <div
            v-for="s in traceSessions"
            :key="s.session_id"
            class="kb__trace-row"
            @click="openTrace(s.session_id)"
          >
            <div class="kb__trace-row-head">
              <span class="kb__trace-agent">{{ s.agent_name || 'agent' }}</span>
              <span class="kb__trace-time">{{ formatRelativeTime(s.updated_at) }}</span>
            </div>
            <div class="kb__trace-meta">{{ s.session_id }} · {{ s.event_count }} 事件</div>
          </div>
        </div>
        <div v-else class="kb__trace-detail">
          <a-button size="small" type="link" @click="closeTrace">← 返回会话列表</a-button>
          <div v-for="(ev, i) in activeTraceEvents" :key="i" class="kb__trace-event">
            <div class="kb__trace-event-head">
              <span class="kb__trace-event-name">{{ ev.event }}</span>
              <span class="kb__trace-time">{{ ev.ts?.slice(11, 19) }}</span>
            </div>
            <pre class="kb__trace-payload">{{ payloadPreview(ev.payload) }}</pre>
          </div>
        </div>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  DatabaseOutlined,
  ThunderboltOutlined,
  RiseOutlined,
  SyncOutlined,
} from '@ant-design/icons-vue'
import AButton from 'ant-design-vue/es/button'
import ADrawer from 'ant-design-vue/es/drawer'
import ASpace from 'ant-design-vue/es/space'
import ASelect, { SelectOption as ASelectOption } from 'ant-design-vue/es/select'
import ASpin from 'ant-design-vue/es/spin'
import ATag from 'ant-design-vue/es/tag'
import {
  getKnowledgeEvolution,
  getKnowledgeMemories,
  getKnowledgeStats,
  type KnowledgeEvolutionItem,
  type KnowledgeMemoryItem,
  type KnowledgeScope,
  type KnowledgeSort,
  type KnowledgeStats,
} from '@/services/api/knowledge'
import { getTraceEvents, getTraceSessions, type TraceEvent, type TraceSession } from '@/services/api/traces'
import { formatRelativeTime } from '@/utils/relativeTime'

const stats = ref<KnowledgeStats | null>(null)
const memories = ref<KnowledgeMemoryItem[]>([])
const evolution = ref<KnowledgeEvolutionItem[]>([])
const scope = ref<KnowledgeScope>('all')
const paperId = ref<number | undefined>(undefined)
const paperOptions = ref<{ id: number; title: string }[]>([])
const kind = ref<string>('')
const sort = ref<KnowledgeSort>('recency')
const loadingMemories = ref(false)
const loadingEvolution = ref(false)
const tracesOpen = ref(false)
const loadingTraces = ref(false)
const traceSessions = ref<TraceSession[]>([])
const activeTrace = ref<string | null>(null)
const activeTraceEvents = ref<TraceEvent[]>([])
let memoriesRequestSeq = 0
let evolutionRequestSeq = 0
let statsRequestSeq = 0
let traceRequestSeq = 0
let paperOptionsRequestSeq = 0

const avgUtility = computed(() => {
  if (!stats.value) return '—'
  const v = stats.value.avg_utility
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
})

// The stats endpoint aggregates every event by calendar day in the user's time zone.
const dailyActivity = computed(() => {
  const days = (stats.value?.activity_7d || []).map((day) => {
    const [, month, date] = day.date.split('-')
    return { label: `${Number(month)}/${Number(date)}`, count: day.count, pct: 0 }
  })
  const max = Math.max(1, ...days.map((d) => d.count))
  for (const d of days) d.pct = d.count ? Math.max(8, Math.round((d.count / max) * 100)) : 0
  return days
})

const distPct = computed(() => {
  const d = stats.value?.health?.utility_distribution
  if (!d) return { positive: 0, neutral: 0, negative: 0 }
  const total = d.positive + d.neutral + d.negative
  if (!total) return { positive: 0, neutral: 0, negative: 0 }
  return {
    positive: (d.positive / total) * 100,
    neutral: (d.neutral / total) * 100,
    negative: (d.negative / total) * 100,
  }
})

const kindColor = (k: string): string => {
  const colors: Record<string, string> = {
    preference: 'purple',
    paper_summary: 'geekblue',
    long: 'blue',
    short: 'cyan',
    working: 'default',
    episodic: 'magenta',
    procedural: 'volcano',
    reflection: 'gold',
  }
  return colors[k] || 'default'
}

const utilityClass = (u: number): string => {
  if (u > 0.05) return 'kb__metric--up'
  if (u < -0.05) return 'kb__metric--down'
  return 'kb__metric--muted'
}

// utility 范围 [-2, +5] → 百分比定位
const utilityPct = (u: number): number => Math.max(0, Math.min(100, ((u + 2) / 7) * 100))

const eventTone = (event: string): string => {
  if (event === 'feedback_up' || event === 'citation_click' || event === 'keep' || event === 'reflection') return 'up'
  if (event === 'feedback_down' || event === 'prune') return 'down'
  return 'neutral'
}

const eventIcon = (event: string): string => {
  const icons: Record<string, string> = {
    feedback_up: '＋',
    feedback_down: '－',
    citation_click: '⚓',
    engaged_read: '📖',
    access: '⚡',
    keep: '✓',
    merge: '⛙',
    supersede: '⇄',
    link: '🔗',
    reflection: '✦',
    prune: '✂',
    decay: '↓',
    compress: '⇓',
    write: '✎',
  }
  return icons[event] || '•'
}

const eventLabel = (event: string): string => {
  const labels: Record<string, string> = {
    feedback_up: '正向反馈',
    feedback_down: '负向反馈',
    citation_click: '引用点击',
    engaged_read: '深度阅读',
    access: '检索命中',
    keep: '周期审查保留',
    merge: '合并去重',
    supersede: '更新取代',
    link: '建立关联',
    reflection: '反思提炼',
    prune: '周期审查删除',
    decay: '衰减',
    compress: '压缩',
    write: '写入',
  }
  return labels[event] || event
}

const loadStats = async () => {
  const requestSeq = ++statsRequestSeq
  try {
    const res = await getKnowledgeStats()
    if (requestSeq === statsRequestSeq) stats.value = res
  } catch {
    if (requestSeq === statsRequestSeq) stats.value = null
  }
}

const loadMemories = async () => {
  const requestSeq = ++memoriesRequestSeq
  loadingMemories.value = true
  try {
    const res = await getKnowledgeMemories({
      scope: scope.value,
      paper_id: scope.value === 'paper' ? paperId.value : undefined,
      kind: kind.value || undefined,
      sort: sort.value,
      limit: 100,
    })
    if (requestSeq === memoriesRequestSeq) memories.value = res.items || []
  } catch {
    if (requestSeq === memoriesRequestSeq) memories.value = []
  } finally {
    if (requestSeq === memoriesRequestSeq) loadingMemories.value = false
  }
}

const onScopeChange = () => {
  paperId.value = undefined
  // The paper dropdown options come from a 500-row memories query — only pay
  // for it when the user actually switches to the paper scope.
  if (scope.value === 'paper' && !paperOptionsLoaded) loadPaperOptions()
  loadMemories()
}

const filterPaperOption = (input: string, option: { label?: string }) =>
  String(option?.label || '').toLowerCase().includes(input.toLowerCase())

let paperOptionsLoaded = false
const loadPaperOptions = async () => {
  const requestSeq = ++paperOptionsRequestSeq
  paperOptionsLoaded = true
  try {
    const res = await getKnowledgeMemories({ scope: 'paper', sort: 'recency', limit: 500 })
    if (requestSeq !== paperOptionsRequestSeq) return
    const seen = new Map<number, string>()
    for (const m of res.items || []) {
      if (m.paper_id && !seen.has(m.paper_id)) {
        seen.set(m.paper_id, m.paper_title || `论文 #${m.paper_id}`)
      }
    }
    paperOptions.value = [...seen.entries()].map(([id, title]) => ({ id, title }))
  } catch {
    if (requestSeq === paperOptionsRequestSeq) {
      paperOptions.value = []
      paperOptionsLoaded = false
    }
  }
}

const loadEvolution = async () => {
  const requestSeq = ++evolutionRequestSeq
  loadingEvolution.value = true
  try {
    const res = await getKnowledgeEvolution(50)
    if (requestSeq === evolutionRequestSeq) evolution.value = res.items || []
  } catch {
    if (requestSeq === evolutionRequestSeq) evolution.value = []
  } finally {
    if (requestSeq === evolutionRequestSeq) loadingEvolution.value = false
  }
}

const openTraces = async () => {
  const requestSeq = ++traceRequestSeq
  tracesOpen.value = true
  activeTrace.value = null
  activeTraceEvents.value = []
  loadingTraces.value = true
  try {
    const res = await getTraceSessions(100)
    if (requestSeq === traceRequestSeq) traceSessions.value = res.sessions || []
  } catch {
    if (requestSeq === traceRequestSeq) traceSessions.value = []
  } finally {
    if (requestSeq === traceRequestSeq) loadingTraces.value = false
  }
}

const openTrace = async (sessionId: string) => {
  const requestSeq = ++traceRequestSeq
  activeTrace.value = sessionId
  activeTraceEvents.value = []
  loadingTraces.value = true
  try {
    const res = await getTraceEvents(sessionId, 500)
    if (requestSeq === traceRequestSeq) activeTraceEvents.value = res.events || []
  } catch {
    if (requestSeq === traceRequestSeq) activeTraceEvents.value = []
  } finally {
    if (requestSeq === traceRequestSeq) loadingTraces.value = false
  }
}

const closeTrace = () => {
  traceRequestSeq += 1
  activeTrace.value = null
  activeTraceEvents.value = []
  loadingTraces.value = false
}
watch(tracesOpen, (open) => {
  if (!open) closeTrace()
})
onBeforeUnmount(() => {
  memoriesRequestSeq += 1
  evolutionRequestSeq += 1
  statsRequestSeq += 1
  traceRequestSeq += 1
  paperOptionsRequestSeq += 1
})

const payloadPreview = (payload: Record<string, unknown>): string => {
  try {
    const s = JSON.stringify(payload, null, 1)
    return s.length > 600 ? s.slice(0, 600) + '…' : s
  } catch {
    return String(payload)
  }
}

onMounted(() => {
  // Fired in parallel (no awaits) so the three panels fill independently.
  loadStats()
  loadMemories()
  loadEvolution()
})
</script>

<style scoped>
.kb {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 1440px;
  margin: 0 auto;
  width: 100%;
}

/* ---- 统计卡片 ---- */
.kb__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}
.kb__stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg, 14px);
  padding: 18px 20px;
  box-shadow: 0 2px 12px rgba(31, 27, 75, 0.04);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
  position: relative;
  overflow: hidden;
}
.kb__stat-card::after {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: linear-gradient(180deg, var(--pg-accent), var(--pg-primary));
  opacity: 0.75;
}
.kb__stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(31, 27, 75, 0.09);
}
.kb__stat-icon {
  flex: 0 0 auto;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 19px;
  background: var(--pg-primary-soft);
  border: 1px solid #dfe3ff;
  box-shadow: 0 4px 10px rgba(99, 102, 241, 0.1);
}
.kb__stat-card--total .kb__stat-icon { color: #4f46e5; }
.kb__stat-card--access .kb__stat-icon { color: #0891b2; background: #ecfeff; border-color: #cffafe; }
.kb__stat-card--utility .kb__stat-icon { color: #059669; background: #ecfdf5; border-color: #d1fae5; }
.kb__stat-card--evolution .kb__stat-icon { color: #7c3aed; background: #f5f3ff; border-color: #ede9fe; }
.kb__stat-body {
  min-width: 0;
}
.kb__stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--pg-text-heading);
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}
.kb__stat-label {
  margin-top: 2px;
  font-size: 12px;
  color: var(--pg-text-tertiary);
}

/* ---- 健康面板 ---- */
.kb__health {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
}
.kb__health-block {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg, 14px);
  padding: 16px 20px;
  box-shadow: 0 2px 12px rgba(31, 27, 75, 0.04);
}
.kb__health-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pg-text-heading);
  margin-bottom: 12px;
}
.kb__health-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
}
.kb__health-dot--signal { background: var(--pg-accent); box-shadow: 0 0 6px rgba(99, 102, 241, 0.5); }
.kb__health-dot--dist { background: #059669; box-shadow: 0 0 6px rgba(5, 150, 105, 0.4); }
.kb__health-dot--activity { background: #7c3aed; box-shadow: 0 0 6px rgba(124, 58, 237, 0.4); }
.kb__health-signals {
  display: flex;
  gap: 22px;
  flex-wrap: wrap;
}
.kb__signal {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kb__signal-num {
  font-size: 20px;
  font-weight: 700;
  color: var(--pg-text-heading);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.kb__signal-label {
  font-size: 11px;
  color: var(--pg-text-tertiary);
}
.kb__health-pos { color: #16a34a; }
.kb__health-neg { color: #dc2626; }
.kb__health-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--pg-text);
}
.kb__activity-chip {
  background: var(--pg-primary-soft);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11.5px;
  color: var(--pg-text);
}
.kb__activity-chip b {
  color: var(--pg-primary);
  font-variant-numeric: tabular-nums;
}
.kb__health-none {
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.kb__health-hint {
  margin-top: 10px;
  font-size: 11px;
  color: var(--pg-text-tertiary);
  line-height: 1.5;
}
.kb__dist-bar {
  display: flex;
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--pg-primary-soft);
}
.kb__dist-seg {
  height: 100%;
  transition: width 0.4s ease;
}
.kb__dist-seg--pos { background: linear-gradient(90deg, #34d399, #059669); }
.kb__dist-seg--neutral { background: #d4d4d8; }
.kb__dist-seg--neg { background: linear-gradient(90deg, #f87171, #dc2626); }
.kb__dist-legend {
  display: flex;
  gap: 14px;
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--pg-text-tertiary);
  flex-wrap: wrap;
}
.kb__dist-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-variant-numeric: tabular-nums;
}
.kb__dist-key {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  display: inline-block;
}
.kb__dist-key--pos { background: #059669; }
.kb__dist-key--neutral { background: #d4d4d8; }
.kb__dist-key--neg { background: #dc2626; }

/* ---- 主区 ---- */
.kb__main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1024px) {
  .kb__main { grid-template-columns: 1fr; }
}
.kb__panel {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg, 14px);
  padding: 18px 20px;
  box-shadow: 0 2px 12px rgba(31, 27, 75, 0.04);
}
.kb__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.kb__panel-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--pg-text-heading);
  display: flex;
  align-items: center;
  gap: 8px;
}
.kb__panel-count {
  font-size: 11px;
  font-weight: 600;
  color: var(--pg-primary);
  background: var(--pg-primary-soft);
  border-radius: 999px;
  padding: 1px 8px;
  font-variant-numeric: tabular-nums;
}
.kb__filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.kb__filter {
  min-width: 110px;
}
.kb__filter--paper {
  min-width: 220px;
  max-width: 320px;
}

/* ---- 空状态 ---- */
.kb__empty {
  padding: 40px 12px;
  text-align: center;
  color: var(--pg-text-tertiary);
  font-size: 13px;
  line-height: 1.8;
}
.kb__empty-icon {
  font-size: 28px;
  margin-bottom: 6px;
}
.kb__empty-sub {
  font-size: 12px;
  opacity: 0.8;
}

/* ---- 记忆卡片 ---- */
.kb__mem {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius);
  padding: 14px 16px;
  margin-bottom: 10px;
  background: #fff;
  transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
}
.kb__mem:hover {
  box-shadow: 0 6px 20px rgba(31, 27, 75, 0.08);
  transform: translateY(-1px);
  border-color: #d9defc;
}
.kb__mem--reflection {
  background: linear-gradient(135deg, #fffdf5 0%, #fffbeb 100%);
  border-color: #fde68a;
}
.kb__mem-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
}
.kb__tag {
  margin-inline-end: 0;
  font-weight: 600;
}
.kb__mem-scope {
  font-size: 11px;
  color: var(--pg-text-tertiary);
  background: var(--pg-primary-soft);
  border-radius: 999px;
  padding: 1px 8px;
}
.kb__mem-scope--link {
  cursor: pointer;
  text-decoration: none;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  vertical-align: bottom;
  color: var(--pg-accent);
}
.kb__mem-scope--link:hover {
  text-decoration: underline;
}
.kb__mem-time {
  margin-left: auto;
  font-size: 11px;
  color: var(--pg-text-tertiary);
  flex: 0 0 auto;
}
.kb__mem-content {
  font-size: 13px;
  line-height: 1.75;
  color: var(--pg-text);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ---- 指标仪表 ---- */
.kb__mem-metrics {
  display: flex;
  gap: 18px;
  margin-top: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.kb__gauge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.kb__gauge-label {
  font-size: 11.5px;
  color: var(--pg-text-tertiary);
}
.kb__gauge-track {
  position: relative;
  width: 72px;
  height: 6px;
  border-radius: 999px;
  background: var(--pg-primary-soft);
  display: inline-block;
}
.kb__gauge-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 999px;
  transition: width 0.3s ease;
}
.kb__gauge-fill--imp {
  background: linear-gradient(90deg, var(--pg-accent), var(--pg-primary));
}
.kb__gauge-track--utility {
  background: linear-gradient(90deg, #fee2e2 0%, #f4f4f5 35%, #f4f4f5 45%, #dcfce7 100%);
}
.kb__gauge-zero {
  position: absolute;
  left: 28.57%; /* 0 在 [-2, +5] 区间中的位置 */
  top: -2px;
  bottom: -2px;
  width: 1.5px;
  background: #a1a1aa;
  border-radius: 2px;
}
.kb__gauge-marker {
  position: absolute;
  top: 50%;
  width: 10px;
  height: 10px;
  border-radius: 999px;
  transform: translate(-50%, -50%);
  border: 2px solid #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
  transition: left 0.3s ease;
}
.kb__gauge-num {
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  color: var(--pg-text);
  min-width: 34px;
}
.kb__metric {
  font-size: 11.5px;
  color: var(--pg-text-tertiary);
}
.kb__metric b {
  font-variant-numeric: tabular-nums;
}
.kb__metric--up.kb__gauge-marker,
.kb__gauge-marker.kb__metric--up { background: #16a34a; }
.kb__metric--down.kb__gauge-marker,
.kb__gauge-marker.kb__metric--down { background: #dc2626; }
.kb__metric--muted.kb__gauge-marker,
.kb__gauge-marker.kb__metric--muted { background: #a1a1aa; }
.kb__metric--up b, b.kb__metric--up { color: #16a34a; }
.kb__metric--down b, b.kb__metric--down { color: #dc2626; }
.kb__metric--muted b, b.kb__metric--muted { color: var(--pg-text-tertiary); font-weight: 500; }

/* ---- 进化时间线 ---- */
.kb__timeline {
  display: flex;
  flex-direction: column;
  position: relative;
}
.kb__evo {
  display: flex;
  gap: 12px;
  padding: 9px 0;
  position: relative;
}
.kb__evo:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 13px;
  top: 30px;
  bottom: -4px;
  width: 1.5px;
  background: linear-gradient(180deg, var(--pg-border), transparent);
}
.kb__evo-badge {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  font-size: 13px;
  border: 1px solid var(--pg-border);
  background: #fff;
  position: relative;
  z-index: 1;
}
.kb__evo-badge--up {
  color: #059669;
  background: #ecfdf5;
  border-color: #a7f3d0;
}
.kb__evo-badge--down {
  color: #dc2626;
  background: #fef2f2;
  border-color: #fecaca;
}
.kb__evo-badge--neutral {
  color: var(--pg-accent);
  background: var(--pg-primary-soft);
  border-color: #dfe3ff;
}
.kb__evo-body {
  min-width: 0;
  flex: 1;
  padding-top: 3px;
}
.kb__evo-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.kb__evo-event {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--pg-text);
}
.kb__evo-delta {
  font-size: 10.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  border-radius: 999px;
  padding: 0 7px;
  line-height: 18px;
}
.kb__evo-delta--up {
  color: #059669;
  background: #ecfdf5;
}
.kb__evo-delta--down {
  color: #dc2626;
  background: #fef2f2;
}
.kb__evo-time {
  margin-left: auto;
  font-size: 11px;
  color: var(--pg-text-tertiary);
  flex: 0 0 auto;
}
.kb__evo-reason {
  margin-top: 3px;
  font-size: 12px;
  color: var(--pg-text-tertiary);
  line-height: 1.6;
  word-break: break-word;
}

/* 趋势条 + trace 抽屉 */
.kb__trend {
  display: flex;
  gap: 6px;
  align-items: flex-end;
  height: 56px;
  margin-top: 10px;
}
.kb__trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  height: 100%;
  justify-content: flex-end;
}
.kb__trend-bar {
  width: 100%;
  border-radius: 4px 4px 0 0;
  background: linear-gradient(180deg, var(--pg-accent), var(--pg-primary));
  min-height: 0;
  transition: height 0.3s ease;
}
.kb__trend-label {
  font-size: 10px;
  color: var(--pg-text-tertiary);
}
.kb__traces {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.kb__trace-row {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius);
  padding: 10px 12px;
  cursor: pointer;
  transition: box-shadow 0.15s ease;
}
.kb__trace-row:hover {
  box-shadow: 0 4px 12px rgba(31, 27, 75, 0.08);
}
.kb__trace-row-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.kb__trace-agent {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--pg-text);
}
.kb__trace-meta {
  margin-top: 3px;
  font-size: 11px;
  color: var(--pg-text-tertiary);
}
.kb__trace-time {
  font-size: 11px;
  color: var(--pg-text-tertiary);
}
.kb__trace-event {
  border-bottom: 1px dashed var(--pg-border);
  padding: 8px 0;
}
.kb__trace-event-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.kb__trace-event-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--pg-accent);
}
.kb__trace-payload {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--pg-text-tertiary);
  background: var(--pg-primary-soft);
  border-radius: 6px;
  padding: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow: auto;
}
</style>
