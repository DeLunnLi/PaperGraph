import { describe, it, expect } from 'vitest'
import {
  escapeBib,
  paperVenue,
  paperExternalUrl,
  formatAuthors,
  toBibTeX,
  toAPA,
  toPlain,
} from './citation'
import type { Paper } from '@/types'

function paper(p: Partial<Paper>): Paper {
  return {
    title: 'T',
    authors: [],
    ...p,
  } as Paper
}

describe('escapeBib', () => {
  it('escapes BibTeX special chars', () => {
    expect(escapeBib('a&b%c')).toBe('a\\&b\\%c')
  })
  it('handles null/empty', () => {
    expect(escapeBib(null)).toBe('')
    expect(escapeBib('')).toBe('')
  })
})

describe('paperVenue', () => {
  it('returns trimmed journal', () => {
    expect(paperVenue(paper({ journal: '  NeurIPS  ' }))).toBe('NeurIPS')
  })
  it('returns empty for missing journal', () => {
    expect(paperVenue(paper({}))).toBe('')
    expect(paperVenue(null)).toBe('')
  })
})

describe('paperExternalUrl', () => {
  it('prefers source_url', () => {
    expect(paperExternalUrl(paper({ source_url: 'https://example.com/a' }))).toBe('https://example.com/a')
  })
  it('falls back to pdf_url', () => {
    expect(paperExternalUrl(paper({ pdf_url: 'https://x.com/p.pdf' }))).toBe('https://x.com/p.pdf')
  })
  it('falls back to arxiv abs', () => {
    expect(paperExternalUrl(paper({ arxiv_id: '2401.12345' }))).toBe('https://arxiv.org/abs/2401.12345')
  })
  it('falls back to doi.org', () => {
    expect(paperExternalUrl(paper({ doi: '10.1000/xyz' }))).toBe('https://doi.org/10.1000/xyz')
  })
  it('returns null when nothing available', () => {
    expect(paperExternalUrl(paper({}))).toBeNull()
  })
})

describe('formatAuthors', () => {
  it('returns empty default when no authors', () => {
    expect(formatAuthors(paper({}))).toBe('—')
    expect(formatAuthors(null)).toBe('—')
  })
  it('joins all authors when no max', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }, { name: 'C' }] })
    expect(formatAuthors(p)).toBe('A, B, C')
  })
  it('truncates with suffix when over max', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }, { name: 'C' }, { name: 'D' }] })
    expect(formatAuthors(p, { max: 3, suffix: ' et al.' })).toBe('A, B, C et al.')
    expect(formatAuthors(p, { max: 2, suffix: ' 等' })).toBe('A, B 等')
  })
  it('does not truncate when at or under max', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }] })
    expect(formatAuthors(p, { max: 2, suffix: '…' })).toBe('A, B')
  })
  it('filters undefined/empty names', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: '' }, { name: undefined }, { name: 'D' }] } as never)
    expect(formatAuthors(p)).toBe('A, D')
  })
  it('respects custom empty', () => {
    expect(formatAuthors(paper({}), { empty: '' })).toBe('')
  })
})

describe('toAPA', () => {
  it('formats 1 author', () => {
    const p = paper({ authors: [{ name: 'Smith' }], year: 2024, title: 'A Method', journal: 'Nature' })
    expect(toAPA(p)).toBe('Smith. (2024). A Method. Nature.')
  })
  it('formats 2 authors with & (round-1 fix: no trailing & )', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }], year: 2024, title: 'T', journal: 'J' })
    expect(toAPA(p)).toBe('A & B. (2024). T. J.')
  })
  it('formats 3 authors with , & before last', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }, { name: 'C' }], year: 2024, title: 'T', journal: 'J' })
    expect(toAPA(p)).toBe('A, B, & C. (2024). T. J.')
  })
  it('uses et al. for >3 authors', () => {
    const p = paper({ authors: [{ name: 'A' }, { name: 'B' }, { name: 'C' }, { name: 'D' }], year: 2024, title: 'T', journal: 'J' })
    expect(toAPA(p)).toBe('A, et al.. (2024). T. J.')
  })
  it('omits year when absent', () => {
    const p = paper({ authors: [{ name: 'A' }], title: 'T', journal: 'J', doi: '10.1/x' })
    expect(toAPA(p)).toBe('A. T. J.  https://doi.org/10.1/x.')
  })
})

describe('toBibTeX', () => {
  it('produces an @article entry with key from author+year+title', () => {
    const p = paper({
      authors: [{ name: 'Jane Smith' }], year: 2024, title: 'Cool Method',
      journal: 'Nature', doi: '10.1/x', arxiv_id: '2401.1',
    })
    const bib = toBibTeX(p)
    expect(bib.startsWith('@article{smith2024coo,')).toBe(true)
    expect(bib).toContain('author = {Jane Smith}')
    expect(bib).toContain('title = {Cool Method}')
    expect(bib).toContain('journal = {Nature}')
    expect(bib).toContain('year = {2024}')
    expect(bib).toContain('doi = {10.1/x}')
    expect(bib).toContain('eprint = {2401.1}')
  })
  it('escapes BibTeX special chars in title', () => {
    const p = paper({ title: 'A&B', authors: [{ name: 'X' }] })
    expect(toBibTeX(p)).toContain('title = {A\\&B}')
  })
})

describe('toPlain', () => {
  it('joins title/authors/venue/year/doi with newlines', () => {
    const p = paper({
      title: 'T', authors: [{ name: 'A' }, { name: 'B' }], journal: 'J', year: 2024, doi: '10.1/x',
    })
    expect(toPlain(p)).toBe('T\nA, B\nJ 2024\nDOI: 10.1/x')
  })
})
