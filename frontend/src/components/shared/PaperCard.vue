<script setup lang="ts">
import { ref, computed } from 'vue'
import LatexInline from '@/components/LatexInline.vue'
import type { Paper } from '@/types'
const props = withDefaults(defineProps<{
  paper: Paper
  index?: number
  abstractPreviewChars?: number
  showTags?: boolean
  showArxivId?: boolean
  tagColor?: string
  tagLabel?: string
}>(), {
  index: 0,
  abstractPreviewChars: 320,
  showTags: true,
  showArxivId: true,
  tagColor: 'blue',
  tagLabel: '',
})
const emit = defineEmits<{
  click: [paper: Paper]
}>()
const expanded = ref(false)
function needsToggle(text?: string): boolean {
  return (text?.length ?? 0) > props.abstractPreviewChars
}
function preview(text?: string): string {
  const t = (text ?? '').trim()
  return t.length <= props.abstractPreviewChars ? t : t.slice(0, props.abstractPreviewChars) + '…'
}
function authors(paper: Paper): string {
  return (paper.authors || []).map((a) => a.name).filter(Boolean).join(', ') || '—'
}
const metaLine = computed(() => {
  const parts: string[] = []
  const j = String(props.paper.journal || props.paper.venue || '').trim()
  if (j) parts.push(j)
  if (props.paper.year != null) parts.push(String(props.paper.year))
  return parts.join(' · ')
})
</script>
<template>
  <a-list-item class="paper-card">
    <div class="paper-card__body">
      <div class="paper-card__head">
        <span v-if="index > 0" class="paper-card__index">{{ index }}</span>
        <div class="paper-card__main">
          <div class="paper-card__title-row">
            <a v-if="paper.source_url" class="paper-card__title" :href="paper.source_url" target="_blank"
              rel="noopener noreferrer" @click="emit('click', paper)">
              <LatexInline :text="paper.title" />
            </a>
            <LatexInline v-else class="paper-card__title" :text="paper.title" />
            <div v-if="showTags" class="paper-card__tags">
              <a-tag v-if="tagLabel" :color="tagColor" class="paper-card__tag">{{ tagLabel }}</a-tag>
              <a-tag v-if="showArxivId && paper.arxiv_id" class="paper-card__tag paper-card__tag--arxiv">arXiv {{ paper.arxiv_id }}</a-tag>
              <slot name="tags" />
            </div>
          </div>
          <div class="paper-card__meta">
            <span class="paper-card__authors">{{ authors(paper) }}</span>
            <span v-if="metaLine" class="paper-card__dot">·</span>
            <span v-if="metaLine" class="paper-card__venue">{{ metaLine }}</span>
          </div>
        </div>
      </div>
      <div v-if="paper.abstract" class="paper-card__abstract">
        <LatexInline :text="expanded ? (paper.abstract ?? '') : preview(paper.abstract)" />
        <a-button v-if="needsToggle(paper.abstract)" type="link" size="small"
          class="paper-card__toggle" @click.stop="expanded = !expanded">
          {{ expanded ? '收起' : '展开全文' }}
        </a-button>
      </div>
      <slot name="description" />
      <div class="paper-card__footer">
        <div class="paper-card__footer-left">
          <slot name="actions" />
        </div>
        <a v-if="paper.pdf_url" :href="paper.pdf_url" target="_blank" @click="emit('click', paper)">
          <a-button type="link" size="small" class="paper-card__pdf">查看 PDF</a-button>
        </a>
      </div>
    </div>
  </a-list-item>
</template>
<style scoped>
.paper-card {
  display: block;
  padding: 0 !important;
  border-bottom: none !important;
}
.paper-card__body {
  padding: 18px 20px;
  border: 1px solid var(--pg-divider);
  border-radius: var(--pg-radius-lg);
  background: var(--pg-glass-bg-strong);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.paper-card__body:hover {
  border-color: var(--pg-border);
  box-shadow: var(--pg-shadow-sm);
}
.paper-card__head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.paper-card__index {
  flex-shrink: 0;
  min-width: 20px;
  color: var(--pg-text-tertiary);
  font-family: var(--pg-font-serif);
  font-size: 14px;
  font-weight: 600;
  text-align: right;
  margin-top: 3px;
  font-variant-numeric: tabular-nums;
}
.paper-card__main {
  flex: 1;
  min-width: 0;
}
.paper-card__title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.paper-card__title {
  font-family: var(--pg-font-serif);
  font-weight: 600;
  font-size: 16.5px;
  line-height: 1.4;
  color: var(--pg-text-heading);
  word-break: break-word;
  text-decoration: none;
  letter-spacing: 0.005em;
}
a.paper-card__title:hover {
  color: var(--pg-accent);
}
.paper-card__tags {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.paper-card__tag {
  margin-inline-end: 0 !important;
  border-radius: var(--pg-radius-pill);
  font-size: 12px;
}
.paper-card__tag--arxiv {
  background: #fff5e9 !important;
  border-color: #ffd9a8 !important;
  color: #b25b00 !important;
}
.paper-card__meta {
  margin-top: 8px;
  font-size: 13px;
  color: var(--pg-text-tertiary);
  line-height: 1.5;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.paper-card__authors {
  color: var(--pg-text-secondary);
  font-variant-numeric: tabular-nums;
}
.paper-card__dot {
  opacity: 0.4;
}
.paper-card__venue {
  font-style: normal;
  font-variant-numeric: tabular-nums;
}
.paper-card__abstract {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--pg-divider);
  font-size: 13.5px;
  line-height: 1.68;
  color: var(--pg-text-secondary);
  word-break: break-word;
}
.paper-card__toggle {
  padding: 0 0 0 4px;
  height: auto;
  font-size: 12.5px;
  vertical-align: baseline;
}
.paper-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
}
.paper-card__footer-left {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
}
.paper-card__pdf {
  font-size: 13px;
}
</style>
