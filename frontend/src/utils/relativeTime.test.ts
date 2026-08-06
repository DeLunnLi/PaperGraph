import { describe, it, expect } from 'vitest'
import { formatRelativeTime } from './relativeTime'

const NOW = 1_800_000_000 // fixed reference timestamp (seconds)

describe('formatRelativeTime', () => {
  it('returns 从未 for empty/zero timestamps', () => {
    expect(formatRelativeTime(0, NOW)).toBe('从未')
    expect(formatRelativeTime(-5, NOW)).toBe('从未')
  })
  it('returns 刚刚 for <60s and future timestamps', () => {
    expect(formatRelativeTime(NOW - 30, NOW)).toBe('刚刚')
    expect(formatRelativeTime(NOW + 100, NOW)).toBe('刚刚')
  })
  it('returns minutes / hours / days buckets', () => {
    expect(formatRelativeTime(NOW - 120, NOW)).toBe('2 分钟前')
    expect(formatRelativeTime(NOW - 3 * 3600, NOW)).toBe('3 小时前')
    expect(formatRelativeTime(NOW - 5 * 86400, NOW)).toBe('5 天前')
  })
  it('falls back to a date for >30 days', () => {
    const ts = NOW - 40 * 86400
    const d = new Date(ts * 1000)
    const expected = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    expect(formatRelativeTime(ts, NOW)).toBe(expected)
  })
})
