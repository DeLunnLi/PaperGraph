<template>
  <div class="library-page">
    <a-layout class="library-layout">
      <a-layout-sider width="200" theme="light" class="library-sider">
        <div class="library-sider__title">文献库分类</div>
        <div v-if="storeRoot" class="library-sider__root">根目录：{{ storeRoot }}</div>
        <a-menu
          v-model:openKeys="openKeys"
          :selected-keys="[selectedKey]"
          mode="inline"
          @click="onCategoryClick"
        >
          <a-menu-item key="__all__">全部</a-menu-item>
          <template v-for="f in folders" :key="'row-' + f.category">
            <a-sub-menu v-if="f.children && f.children.length > 0" :key="'sub-' + f.category">
              <template #title>
                <span>{{ f.category }}</span>
                <span class="library-sider__count">（{{ f.count }}）</span>
              </template>
              <a-menu-item v-for="c in f.children" :key="c.category">
                <span class="library-sider__child-label">{{ c.label }}</span>
                <span class="library-sider__count">（{{ c.count }}）</span>
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item v-else :key="f.category">
              <span>{{ f.category }}</span>
              <span class="library-sider__count">（{{ f.count }}）</span>
            </a-menu-item>
          </template>
        </a-menu>
      </a-layout-sider>
      <a-layout-content class="library-content">
        <a-card :bordered="false" class="library-card">
          <template #title>
            <div class="library-card__title">
              <span class="library-card__title-text">我的文献库</span>
              <a-tag color="processing">{{ selectedCategoryLabel }}</a-tag>
            </div>
          </template>
          <template #extra>
            <a-space wrap>
              <a-tag color="blue">共 {{ pagination.total }} 篇</a-tag>
              <a-dropdown @click.stop>
                <a-button size="small" :loading="exporting">
                  <ExportOutlined /> 导出知识
                </a-button>
                <template #overlay>
                  <a-menu @click="onExportKnowledge">
                    <a-menu-item key="all">全部（论文+对话+记忆+图谱）</a-menu-item>
                    <a-menu-divider />
                    <a-menu-item key="papers">仅论文库</a-menu-item>
                    <a-menu-item key="reader">仅阅读对话</a-menu-item>
                    <a-menu-item key="memory">仅记忆</a-menu-item>
                    <a-menu-item key="graph">仅知识图谱</a-menu-item>
                  </a-menu>
                </template>
              </a-dropdown>
              <a-button size="small" :disabled="selectedRowKeys.length === 0" @click="exportBibTeX">
                BibTeX
              </a-button>
              <a-button size="small" danger :disabled="selectedRowKeys.length === 0" :loading="batchDeleting" @click="batchDelete">
                删除 ({{ selectedRowKeys.length }})
              </a-button>
              <a-button :loading="loading" @click="load">刷新</a-button>
            </a-space>
          </template>
          <div class="library-toolbar">
            <a-input-search
              v-model:value="searchQuery"
              placeholder="搜索标题、作者、DOI…"
              allow-clear
              :loading="loading"
              class="library-toolbar__search"
              @search="onSearchSubmit"
            />
          </div>
          <a-table
            class="library-table"
            :columns="columns"
            :data-source="papers"
            :loading="loading"
            :pagination="pagination"
            :row-selection="{ selectedRowKeys, onChange: onSelectChange }"
            @change="onTableChange"
            :custom-row="customRow"
            :row-key="(r: Paper) => (r.id != null ? String(r.id) : `${r.title}-${r.doi || ''}`)"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'title'">
                <span class="lib-title-wrap">
                  <span class="lib-title-text">{{ record.title }}</span>
                  <a
                    v-if="record.source_url"
                    class="lib-title-ext"
                    :href="record.source_url"
                    target="_blank"
                    rel="noopener"
                    @click.stop
                  >原文 ↗</a>
                </span>
              </template>
              <template v-if="column.key === 'authors'">
                <span class="lib-authors-cell">{{ formatAuthorsEtAl(record.authors) }}</span>
              </template>
              <template v-if="column.key === 'year'">
                <span class="lib-year">{{ record.year ?? '—' }}</span>
              </template>
              <template v-if="column.key === 'category'">
                {{ record.category || '—' }}
              </template>
              <template v-if="column.key === 'journal_or_source'">
                <span v-if="record.journal && !String(record.journal).startsWith('arXiv:')" class="lib-venue">
                  <a-tag v-if="record.venue_type === 'conference'" color="purple" size="small" style="margin-right:4px">会议</a-tag>
                  <a-tag v-else-if="record.venue_type === 'journal'" color="cyan" size="small" style="margin-right:4px">期刊</a-tag>
                  {{ record.journal }}
                </span>
                <a-tag v-else>{{ record.source }}</a-tag>
              </template>
              <template v-if="column.key === 'actions'">
                <span class="lib-cell-actions" @click.stop>
                  <a-button type="link" size="small" @click="goReader(record)">阅读</a-button>
                  <a-button type="link" danger size="small" @click="onDelete(record)">删除</a-button>
                </span>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-layout-content>
    </a-layout>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { ExportOutlined } from '@ant-design/icons-vue'
