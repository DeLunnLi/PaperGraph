import { describe, it, expect } from 'vitest'
import { clipTextAvoidBreakingMath } from './markdown'

describe('clipTextAvoidBreakingMath', () => {
  it('returns short strings unchanged', () => {
    expect(clipTextAvoidBreakingMath('short', 10)).toBe('short')
  })

  it('truncates with ellipsis when no math', () => {
    const s = 'x'.repeat(20)
    expect(clipTextAvoidBreakingMath(s, 10)).toBe('xxxxxxxxxx…')
  })

  it('backs up before an unclosed $ delimiter', () => {
    // cut point lands inside an unclosed math segment: "foo $x^2" — the odd $
    // means the math run is unclosed, so we back up to before the '$'.
    const s = 'foo $x^2 bar baz qux'
    const out = clipTextAvoidBreakingMath(s, 9)
    expect(out).toBe('foo…')
    expect(out).not.toContain('$')
  })

  it('does not cut after a closed math segment', () => {
    // "foo $x$ bar" — the $ pair is closed, so a cut after it is fine.
    const s = 'foo $x$ bar' + 'z'.repeat(20)
    const out = clipTextAvoidBreakingMath(s, 10)
    expect(out.endsWith('…')).toBe(true)
  })

  it('handles empty/null', () => {
    expect(clipTextAvoidBreakingMath('', 10)).toBe('')
    expect(clipTextAvoidBreakingMath(null as unknown as string, 10)).toBe(null as unknown as string)
  })
})
