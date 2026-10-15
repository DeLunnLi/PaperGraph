import { apiClient } from './client'
import type { Paper } from '@/types'
type PaperReaderChatTurn = { role: string; content: string }
export type PaperReaderCitation = {
  marker: string
  page: number
  snippet?: string
  /** Verbatim excerpt the LLM quoted from page N (passage-level grounding). */
  quote?: string | null
  /** True = quote verified on page N; False = quote present but not found on page (hallucination signal); null/undefined = page-level only (no quote). */
  verified?: boolean | null
}
const PAPER_READER_CHAT_REQUEST_MS = 240000
interface PaperReaderChatOptions {
  signal?: AbortSignal
}
export async function postPaperReaderOpening(paperId: number): Promise<{ success: boolean; opening: string; pdf_parsing?: boolean }> {
  const response = await apiClient.post('/api/ai/paper-reader/opening', { paper_id: paperId })
  return response.data
}
export async function postPaperReaderChat(body: {
  paper_id: number; messages: PaperReaderChatTurn[]; user_message: string
}, options: PaperReaderChatOptions = {}): Promise<{
  success: boolean; reply: string; related_papers?: Paper[]; citations?: PaperReaderCitation[]; assistant_turn_id?: number | null
}> {
  const response = await apiClient.post('/api/ai/paper-reader/chat', body, {
    timeout: PAPER_READER_CHAT_REQUEST_MS,
    signal: options.signal,
  })
  return response.data
}
export async function getPaperReaderHistory(paperId: number, limit = 200): Promise<{
  success: boolean; turns: { id?: number | null; role: string; content: string }[]
}> {
  const response = await apiClient.get('/api/ai/paper-reader/history', { params: { paper_id: paperId, limit } })
  return response.data
}

export async function postReaderFeedback(body: {
  paper_id: number; turn_id: number; signal: 'up' | 'down' | 'citation_click'
}): Promise<{ success: boolean; affected_memory_count?: number }> {
  const response = await apiClient.post('/api/ai/paper-reader/feedback', body, { timeout: 30000 })
  return response.data
}
