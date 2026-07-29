/** Auth token storage + 401 handling, shared by the axios client, the SSE fetch
 *  path, and the router guard. Keeps the "clear token → redirect to /login"
 *  contract in one place instead of three. */

const TOKEN_KEY = 'pg_token'
const USERNAME_KEY = 'pg_username'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

/** A JWT that exists and has not expired. */
export function isUsableToken(token: string | null = getToken()): boolean {
  if (!token) return false
  try {
    const payloadPart = token.split('.')[1]
    if (!payloadPart) return false
    const normalized = payloadPart.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')))
    return Number(payload.exp || 0) * 1000 > Date.now()
  } catch {
    return false
  }
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
}

/** Clear stored credentials and bounce to /login (unless already there). */
export function clearAuthAndRedirect(): void {
  clearAuth()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}
