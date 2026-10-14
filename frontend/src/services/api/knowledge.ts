import { apiClient } from './client'

export type KnowledgeScope = 'global' | 'paper' | 'agent' | 'all'
export type KnowledgeSort = 'utility' | 'access' | 'recency' | 'importance'

export interface KnowledgeMemoryItem {
  memory_id: string
  scope: 'global' | 'paper' | 'agent'
  paper_id: number | null
  paper_title?: string | null
  kind: string
  content: string
  importance: number
  utility: number
  access_count: number
  last_used: number
  timestamp: number
}

export interface KnowledgeHealth {
  feedback: {
    up: number
    down: number
    citation_click: number
    engaged_read: number
    up_ratio: number | null
  }
  utility_distribution: { negative: number; neutral: number; positive: number }
  evolution_7d: Record<string, number>
}

export interface KnowledgeStats {
  total: number
  avg_utility: number
  total_accesses: number
  evolution_events_24h: number
  activity_7d?: { date: string; count: number }[]
  health?: KnowledgeHealth
}

export interface KnowledgeEvolutionItem {
  id: number
  source_db: 'reader' | 'agent'
  event: string
  utility_delta: number
  reason: string
  created_at: number
}

export async function getKnowledgeMemories(params: {
  scope: KnowledgeScope; paper_id?: number; kind?: string; sort: KnowledgeSort; limit?: number
}): Promise<{ items: KnowledgeMemoryItem[] }> {
  const response = await apiClient.get('/api/knowledge/memories', { params })
  return response.data
}

export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const response = await apiClient.get('/api/knowledge/stats', { params: { time_zone: timeZone } })
  return response.data
}

export async function getKnowledgeEvolution(limit = 50): Promise<{
  items: KnowledgeEvolutionItem[]
}> {
  const response = await apiClient.get('/api/knowledge/evolution', { params: { limit } })
  return response.data
}
