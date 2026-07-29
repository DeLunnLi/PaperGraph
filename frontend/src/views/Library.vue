<template>
  <div class="library-page">
    <section class="library-overview">
      <div>
        <div class="library-overview__eyebrow">PERSONAL RESEARCH ARCHIVE</div>
        <h1>我的文献库</h1>
        <p>集中管理已收藏的论文，快速检索、批量导出并进入深度阅读。</p>
      </div>
      <div class="library-overview__stat">
        <strong>{{ pagination.total }}</strong>
        <span>篇论文</span>
      </div>
    </section>
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
              <span class="library-card__title-text">文献列表</span>
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
              <a-button type="primary" class="library-import-button" @click="openImportDialog">
                <UploadOutlined /> 本地导入
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

    <a-modal
      v-model:open="importDialogOpen"
      title="导入本地论文"
      width="600px"
      :footer="null"
      :mask-closable="!importing"
      :closable="!importing"
      class="library-import-modal"
    >
      <div class="import-dialog">
        <div class="import-dialog__intro">
          <div class="import-dialog__icon"><InboxOutlined /></div>
          <div>
            <strong>上传 PDF，自动补全论文信息</strong>
            <p>系统会解析标题、DOI、arXiv ID 和页数，并尝试从 Crossref、arXiv、OpenAlex 等来源补全作者、摘要与期刊信息。</p>
          </div>
        </div>
        <a-upload-dragger
          :file-list="importFileList"
          :before-upload="beforePdfUpload"
          :custom-request="handlePdfUpload"
          :multiple="false"
          :max-count="1"
          accept="application/pdf,.pdf"
          :disabled="importing"
          @remove="removeImportFile"
        >
          <p class="ant-upload-drag-icon"><FilePdfOutlined /></p>
          <p class="ant-upload-text">点击或拖拽一篇 PDF 到这里</p>
          <p class="ant-upload-hint">单文件最大 200 MiB；加密或损坏的 PDF 无法导入</p>
        </a-upload-dragger>
        <div class="import-dialog__category">
          <span><strong>导入到</strong><small>可指定当前分类，也可以交给系统自动归类</small></span>
          <a-select v-model:value="importCategory" :disabled="importing" style="min-width: 220px">
            <a-select-option value="__auto__">自动选择分类</a-select-option>
            <a-select-option v-for="option in importCategoryOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </a-select-option>
          </a-select>
        </div>
        <div class="import-dialog__options">
          <label class="import-option">
            <span><strong>联网补全元数据</strong><small>通过 DOI、arXiv ID 或标题查找可信来源</small></span>
            <a-switch v-model:checked="importAutoEnrich" :disabled="importing" aria-label="联网补全元数据" />
          </label>
          <label class="import-option">
            <span><strong>自动归类</strong><small>根据论文内容放入合适的文献库分类</small></span>
            <a-switch v-model:checked="importAutoClassify" :disabled="importing || importCategory !== '__auto__'" aria-label="自动归类" />
          </label>
        </div>
        <div v-if="importing" class="import-processing" role="status" aria-live="polite">
          <a-progress v-if="importStage === 'uploading'" :percent="importProgress" status="active" />
          <a-spin v-else size="small" />
          <span>{{ importStage === 'uploading' ? `正在上传 PDF（${importProgress}%）` : '文件已上传，正在解析并补全论文信息…' }}</span>
          <a-button type="link" danger size="small" @click="cancelImport">取消</a-button>
        </div>
        <a-alert v-if="importError" type="error" show-icon :message="importError" role="alert">
          <template #action><a-button size="small" @click="resetImportState">选择其他文件</a-button></template>
        </a-alert>
        <div v-if="importResult" class="import-result" role="status" aria-live="polite">
          <div class="import-result__status">{{ importResultStatus }}</div>
          <strong>{{ importResult.paper.title }}</strong>
          <div class="import-result__meta">
            <a-tag color="green">{{ importResult.page_count }} 页</a-tag>
            <a-tag color="blue">{{ importSourceLabel(importResult.metadata_source) }}</a-tag>
            <a-tag v-if="importResult.detected_doi">DOI 已识别</a-tag>
            <a-tag v-if="importResult.detected_arxiv_id">arXiv 已识别</a-tag>
          </div>
          <div class="import-result__actions">
            <a-button @click="resetImportState">继续导入</a-button>
            <a-button v-if="importResult.paper.id" type="primary" @click="goReader(importResult.paper)">开始阅读</a-button>
            <a-button @click="importDialogOpen = false">完成</a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal, Upload } from 'ant-design-vue'
