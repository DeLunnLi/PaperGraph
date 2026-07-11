<script setup lang="ts">
interface SearchStep {
  name: string
  label: string
  status: 'running' | 'done' | 'error'
  detail?: string
}
defineProps<{
  steps: SearchStep[]
  subQueries?: string[]
  loading?: boolean
}>()
const activeKey = ref<string[]>(['trace'])
watchEffect(() => {
  // Auto-expand while loading, auto-collapse when done
})
</script>

<template>
  <div v-if="steps.length > 0 || loading" class="search-trace">
    <a-collapse v-model:activeKey="activeKey" ghost class="trace-collapse">
      <a-collapse-panel key="trace">
        <template #header>
          <span class="trace-header">
            <span class="trace-header__icon" :class="{ 'trace-header__icon--spin': loading }">
              {{ loading ? '⟳' : '✓' }}
            </span>
            <span class="trace-header__label">{{ loading ? '搜索进行中' : '搜索路径' }}</span>
            <span v-if="subQueries?.length" class="trace-header__count">{{ subQueries.length }} 个子问题</span>
          </span>
        </template>

        <!-- Sub-queries (deep search) -->
        <div v-if="subQueries?.length" class="trace-subqueries">
          <div class="trace-subqueries__title">分解的子问题：</div>
          <div class="trace-subqueries__list">
            <span v-for="(sq, i) in subQueries" :key="i" class="trace-subquery-chip">
              {{ sq }}
            </span>
          </div>
        </div>

        <!-- Step timeline -->
        <div class="trace-steps">
          <div
            v-for="(step, i) in steps"
            :key="i"
            class="trace-step"
            :class="'trace-step--' + step.status"
          >
            <span class="trace-step__indicator">
              <span v-if="step.status === 'running'" class="trace-step__dot trace-step__dot--running"></span>
              <span v-else-if="step.status === 'done'" class="trace-step__dot trace-step__dot--done">✓</span>
              <span v-else class="trace-step__dot trace-step__dot--error">✗</span>
            </span>
            <span v-if="i < steps.length - 1" class="trace-step__line" :class="{ 'trace-step__line--done': step.status === 'done' }"></span>
            <div class="trace-step__content">
              <span class="trace-step__label">{{ step.label }}</span>
              <span v-if="step.detail" class="trace-step__detail">{{ step.detail }}</span>
            </div>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<script lang="ts">
import { ref, watchEffect } from 'vue'
</script>

<style scoped>
.search-trace {
  margin-bottom: 10px;
}
.trace-collapse :deep(.ant-collapse-header) {
  padding: 6px 0 !important;
  align-items: center;
}
.trace-collapse :deep(.ant-collapse-content-box) {
  padding: 0 0 8px !important;
}
.trace-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.trace-header__icon {
  font-size: 14px;
  color: var(--pg-text-tertiary);
}
.trace-header__icon--spin {
  animation: pg-spin 1s linear infinite;
  color: var(--pg-primary);
}
@keyframes pg-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.trace-header__label {
  font-weight: 500;
  color: var(--pg-text-secondary);
}
.trace-header__count {
  font-size: 12px;
  color: var(--pg-primary);
  background: var(--pg-primary-soft);
  padding: 1px 8px;
  border-radius: var(--pg-radius-pill);
}

.trace-subqueries {
  margin-bottom: 10px;
}
.trace-subqueries__title {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 6px;
}
.trace-subqueries__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.trace-subquery-chip {
  font-size: 12px;
  padding: 3px 10px;
  background: var(--pg-primary-soft);
  color: var(--pg-primary-hover);
  border-radius: var(--pg-radius-pill);
  line-height: 1.4;
}

.trace-steps {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.trace-step {
  display: flex;
  align-items: flex-start;
  gap: 0;
  position: relative;
  padding-bottom: 10px;
}
.trace-step:last-child {
  padding-bottom: 0;
}
.trace-step__indicator {
  width: 20px;
  flex-shrink: 0;
  display: flex;
  justify-content: center;
  position: relative;
  z-index: 1;
}
.trace-step__dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  flex-shrink: 0;
}
.trace-step__dot--running {
  width: 10px;
  height: 10px;
  border: 2px solid var(--pg-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pg-spin 0.8s linear infinite;
  margin: 4px;
}
.trace-step__dot--done {
  background: var(--pg-primary);
  color: #fff;
}
.trace-step__dot--error {
  background: #ef4444;
  color: #fff;
}
.trace-step__line {
  position: absolute;
  left: 9px;
  top: 18px;
  bottom: -2px;
  width: 2px;
  background: var(--pg-border);
}
.trace-step__line--done {
  background: var(--pg-primary);
  opacity: 0.3;
}
.trace-step__content {
  flex: 1;
  padding-left: 8px;
  padding-top: 0;
  min-width: 0;
}
.trace-step__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--pg-text);
  line-height: 18px;
}
.trace-step--running .trace-step__label {
  color: var(--pg-primary);
}
.trace-step--done .trace-step__label {
  color: var(--pg-text-secondary);
}
.trace-step__detail {
  display: block;
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-top: 2px;
  line-height: 1.4;
}
</style>
