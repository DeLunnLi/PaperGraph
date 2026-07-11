<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { FilePdfOutlined, SaveOutlined } from '@ant-design/icons-vue'
import LatexInline from '@/components/LatexInline.vue'
import type { Paper } from '@/types'
import { clipTextAvoidBreakingMath } from '@/utils/markdown'
import { resultSourceMeta } from '@/utils/paperSourceMeta'

const props = defineProps<{
  papers: Paper[]
  total: number
}>()

const emit = defineEmits<{
  saveOne: [paper: Paper]
  saveAll: []
}>()

const sortBy = ref('default')

const sortedPapers = computed(() => {
  if (sortBy.value === 'default') return props.papers
  const arr = [...props.papers]
  if (sortBy.value === 'year') {
    arr.sort((a, b) => (b.year ?? 0) - (a.year ?? 0))
  } else if (sortBy.value === 'citations') {
    arr.sort((a, b) => (b.citations ?? 0) - (a.citations ?? 0))
  }
  return arr
})

function onSortChange() {
  // sortedPapers is computed, re-renders automatically
}

const displayCount = ref(8)
const visiblePapers = computed(() => sortedPapers.value.slice(0, displayCount.value))
const hasMore = computed(() => displayCount.value < sortedPapers.value.length)
function loadMore() {
  displayCount.value += 8
}
watch(() => props.papers, () => { displayCount.value = 8 })

function abstractPreview(abs: string, length = 280): string {
  return clipTextAvoidBreakingMath(abs, length)
}
</script>

<template>
  <div v-if="papers.length > 0" class="papers-list">
    <div class="papers-header">
      <span>为你找到 {{ total }} 篇相关论文：</span>
      <div class="papers-header__right">
        <a-select v-model:value="sortBy" size="small" style="width: 110px" @change="onSortChange">
          <a-select-option value="default">默认排序</a-select-option>
          <a-select-option value="year">按年份</a-select-option>
          <a-select-option value="citations">按引用数</a-select-option>
        </a-select>
        <a-button type="link" size="small" @click="emit('saveAll')">
          <SaveOutlined /> 保存全部
        </a-button>
      </div>
    </div>
    <div v-for="(paper, pIndex) in visiblePapers" :key="pIndex" class="paper-item">
      <div class="paper-number">{{ pIndex + 1 }}.</div>
      <div class="paper-content">
        <div class="paper-title-line">
          <a v-if="paper.source_url" :href="paper.source_url" target="_blank" class="paper-title-link">
            <LatexInline :text="paper.title" />
          </a>
          <span v-else class="paper-title-text">
            <LatexInline :text="paper.title" />
          </span>
          <span class="paper-badges">
            <a-tag :color="resultSourceMeta(paper.source).color" size="small">
              {{ resultSourceMeta(paper.source).label }}
            </a-tag>
          </span>
        </div>
        <div class="paper-meta">
          <span v-if="paper.authors?.length" class="paper-meta__authors">
            {{ paper.authors.map(a => a.name).slice(0, 5).join(', ') }}<span v-if="paper.authors.length > 5"> 等</span>
          </span>
          <span class="paper-meta__dot">·</span>
          <LatexInline
            class="paper-meta__venue"
            :text="`${paper.journal || paper.venue || (paper.source ? resultSourceMeta(paper.source).label + ' 预印本' : '—')} · ${paper.year ?? '—'}`"
          />
        </div>
        <div v-if="paper.abstract" class="paper-abstract">
          <LatexInline :text="abstractPreview(paper.abstract, 280)" />
        </div>
        <div class="paper-footer">
          <span class="paper-tags">
            <a-tag v-if="paper.arxiv_id" color="orange" size="small">arXiv {{ paper.arxiv_id }}</a-tag>
            <a-tag v-if="paper.citations" color="cyan" size="small">{{ paper.citations }} 引用</a-tag>
          </span>
          <span class="paper-actions">
            <a v-if="paper.pdf_url" :href="paper.pdf_url" target="_blank">
              <a-button type="link" size="small">
                <FilePdfOutlined /> PDF
              </a-button>
            </a>
            <a v-else-if="paper.doi" :href="'https://doi.org/' + paper.doi" target="_blank">
              <a-button type="link" size="small">DOI</a-button>
            </a>
            <a-button type="link" size="small" @click="emit('saveOne', paper)">
              <SaveOutlined /> 保存
            </a-button>
          </span>
        </div>
      </div>
    </div>
    <div v-if="hasMore" class="papers-loadmore">
      <a-button type="link" @click="loadMore">
        加载更多（剩余 {{ sortedPapers.length - displayCount }} 篇）
      </a-button>
    </div>
  </div>
</template>

<style scoped>
.papers-list {
  background: var(--pg-surface);
  border-radius: var(--pg-radius-lg);
  padding: 8px 18px;
  border: 1px solid var(--pg-border);
  box-shadow: var(--pg-shadow-xs);
  margin-top: 12px;
}
.papers-loadmore {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
  border-top: 1px solid var(--pg-border-soft);
  margin-top: 8px;
}
.papers-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--pg-border-soft);
  font-size: 13.5px;
  color: var(--pg-text-secondary);
  margin-bottom: 4px;
  padding: 12px 0;
}
.papers-header__right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.paper-item {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  border-bottom: 1px solid var(--pg-border-soft);
}
.paper-item:last-child {
  border-bottom: none;
}
.paper-number {
  font-weight: 600;
  color: var(--pg-primary);
  font-size: 14px;
  flex-shrink: 0;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: var(--pg-radius-pill);
  background: var(--pg-primary-soft);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.paper-content {
  flex: 1;
  min-width: 0;
}
.paper-title-line {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.paper-title-link {
  font-weight: 650;
  font-size: 15.5px;
  color: var(--pg-text);
  line-height: 1.4;
}
.paper-title-text {
  font-weight: 650;
  font-size: 15.5px;
  color: var(--pg-text);
  line-height: 1.4;
}
.paper-title-link:hover {
  color: var(--pg-primary-hover);
}
.paper-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--pg-text-tertiary);
  margin-bottom: 6px;
  line-height: 1.5;
}
.paper-meta__authors {
  color: var(--pg-text-secondary);
}
.paper-meta__dot {
  opacity: 0.6;
}
.paper-abstract {
  font-size: 13.5px;
  color: var(--pg-text-secondary);
  line-height: 1.62;
  margin-bottom: 6px;
}
.paper-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.paper-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.paper-tags :deep(.ant-tag),
.paper-badges :deep(.ant-tag) {
  margin-inline-end: 0 !important;
  border-radius: var(--pg-radius-pill);
  font-size: 12px;
}
.paper-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
@media (max-width: 768px) {
  .paper-title-line {
    flex-direction: column;
    gap: 8px;
  }
  .paper-footer {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
