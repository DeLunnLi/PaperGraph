/**
 * Lazy katex loader.
 *
 * katex (~294KB JS+CSS) is only needed when a string actually contains math
 * ($...$). Most paper titles/abstracts have none, so we keep it out of the
 * initial bundle via a dynamic import and cache the resolved module. Callers
 * that render math synchronously fall back to plain text until katex arrives;
 * routes that are likely to show math can call ensureKatex() on mount to warm
 * it ahead of time.
 */
import type KatexType from 'katex'

let katexMod: typeof KatexType | null = null
let loadPromise: Promise<typeof KatexType> | null = null

export function ensureKatex(): Promise<typeof KatexType> {
  if (katexMod) return Promise.resolve(katexMod)
  if (!loadPromise) {
    loadPromise = import('katex').then((m) => {
      katexMod = m.default ?? (m as unknown as typeof KatexType)
      // Side-effect-import the CSS the first time katex loads.
      void import('katex/dist/katex.min.css')
      return katexMod
    })
  }
  return loadPromise
}

export interface KatexRenderOptions {
  displayMode?: boolean
  throwOnError?: boolean
  output?: 'html' | 'htmlAndMathml' | 'mathml'
  strict?: boolean | 'error' | 'ignore' | 'warn'
}

/** Render tex to HTML synchronously. Returns null if katex is not loaded yet. */
export function renderKatex(tex: string, opts: KatexRenderOptions = {}): string | null {
  if (!katexMod) return null
  try {
    return katexMod.renderToString(tex, {
      displayMode: opts.displayMode ?? false,
      throwOnError: opts.throwOnError ?? false,
      output: opts.output ?? 'html',
      strict: opts.strict ?? 'ignore',
    })
  } catch {
    return null
  }
}

/** Kick off loading on module import so it is warm by the time math renders. */
void ensureKatex()