import { getLibrary, getLibraryCategoryFolders, deletePaper } from '@/services/api'
import apiClient from '@/services/api/client'
import type { LibraryCategoryFolder, Paper } from '@/types'
const router = useRouter()
const papers = ref<Paper[]>([])
const folders = ref<LibraryCategoryFolder[]>([])
const storeRoot = ref('')
const selectedKey = ref('__all__')
const openKeys = ref<string[]>([])
const loading = ref(false)
const searchQuery = ref('')
const selectedRowKeys = ref<string[]>([])
const batchDeleting = ref(false)
const exporting = ref(false)
const onExportKnowledge = async ({ key }: { key: string }) => {
  exporting.value = true
  try {
    const resp = await apiClient.get(`/api/export/json`, {
      params: { scope: key },
      responseType: 'blob',
      timeout: 60000,
    })
    const blob = new Blob([resp.data], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const disposition = resp.headers['content-disposition'] || ''
    const match = disposition.match(/filename="?([^"]+)"?/)
    a.download = match ? match[1] : `papergraph_export_${key}.json`
    a.click()
    URL.revokeObjectURL(url)
    message.success('知识库已导出')
  } catch (e: unknown) {
    message.error((e as Error).message || '导出失败')
  } finally {
    exporting.value = false
  }
}
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null
const selectedCategoryLabel = computed(() => {
  const sk = selectedKey.value
  if (sk === '__all__') return '全部分类'
  for (const f of folders.value) {
    if (f.category === sk) return f.category
    for (const c of f.children || []) {
      if (c.category === sk) return c.label || c.category
    }
  }
  return sk
})
const pagination = ref({
  total: 0,
  current: 1,
  pageSize: 10,
  showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 条`,
})
const columns = [
  { title: '标题', dataIndex: 'title', key: 'title', width: '28%', align: 'left' as const },
  { title: '作者', key: 'authors', width: '16%', align: 'left' as const },
  { title: '年份', key: 'year', width: '7%', align: 'center' as const, sorter: (a: any, b: any) => (a.year ?? 0) - (b.year ?? 0) },
  { title: '出版', key: 'journal_or_source', width: '14%', align: 'center' as const },
  { title: '领域', key: 'category', width: '24%', align: 'left' as const, ellipsis: true },
  { title: '操作', key: 'actions', width: '10%', align: 'center' as const },
]
function formatAuthorsEtAl(authors: Paper['authors'] | undefined): string {
  const names = (authors || []).map((a) => String(a?.name || '').trim()).filter(Boolean)
  if (names.length === 0) return '—'
  if (names.length <= 3) return names.join(', ')
  return `${names.slice(0, 3).join(', ')} et al.`
}
const loadFolders = async () => {
  try {
    const res = await getLibraryCategoryFolders()
    if (res.success) {
      folders.value = res.folders || []
      storeRoot.value = res.store_root || ''
      openKeys.value = folders.value
        .filter((f) => (f.children?.length ?? 0) > 0)
        .map((f) => `sub-${f.category}`)
    }
  } catch {
  }
}
let _loading = false
const load = async () => {
  if (_loading) return
  _loading = true
  loading.value = true
  try {
    const sk = selectedKey.value
    const cat = sk === '__all__' ? undefined : sk
    const q = searchQuery.value.trim() || undefined
    const ps = pagination.value.pageSize || 10
    const cur = pagination.value.current || 1
    const offset = (cur - 1) * ps
    const res = await getLibrary(ps, {
      offset,
      ...(cat ? { category: cat } : {}),
      ...(q ? { q } : {}),
    })
    if (res.success) {
      papers.value = res.papers ?? []
      pagination.value = { ...pagination.value, total: typeof res.total === 'number' ? res.total : papers.value.length }
    }
  } catch (e: unknown) {
    message.error((e as Error).message || '加载失败')
  } finally {
    loading.value = false
    _loading = false
  }
}
const onTableChange = (pag: { current?: number; pageSize?: number }) => {
  if (pag.current != null) pagination.value.current = pag.current
  if (pag.pageSize != null) pagination.value.pageSize = pag.pageSize
  void load()
}
const onCategoryClick = ({ key }: { key: string }) => {
  selectedKey.value = key
  pagination.value.current = 1
  void load()
}
const onSearchSubmit = () => {
  pagination.value.current = 1
  void load()
}
watch(searchQuery, () => {
  if (searchDebounceTimer != null) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    searchDebounceTimer = null
    pagination.value.current = 1
    void load()
  }, 420)
})
const openReaderUrl = (id: number) => {
  window.open(router.resolve({ path: `/library/read/${id}`, query: { standalone: '1' } }).href, '_blank', 'noopener,noreferrer')
}
const goReader = (record: Paper) => {
  if (record.id == null) { message.warning('无 ID，无法打开阅读页'); return }
  openReaderUrl(record.id)
}
const customRow = (record: Paper) => ({
  onClick: () => { if (record.id != null) openReaderUrl(record.id) },
  style: { cursor: record.id != null ? 'pointer' : 'default' },
})
const onDelete = (record: Paper) => {
  if (record.id == null) {
    message.warning('无 ID，无法删除')
    return
  }
  Modal.confirm({
    title: '确认删除该文献？',
    onOk: async () => {
      try {
        await deletePaper(record.id!)
        message.success('已删除')
        await loadFolders()
        await load()
      } catch (e: unknown) {
        message.error((e as Error).message || '删除失败')
      }
    },
  })
}
const onSelectChange = (keys: string[]) => {
  selectedRowKeys.value = keys
}
const batchDelete = () => {
  if (selectedRowKeys.value.length === 0) return
  Modal.confirm({
    title: `确认删除选中的 ${selectedRowKeys.value.length} 篇文献？`,
    content: '删除后无法恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      batchDeleting.value = true
      let ok = 0
      let fail = 0
      try {
        for (const key of selectedRowKeys.value) {
          const id = parseInt(key, 10)
          if (Number.isFinite(id) && id > 0) {
            try {
              await deletePaper(id)
              ok++
            } catch {
              fail++
            }
          }
        }
        message.success(`已删除 ${ok} 篇${fail > 0 ? `，${fail} 篇失败` : ''}`)
        selectedRowKeys.value = []
        await loadFolders()
        await load()
      } catch (e: unknown) {
        message.error((e as Error).message || '批量删除失败')
      } finally {
        batchDeleting.value = false
      }
    },
  })
}
function escapeBibTeX(s: string): string {
  return String(s || '').replace(/([&%$#_{}~^\\])/g, '\\$1')
}
function paperToBibTeX(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean).join(' and ')
  const year = p.year ?? ''
  const title = escapeBibTeX(p.title || 'Untitled')
  const journal = escapeBibTeX(String(p.journal || p.venue || ''))
  const doi = p.doi || ''
  const arxiv = p.arxiv_id || ''
  const key = `${(p.authors?.[0]?.name || 'unknown').split(' ').pop()?.toLowerCase() || 'unknown'}${year}${title.slice(0, 3).toLowerCase()}`
  const lines = [`@article{${key},`]
  if (authors) lines.push(`  author = {${escapeBibTeX(authors)}},`)
  if (title) lines.push(`  title = {${title}},`)
  if (journal) lines.push(`  journal = {${journal}},`)
  if (year) lines.push(`  year = {${year}},`)
  if (doi) lines.push(`  doi = {${doi}},`)
  if (arxiv) lines.push(`  eprint = {${arxiv}},`)
  if (p.source_url) lines.push(`  url = {${p.source_url}},`)
  lines.push('}')
  return lines.join('\n')
}
const exportBibTeX = () => {
  const selected = papers.value.filter((p) => p.id != null && selectedRowKeys.value.includes(String(p.id)))
  if (selected.length === 0) {
    message.warning('请先选择要导出的文献')
    return
  }
  const bib = selected.map(paperToBibTeX).join('\n\n')
  const blob = new Blob([bib], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `papergraph_library_${new Date().toISOString().slice(0, 10)}.bib`
  a.click()
  URL.revokeObjectURL(url)
  message.success(`已导出 ${selected.length} 篇文献为 BibTeX`)
}
onMounted(async () => {
  await loadFolders()
  await load()
})
onBeforeUnmount(() => {
  if (searchDebounceTimer != null) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
})
</script>
<style scoped>
.library-page {
  max-width: min(1200px, 100%);
  margin: 0 auto;
  width: 100%;
}
.library-card {
  border-radius: var(--pg-radius-lg);
  box-shadow: var(--pg-shadow-xs);
  border: 1px solid var(--pg-border);
}
.library-card :deep(.ant-card-head) {
  border-bottom: 1px solid var(--pg-border-soft);
  min-height: 56px;
}
.library-card__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.library-card__title-text {
  font-family: var(--pg-font-serif);
  font-size: 17px;
  font-weight: 600;
  color: var(--pg-text-heading);
  letter-spacing: 0.01em;
}
.library-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.library-toolbar__search {
  flex: 1 1 240px;
  max-width: 420px;
  min-width: 0;
}
.library-layout {
  background: transparent;
  gap: 16px;
  flex-direction: row;
  align-items: flex-start;
}
.library-sider {
  flex: 0 0 200px;
  max-width: 200px;
  border-radius: var(--pg-radius-lg);
  border: 1px solid var(--pg-border);
  background: var(--pg-surface);
  box-shadow: var(--pg-shadow-xs);
  height: fit-content;
  padding-top: 10px;
}
.library-sider__title {
  font-weight: 600;
  padding: 0 14px 8px;
  font-size: 13px;
  color: var(--pg-text);
}
.library-sider__root {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  padding: 0 14px 8px;
  line-height: 1.4;
  word-break: break-all;
}
.library-sider__count {
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.library-sider__child-label {
  word-break: break-all;
}
.library-sider :deep(.ant-menu-inline .ant-menu-item),
.library-sider :deep(.ant-menu-inline .ant-menu-submenu-title) {
  padding-inline: 12px !important;
}
.library-sider :deep(.ant-menu-submenu .ant-menu-item) {
  padding-inline: 24px !important;
}
.library-content {
  min-height: 360px;
  flex: 1;
  min-width: 0;
}
.library-table :deep(.ant-table-thead > tr > th) {
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--pg-text-tertiary);
  background: var(--pg-bg-soft) !important;
  border-bottom: 1px solid var(--pg-border) !important;
  padding: 12px 16px !important;
}
.library-table :deep(.ant-table-tbody > tr > td) {
  vertical-align: middle;
  padding: 14px 16px !important;
  border-bottom: 1px solid var(--pg-border-soft) !important;
  transition: background 0.15s ease;
}
.library-table :deep(.ant-table-tbody > tr) {
  transition: background 0.15s ease;
}
.lib-year {
  white-space: nowrap;
  color: var(--pg-text-secondary);
}
.lib-venue {
  font-size: 12px;
  color: var(--pg-text-secondary);
  white-space: nowrap;
}
.library-table :deep(.ant-table-tbody > tr:hover > td) {
  background: var(--pg-primary-softer) !important;
}
.lib-title-wrap {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 6px;
  width: 100%;
  text-align: left;
  overflow-x: auto;
}
.lib-title-text {
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
  color: var(--pg-text-heading);
  font-size: 14px;
}
.lib-title-text:hover {
  color: var(--pg-primary-hover);
}
.lib-title-ext {
  font-size: 11px;
  white-space: nowrap;
  color: var(--pg-text-tertiary);
}
.lib-authors-cell {
  display: block;
  font-size: 13px;
  color: var(--pg-text-secondary);
  white-space: nowrap;
  overflow-x: auto;
  line-height: 1.45;
  word-break: break-word;
  text-align: left;
}
.lib-cell-actions {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 0;
}
@media (max-width: 900px) {
  .library-layout {
  flex-direction: column;
  }
  .library-sider {
  flex: 1 1 auto !important;
  max-width: none !important;
  width: 100% !important;
  }
  .library-toolbar__search {
  max-width: none;
  width: 100%;
  }
}
@media (max-width: 640px) {
  .library-card :deep(.ant-table) {
  font-size: 12px;
  }
  .lib-title-text {
  font-size: 13px;
  }
}
</style>