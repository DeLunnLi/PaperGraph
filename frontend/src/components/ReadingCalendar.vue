<template>
  <div class="cal">
    <div class="cal__head">
      <button class="cal__nav" type="button" title="上个月" @click="prevMonth">‹</button>
      <span class="cal__month">{{ monthLabel }}</span>
      <button class="cal__nav" type="button" title="下个月" @click="nextMonth">›</button>
    </div>
    <div class="cal__body" :class="{ 'cal__body--syncing': syncing }">
      <div v-for="w in weekDays" :key="w" class="cal__dow">{{ w }}</div>
      <div
        v-for="d in monthCells"
        :key="d.key"
        class="cal__day"
        :class="{
          'cal__day--empty': d.isPad,
          'cal__day--today': !d.isPad && d.isToday,
          [`cal__day--lv${d.level}`]: !d.isPad,
        }"
        :title="d.isPad ? '' : `${d.date} · ${Math.round(d.seconds / 60)} 分钟 · ${d.sessions} 次`"
      >
        <span v-if="!d.isPad" class="cal__day-num">{{ d.day }}</span>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getReadingCalendar } from '@/services/api/papers'
type Level = 0 | 1 | 2 | 3 | 4
type MonthCell =
  | { key: string; isPad: true; date: ''; day: 0; seconds: 0; sessions: 0; level: 0; isToday: false }
  | {
      key: string
      isPad: false
      date: string
      day: number
      seconds: number
      sessions: number
      level: Level
      isToday: boolean
    }
