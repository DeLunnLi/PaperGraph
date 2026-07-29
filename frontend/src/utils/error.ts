/** True for AbortController/abortable-fetch cancellations (DOMException) and
 *  axios cancellations (``ERR_CANCELED``). Used to distinguish user-cancelled
 *  requests from real errors. */
export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'AbortError') return true
  return (error as { code?: string } | null | undefined)?.code === 'ERR_CANCELED'
}
