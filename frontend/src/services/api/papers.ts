import { apiClient } from './client'
import type { Paper, PapersResponse, LibraryCategoriesResponse, SavePapersResponse } from '@/types'

export interface LocalPdfImportResponse {
  success: boolean
  message?: string | null
  paper: Paper
  added: boolean
  pdf_attached: boolean
  metadata_source: string
  detected_doi?: string | null
  detected_arxiv_id?: string | null
  page_count: number
}
export async function getLibrary(limit = 50, params?: {
  q?: string; year_from?: number; year_to?: number
  read_status?: string; tags?: string; category?: string; offset?: number
}): Promise<PapersResponse> {
  const response = await apiClient.get<PapersResponse>('/api/papers/library', { params: { limit, ...params } })
  return response.data
}
export async function getLibraryCategoryFolders(): Promise<LibraryCategoriesResponse> {
  const response = await apiClient.get<LibraryCategoriesResponse>('/api/papers/library/categories')
  return response.data
}
export function getLibraryPdfHref(paperId: number): string {
  const base = (apiClient.defaults.baseURL || '').replace(/\/$/, '')
  return `${base}/api/papers/${paperId}/library-pdf`
}
export async function getLibraryPdfStreamUrl(paperId: number): Promise<string> {
  const response = await apiClient.post<{ success: boolean; ticket: string; expires_in: number }>(
    `/api/papers/${paperId}/pdf-ticket`,
  )
  if (!response.data?.ticket) throw new Error('无法创建 PDF 访问票据')
  const base = (apiClient.defaults.baseURL || '').replace(/\/$/, '')
  return `${base}/api/papers/${paperId}/library-pdf?ticket=${encodeURIComponent(response.data.ticket)}`
}
export async function getPaper(id: number): Promise<Paper> {
  const response = await apiClient.get<Paper>(`/api/papers/${id}`)
  return response.data
}
export async function ensurePaperPdf(id: number): Promise<Paper> {
  const response = await apiClient.post<Paper>(`/api/papers/${id}/ensure-pdf`, undefined, { timeout: 180000 })
  return response.data
}
export async function savePapers(papers: Paper[], options?: {
  download_pdfs?: boolean; llm_classify?: boolean
}): Promise<SavePapersResponse> {
  const payload = { papers, download_pdfs: options?.download_pdfs ?? true, llm_classify: options?.llm_classify ?? true }
  const n = papers.length
  const heavy = (options?.download_pdfs ?? true) || (options?.llm_classify ?? true)
  let timeoutMs = 90000
  if (heavy && (options?.download_pdfs ?? true) && n > 1) {
    timeoutMs = Math.min(420000, 90000 + n * 45000)
  } else if (heavy) {
    timeoutMs = 300000
  }
  const response = await apiClient.post<SavePapersResponse>('/api/papers/save', payload, { timeout: timeoutMs })
  return response.data
}
export async function importLocalPdf(file: File, options?: {
  category?: string
  auto_enrich?: boolean
  auto_classify?: boolean
  onProgress?: (percent: number) => void
  signal?: AbortSignal
}): Promise<LocalPdfImportResponse> {
  const formData = new FormData()
  formData.append('file', file, file.name)
  if (options?.category?.trim()) formData.append('category', options.category.trim())
  formData.append('auto_enrich', String(options?.auto_enrich ?? true))
  formData.append('auto_classify', String(options?.auto_classify ?? true))
  const response = await apiClient.post<LocalPdfImportResponse>('/api/papers/import-pdf', formData, {
    timeout: 360000,
    signal: options?.signal,
    onUploadProgress: (event) => {
      if (event.total && options?.onProgress) {
        options.onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)))
      }
    },
  })
  options?.onProgress?.(100)
  return response.data
}

export async function deletePaper(id: number): Promise<void> {
  await apiClient.delete(`/api/papers/${id}`)
}
export async function postReadingLog(body: {
  paper_id: number; duration_sec: number; client_ts?: number
}): Promise<{ success: boolean }> {
  const response = await apiClient.post('/api/papers/reading/log', body)
  return response.data
}
export async function getReadingCalendar(days = 180): Promise<{
  success: boolean; days: number; items: { date: string; seconds: number; sessions: number }[]
}> {
  const response = await apiClient.get('/api/papers/reading/calendar', { params: { days } })
  return response.data
}