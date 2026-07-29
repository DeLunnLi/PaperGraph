import { describe, it, expect, beforeEach, vi } from 'vitest'
import { isUsableToken } from './auth'

/** Build a JWT with a given exp (seconds since epoch). */
function jwt(exp: number | null): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).replace(/=/g, '')
  const payload = btoa(JSON.stringify(exp === null ? {} : { exp })).replace(/=/g, '')
  return `${header}.${payload}.sig`
}

describe('isUsableToken', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2025-01-01T00:00:00Z'))
  })

  it('accepts a token expiring in the future', () => {
    // now = 2025-01-01 = 1735689600; exp 1h later
    vi.setSystemTime(new Date(1735689600 * 1000))
    const t = jwt(1735689600 + 3600)
    expect(isUsableToken(t)).toBe(true)
  })

  it('rejects an expired token', () => {
    vi.setSystemTime(new Date(1735689600 * 1000))
    const t = jwt(1735689600 - 3600)
    expect(isUsableToken(t)).toBe(false)
  })

  it('rejects null/empty', () => {
    expect(isUsableToken(null)).toBe(false)
    expect(isUsableToken('')).toBe(false)
  })

  it('rejects a token without exp claim', () => {
    expect(isUsableToken(jwt(null))).toBe(false)
  })

  it('rejects malformed token', () => {
    expect(isUsableToken('not-a-jwt')).toBe(false)
    expect(isUsableToken('a.b')).toBe(false)
  })
})