import type { UploadFile, UploadProps } from 'ant-design-vue'
import type { UploadRequestOption } from 'ant-design-vue/es/vc-upload/interface'
import { ExportOutlined, FilePdfOutlined, InboxOutlined, UploadOutlined } from '@ant-design/icons-vue'
import { getLibrary, getLibraryCategoryFolders, deletePaper, importLocalPdf } from '@/services/api'
import type { LocalPdfImportResponse } from '@/services/api/papers'
import apiClient from '@/services/api/client'
import type { LibraryCategoryFolder, Paper } from '@/types'
import { toBibTeX } from '@/utils/citation'
import { downloadBlob, downloadText, todayStamp } from '@/utils/download'
import { isAbortError } from '@/utils/error'
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
const importDialogOpen = ref(false)
const importing = ref(false)
const importProgress = ref(0)
const importStage = ref<'idle' | 'uploading' | 'processing'>('idle')
const importError = ref('')
let libraryLoadSeq = 0
const importCategory = ref('__auto__')
const importAutoEnrich = ref(true)
const importAutoClassify = ref(true)
const importFileList = ref<UploadFile[]>([])
const importResult = ref<LocalPdfImportResponse | null>(null)
let importAbortController: AbortController | null = null
const importCategoryOptions = computed(() => folders.value.flatMap((folder) => [
  { value: folder.category, label: folder.category },
  ...(folder.children || []).map((child) => ({ value: child.category, label: `　${child.label || child.category}` })),
]))
const importResultStatus = computed(() => {
  const result = importResult.value
  if (!result) return ''
  if (result.added) return '已添加到文献库'
  if (result.pdf_attached) return '已匹配现有文献并关联 PDF'
  return '文献已存在，已保留原有 PDF'
})
const resetImportState = () => {
  importResult.value = null
  importError.value = ''
  importProgress.value = 0
  importStage.value = 'idle'
  importFileList.value = []
}
const cancelImport = () => {
  importAbortController?.abort()
  importAbortController = null
}
const openImportDialog = () => {
  importDialogOpen.value = true
  resetImportState()
  importCategory.value = selectedKey.value === '__all__' ? '__auto__' : selectedKey.value
}
const beforePdfUpload: UploadProps['beforeUpload'] = (file) => {
  const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  if (!isPdf) {
    message.error('仅支持 PDF 文件')
    return Upload.LIST_IGNORE
  }
  if (file.size > 200 * 1024 * 1024) {
    message.error('PDF 文件不能超过 200 MiB')
    return Upload.LIST_IGNORE
  }
  return true
}
const removeImportFile = () => {
  if (importing.value) return false
  resetImportState()
  return true
}
const importSourceLabel = (source: string) => ({
  crossref: 'Crossref 补全', arxiv: 'arXiv 补全', openalex: 'OpenAlex 补全',
  dblp: 'DBLP 补全', pdf: 'PDF 解析', local: '本地解析',
}[source] || `${source} 补全`)
const handlePdfUpload = async (options: UploadRequestOption) => {
  const file = options.file as File
  importing.value = true
  importProgress.value = 1
  importStage.value = 'uploading'
  importResult.value = null
  importError.value = ''
  importAbortController = new AbortController()
  importFileList.value = [{ uid: (file as File & { uid?: string }).uid || String(Date.now()), name: file.name, status: 'uploading', originFileObj: file as any }]
  try {
    const result = await importLocalPdf(file, {
      category: importCategory.value === '__auto__' ? undefined : importCategory.value,
      auto_enrich: importAutoEnrich.value,
      auto_classify: importCategory.value === '__auto__' && importAutoClassify.value,
      signal: importAbortController.signal,
      onProgress: (percent) => {
        importProgress.value = percent
        if (percent >= 99) importStage.value = 'processing'
      },
    })
    importResult.value = result
    importFileList.value = importFileList.value.map((item) => ({ ...item, status: 'done' }))
    options.onSuccess?.(result)
    pagination.value.current = 1
    await loadFolders()
    await load()
    message.success(result.message || 'PDF 已导入文献库')
  } catch (error) {
    const cancelled = isAbortError(error)
    importFileList.value = importFileList.value.map((item) => ({ ...item, status: 'error' }))
    importError.value = cancelled
      ? '已取消等待。若文件已上传完成，服务端可能仍在处理，请稍后刷新文献库。'
      : ((error as Error).message || 'PDF 导入失败')
    options.onError?.(error as Error)
    if (!cancelled) message.error(importError.value)
  } finally {
    importing.value = false
    importStage.value = 'idle'
    importAbortController = null
  }
}
const onExportKnowledge = async ({ key }: { key: string }) => {
  exporting.value = true
  try {
    const resp = await apiClient.get(`/api/export/json`, {
      params: { scope: key },
      responseType: 'blob',
      timeout: 60000,
    })
    const blob = new Blob([resp.data], { type: 'application/json' })
    const disposition = resp.headers['content-disposition'] || ''
    const match = disposition.match(/filename="?([^"]+)"?/)
    downloadBlob(blob, match ? match[1] : `papergraph_export_${key}.json`)
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
const load = async () => {
  const requestSeq = ++libraryLoadSeq
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
    if (requestSeq !== libraryLoadSeq) return
    if (res.success) {
      papers.value = res.papers ?? []
      pagination.value = { ...pagination.value, total: typeof res.total === 'number' ? res.total : papers.value.length }
    }
  } catch (e: unknown) {
    if (requestSeq !== libraryLoadSeq) return
    message.error((e as Error).message || '加载失败')
  } finally {
    if (requestSeq === libraryLoadSeq) {
      loading.value = false
    }
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
const exportBibTeX = () => {
  const selected = papers.value.filter((p) => p.id != null && selectedRowKeys.value.includes(String(p.id)))
  if (selected.length === 0) {
    message.warning('请先选择要导出的文献')
    return
  }
  const bib = selected.map(toBibTeX).join('\n\n')
  downloadText(bib, `papergraph_library_${todayStamp()}.bib`)
  message.success(`已导出 ${selected.length} 篇文献为 BibTeX`)
}
onMounted(async () => {
  await loadFolders()
  await load()
})
onBeforeUnmount(() => {
  importAbortController?.abort()
  if (searchDebounceTimer != null) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
})
</script>
<style scoped>
.library-import-button { box-shadow: var(--pg-shadow-primary); }
.import-dialog { display: flex; flex-direction: column; gap: 18px; padding-top: 4px; }
.import-dialog__intro { display: flex; align-items: flex-start; gap: 14px; padding: 16px; border-radius: 16px; background: var(--pg-primary-soft); border: 1px solid #dfe3ff; }
.import-dialog__icon { width: 42px; height: 42px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 13px; color: var(--pg-primary); background: #fff; font-size: 21px; box-shadow: var(--pg-shadow-xs); }
.import-dialog__intro strong { color: var(--pg-text-heading); font-size: 15px; }
.import-dialog__intro p { margin: 5px 0 0; color: var(--pg-text-secondary); font-size: 12px; line-height: 1.65; }
.import-dialog__category { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 13px 14px; border: 1px solid var(--pg-divider); border-radius: 13px; }
.import-dialog__category > span { display: flex; flex-direction: column; gap: 3px; }
.import-dialog__category strong { color: var(--pg-text-heading); font-size: 13px; }
.import-dialog__category small { color: var(--pg-text-tertiary); font-size: 10px; }
.import-dialog__options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.import-option { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 14px; border: 1px solid var(--pg-divider); border-radius: 13px; background: var(--pg-bg-soft); }
.import-option > span { display: flex; flex-direction: column; gap: 3px; }
.import-option strong { color: var(--pg-text-heading); font-size: 13px; }
.import-option small { color: var(--pg-text-tertiary); font-size: 10px; line-height: 1.4; }
.import-result { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding: 16px; border-radius: 15px; border: 1px solid #b7ebc6; background: #f4fbf6; }
.import-result__status { color: #16803a; font-size: 11px; font-weight: 700; letter-spacing: .08em; }
.import-result > strong { color: var(--pg-text-heading); font-family: var(--pg-font-serif); font-size: 17px; }
.import-result__meta { display: flex; flex-wrap: wrap; gap: 5px; }
.import-result__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.import-processing { display: flex; align-items: center; gap: 10px; color: var(--pg-text-secondary); font-size: 12px; }
.import-processing :deep(.ant-progress) { flex: 1; margin: 0; }
@media (max-width: 640px) {
  .import-dialog__options { grid-template-columns: 1fr; }
  .import-dialog__category { align-items: stretch; flex-direction: column; }
  .import-dialog__category :deep(.ant-select) { width: 100%; }
  .import-result__actions { width: 100%; flex-direction: column; }
  .import-result__actions :deep(.ant-btn) { width: 100%; }
}
.library-page {
  max-width: min(var(--pg-content-max), 100%);
  margin: 0 auto;
  width: 100%;
}
.library-overview {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 8px 4px 30px;
}
.library-overview__eyebrow {
  color: var(--pg-accent);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .15em;
  margin-bottom: 8px;
}
.library-overview h1 {
  margin: 0;
  color: var(--pg-text-heading);
  font: 700 clamp(24px, 3vw, 34px)/1.2 var(--pg-font-serif);
}
.library-overview p {
  margin: 8px 0 0;
  color: var(--pg-text-secondary);
}
.library-overview__stat {
  min-width: 98px;
  padding: 13px 16px;
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg);
  background: rgba(255,255,255,.78);
  box-shadow: var(--pg-shadow-sm);
  backdrop-filter: blur(12px);
  text-align: center;
}
.library-overview__stat strong {
  display: block;
  color: var(--pg-primary);
  font-size: 24px;
  line-height: 1.1;
}
.library-overview__stat span { color: var(--pg-text-tertiary); font-size: 11px; }
.library-card {
border-radius: var(--pg-radius-xl);
  box-shadow: var(--pg-shadow-md);
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
  gap: 20px;
  flex-direction: row;
  align-items: flex-start;
}
.library-sider {
  flex: 0 0 200px;
  max-width: 200px;
  border-radius: var(--pg-radius-xl);
  border: 1px solid var(--pg-border);
  background: rgba(255,255,255,.86);
  box-shadow: var(--pg-shadow-sm);
  overflow: hidden;
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
  padding: 16px !important;
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
  white-space: normal;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  line-height: 1.5;
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
.library-overview {
  align-items: flex-start;
  padding: 2px 2px 18px;
}
.library-overview__stat { min-width: 72px; padding: 10px; }
.library-overview__stat strong { font-size: 20px; }
.library-overview p { font-size: 12px; }
.library-card :deep(.ant-card-head) { padding: 0 14px; }
.library-card :deep(.ant-card-body) { padding: 14px; }
.library-card :deep(.ant-table) {
  font-size: 12px;
  }
  .lib-title-text {
  font-size: 13px;
  }
}
</style>
