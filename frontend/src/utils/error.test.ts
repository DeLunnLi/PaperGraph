import { describe, it, expect } from 'vitest'
import { isAbortError } from './error'

describe('isAbortError', () => {
  it('recognizes DOMException AbortError', () => {
    const e = new DOMException('aborted', 'AbortError')
    expect(isAbortError(e)).toBe(true)
  })

  it('recognizes axios ERR_CANCELED', () => {
    const e = { code: 'ERR_CANCELED', message: 'canceled' }
    expect(isAbortError(e)).toBe(true)
  })

  it('rejects generic errors', () => {
    expect(isAbortError(new Error('boom'))).toBe(false)
    expect(isAbortError({ code: 'OTHER' })).toBe(false)
    expect(isAbortError(null)).toBe(false)
    expect(isAbortError(undefined)).toBe(false)
  })

  it('rejects DOMException with other names', () => {
    const e = new DOMException('nope', 'NetworkError')
    expect(isAbortError(e)).toBe(false)
  })
})
