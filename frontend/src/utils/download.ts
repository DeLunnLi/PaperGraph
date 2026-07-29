/** Trigger a browser download for a Blob. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Trigger a browser download for a text string. */
export function downloadText(text: string, filename: string, mime = 'text/plain;charset=utf-8'): void {
  downloadBlob(new Blob([text], { type: mime }), filename)
}

/** Current date as ``YYYY-MM-DD`` for download filenames. */
export function todayStamp(): string {
  return new Date().toISOString().slice(0, 10)
}
