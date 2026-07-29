import type { Paper } from '@/types'

/** Escape BibTeX special characters. */
export function escapeBib(s: string | null | undefined): string {
  return String(s || '').replace(/([&%$#_{}~^\\])/g, '\\$1')
}

/** Paper venue/journal name (the API exposes ``journal``; ``venue`` is not a real field). */
export function paperVenue(p: Paper | null | undefined): string {
  return String(p?.journal || '').trim()
}

export interface FormatAuthorsOptions {
  /** Max authors to show before truncating (0/undefined = all). */
  max?: number
  /** Suffix appended when authors are truncated (e.g. ' 等', ' et al.', '…'). */
  suffix?: string
  /** Value returned when there are no authors (default '—'). */
  empty?: string
}

/** Format a paper's author list for UI display. Dedupes the per-view hand-rolled
 *  variants (PaperCard / SearchResultPapers / PaperReader / KnowledgeGraph / Library). */
export function formatAuthors(
  p: Paper | null | undefined,
  opts: FormatAuthorsOptions = {},
): string {
  const { max, suffix = '', empty = '—' } = opts
  const names = (p?.authors || []).map((a) => a?.name).filter((n): n is string => !!n)
  if (!names.length) return empty
  if (max && max > 0 && names.length > max) {
    return `${names.slice(0, max).join(', ')}${suffix}`
  }
  return names.join(', ')
}

/** Best external link for a paper: source_url → pdf_url → arXiv abs → DOI. */
export function paperExternalUrl(p: Paper | null | undefined): string | null {
  if (!p) return null
  const src = String(p.source_url || '').trim()
  if (src && /^https?:\/\//i.test(src)) return src
  const pdf = String(p.pdf_url || '').trim()
  if (pdf && /^https?:\/\//i.test(pdf)) return pdf
  let ax = String(p.arxiv_id || '').trim()
  if (ax) {
    ax = ax.replace(/^arxiv:/i, '').replace(/\.pdf$/i, '')
    return `https://arxiv.org/abs/${ax}`
  }
  const doiRaw = String(p.doi || '').trim()
  if (doiRaw) {
    if (/^https?:\/\//i.test(doiRaw)) return doiRaw
    return `https://doi.org/${doiRaw.replace(/^doi:/i, '')}`
  }
  return null
}

function bibtexKey(p: Paper): string {
  const year = p.year ?? ''
  const last = (p.authors?.[0]?.name || 'unknown').split(' ').pop()?.toLowerCase() || 'unknown'
  const head = escapeBib(p.title || 'Untitled').slice(0, 3).toLowerCase()
  return `${last}${year}${head}`
}

export function toBibTeX(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean).join(' and ')
  const year = p.year ?? ''
  const title = escapeBib(p.title || 'Untitled')
  const journal = escapeBib(paperVenue(p))
  const doi = p.doi || ''
  const arxiv = p.arxiv_id || ''
  const lines = [`@article{${bibtexKey(p)},`]
  if (authors) lines.push(`  author = {${escapeBib(authors)}},`)
  if (title) lines.push(`  title = {${title}},`)
  if (journal) lines.push(`  journal = {${journal}},`)
  if (year) lines.push(`  year = {${year}},`)
  if (doi) lines.push(`  doi = {${doi}},`)
  if (arxiv) lines.push(`  eprint = {${arxiv}},`)
  if (p.source_url) lines.push(`  url = {${p.source_url}},`)
  lines.push('}')
  return lines.join('\n')
}

export function toAPA(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean)
  let authorStr = ''
  if (authors.length === 1) {
    authorStr = authors[0]
  } else if (authors.length === 2) {
    authorStr = `${authors[0]} & ${authors[1]}`
  } else if (authors.length === 3) {
    authorStr = `${authors[0]}, ${authors[1]}, & ${authors[2]}`
  } else if (authors.length > 3) {
    authorStr = `${authors[0]}, et al.`
  }
  const year = p.year ? `(${p.year})` : ''
  const title = p.title || ''
  const venue = paperVenue(p)
  const doi = p.doi ? ` https://doi.org/${p.doi}` : ''
  return [authorStr, year, title, venue, doi].filter(Boolean).join('. ') + '.'
}

export function toPlain(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).join(', ')
  const venue = paperVenue(p)
  const year = p.year ?? ''
  const doi = p.doi ? `DOI: ${p.doi}` : ''
  return [p.title || '', authors, `${venue} ${year}`.trim(), doi].filter(Boolean).join('\n')
}
