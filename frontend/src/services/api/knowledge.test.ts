import { expect, it, vi } from 'vitest'
import { getKnowledgeStats } from './knowledge'
import { apiClient } from './client'

vi.mock('./client', () => ({ apiClient: { get: vi.fn(async () => ({ data: { total: 5 } })) } }))

it('requests calendar-day aggregates using the browser time zone', async () => {
  expect(await getKnowledgeStats()).toEqual({ total: 5 })
  expect(apiClient.get).toHaveBeenCalledWith('/api/knowledge/stats', {
    params: { time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' },
  })
})
