import { apiClient } from './client'

export interface TraceSession {
  session_id: string
  updated_at: number
  event_count: number
  agent_name: string
  has_html: boolean
}

export interface TraceEvent {
  ts: string
  session_id: string
  step: number | null
  event: string
  payload: Record<string, unknown>
}

export async function getTraceSessions(limit = 100): Promise<{ sessions: TraceSession[] }> {
  const response = await apiClient.get('/api/traces', { params: { limit } })
  return response.data
}

export async function getTraceEvents(sessionId: string, limit = 500): Promise<{ events: TraceEvent[] }> {
  const response = await apiClient.get(`/api/traces/${encodeURIComponent(sessionId)}`, { params: { limit } })
  return response.data
}
