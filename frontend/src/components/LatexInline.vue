<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ensureKatex, renderKatex } from '@/utils/katex'
const props = withDefaults(defineProps<{ text: string; display?: boolean }>(), { text: '', display: false })
// Re-render once katex has finished loading (first math-bearing view only).
const katexReady = ref(false)
onMounted(() => { void ensureKatex().then(() => { katexReady.value = true }) })
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}
const html = computed(() => {
  // depend on katexReady so the computed re-evaluates when katex arrives
  void katexReady.value
  const raw = String(props.text ?? '')
    .replace(/([A-Za-z0-9])\\textsuperscript\{([^}]+)\}/g, '$1$^{${2}}$')
    .replace(/\\textsuperscript\{([^}]+)\}/g, '$^{${1}}$')
  if (!raw) return ''
  const out: string[] = []
  const re = /\$([^$]+)\$/g
  let last = 0; let m: RegExpExecArray | null
  while ((m = re.exec(raw)) !== null) {
    if (m.index > last) out.push(`<span class="latex-inline__txt">${escapeHtml(raw.slice(last, m.index))}</span>`)
    const rendered = renderKatex(m[1].trim(), { displayMode: props.display })
    if (rendered) out.push(rendered)
    else out.push(`<span class="latex-inline__txt">${escapeHtml(m[0])}</span>`)
    last = m.index + m[0].length
  }
  if (last < raw.length) out.push(`<span class="latex-inline__txt">${escapeHtml(raw.slice(last))}</span>`)
  return out.length ? out.join('') : escapeHtml(raw)
})
</script>
<template><span class="latex-inline" v-html="html" /></template>
<style scoped>
.latex-inline { display: inline; line-height: 1.45; vertical-align: baseline; }
.latex-inline :deep(.katex) { font-size: 0.92em; }
.latex-inline :deep(.katex-display) { margin: 0; }
.latex-inline__txt { white-space: pre-wrap; }
</style>