const syncing = ref(false)
const days = ref(366)
const items = ref<{ date: string; seconds: number; sessions: number }[]>([])
const weekDays = ['日', '一', '二', '三', '四', '五', '六']
function pad2(n: number) {
  return String(n).padStart(2, '0')
}
function toDayKey(d: Date) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}
function firstDayOfMonth(y: number, m0: number) {
  const d = new Date(y, m0, 1)
  d.setHours(12, 0, 0, 0)
  return d
}
function daysInMonth(y: number, m0: number) {
  return new Date(y, m0 + 1, 0).getDate()
}
function clampMonth(y: number, m0: number): { y: number; m0: number } {
  let yy = y
  let mm = m0
  while (mm < 0) {
    mm += 12
    yy -= 1
  }
  while (mm > 11) {
    mm -= 12
    yy += 1
  }
  return { y: yy, m0: mm }
}
const mapByDate = computed(() => {
  const m = new Map<string, { seconds: number; sessions: number }>()
  for (const it of items.value || []) {
    if (!it?.date) continue
    m.set(it.date, { seconds: Math.max(0, Number(it.seconds || 0)), sessions: Math.max(0, Number(it.sessions || 0)) })
  }
  return m
})
function levelFromSeconds(sec: number, p90: number): 0 | 1 | 2 | 3 | 4 {
  const s = Math.max(0, sec || 0)
  if (s <= 0) return 0
  const cap = Math.max(10 * 60, p90 || 0)
  const x = Math.min(s, cap) / cap
  if (x <= 0.2) return 1
  if (x <= 0.4) return 2
  if (x <= 0.7) return 3
  return 4
}
const _t0 = new Date()
const monthCursor = ref<{ y: number; m0: number }>({ y: _t0.getFullYear(), m0: _t0.getMonth() })
const monthLabel = computed(() => `${monthCursor.value.y}-${pad2(monthCursor.value.m0 + 1)}`)
const monthCells = computed<MonthCell[]>(() => {
  const { y, m0 } = monthCursor.value
  const first = firstDayOfMonth(y, m0)
  const pad = first.getDay()
  const n = daysInMonth(y, m0)
  const todayKey = toDayKey(new Date())
  const monthValues = Array.from({ length: n }, (_, idx) => {
    const day = idx + 1
    const date = toDayKey(new Date(y, m0, day, 12, 0, 0, 0))
    const v = mapByDate.value.get(date) || { seconds: 0, sessions: 0 }
    return { date, day, seconds: v.seconds, sessions: v.sessions, level: 0 as Level, isToday: date === todayKey }
  })
  const secs = monthValues
    .map((x) => x.seconds)
    .filter((x) => x > 0)
    .sort((a, b) => a - b)
  const p90 = secs.length ? secs[Math.floor(secs.length * 0.9)] : 0
  for (const it of monthValues) it.level = levelFromSeconds(it.seconds, p90)
  const out: MonthCell[] = []
  for (let i = 0; i < pad; i++)
    out.push({ key: `pad-${y}-${m0}-${i}`, isPad: true, date: '', day: 0, seconds: 0, sessions: 0, level: 0, isToday: false })
  for (const it of monthValues) out.push({ key: it.date, isPad: false, ...it })
  const target = 42
  if (out.length < target) {
    const need = target - out.length
    for (let i = 0; i < need; i++) {
      out.push({
        key: `pad2-${y}-${m0}-${i}`,
        isPad: true,
        date: '',
        day: 0,
        seconds: 0,
        sessions: 0,
        level: 0,
        isToday: false,
      })
    }
  }
  return out
})
const prevMonth = () => {
  const { y, m0 } = clampMonth(monthCursor.value.y, monthCursor.value.m0 - 1)
  monthCursor.value = { y, m0 }
}
const nextMonth = () => {
  const { y, m0 } = clampMonth(monthCursor.value.y, monthCursor.value.m0 + 1)
  monthCursor.value = { y, m0 }
}
onMounted(async () => {
  syncing.value = true
  try {
    const r = await getReadingCalendar(days.value)
    if (r?.success) {
      days.value = r.days || days.value
      items.value = Array.isArray(r.items) ? r.items : []
    }
  } catch {
    items.value = []
  } finally {
    syncing.value = false
  }
})
</script>
<style scoped>
.cal {
  padding: 12px 12px 14px;
}
.cal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.cal__month {
  font-size: 12px;
  font-weight: 600;
  color: var(--pg-text);
  letter-spacing: 0.02em;
  font-variant-numeric: tabular-nums;
  flex: 1 1 auto;
  text-align: center;
}
.cal__nav {
  width: 24px;
  height: 22px;
  border-radius: 6px;
  border: 1px solid var(--pg-border);
  background: var(--pg-surface);
  color: var(--pg-text-secondary);
  cursor: pointer;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.cal__nav:hover {
  color: var(--pg-primary);
  border-color: #c7c9f5;
  background: var(--pg-primary-softer);
}
.cal__body {
  display: grid;
  grid-template-columns: repeat(7, 22px);
  grid-auto-rows: 22px;
  gap: 5px;
  align-items: center;
  justify-content: center;
  min-height: calc(22px * 7 + 5px * 6);
}
.cal__body--syncing {
  opacity: 0.6;
  transition: opacity 0.2s ease;
}
.cal__dow {
  width: 22px;
  height: 22px;
  font-size: 10px;
  font-weight: 500;
  line-height: 22px;
  text-align: center;
  color: var(--pg-text-tertiary);
}
.cal__day {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--pg-bg-soft);
}
.cal__day--empty {
  background: transparent;
}
.cal__day-num {
  font-size: 10.5px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--pg-text-secondary);
  user-select: none;
}
.cal__day--lv0 .cal__day-num {
  color: var(--pg-text-tertiary);
}
/* 热力等级:浅靛 → 深靛,高级感克制 */
.cal__day--lv1 {
  background: #eef2ff;
}
.cal__day--lv2 {
  background: #c7d2fe;
}
.cal__day--lv3 {
  background: #818cf8;
}
.cal__day--lv3 .cal__day-num {
  color: var(--pg-text-inverse);
}
.cal__day--lv4 {
  background: var(--pg-primary);
}
.cal__day--lv4 .cal__day-num {
  color: var(--pg-text-inverse);
}
.cal__day--today {
  outline: 1.5px solid var(--pg-primary);
  outline-offset: 1px;
}
</style>
