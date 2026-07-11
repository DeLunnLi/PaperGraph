/** Keep frontend ports aligned with ports.env. */

function parsePort(raw: string | undefined, fallback: number): number {
  const n = Number.parseInt(String(raw ?? '').trim(), 10)
  if (!Number.isFinite(n) || n < 1 || n > 65535) return fallback
  return n
}

export const BACKEND_PORT = parsePort(import.meta.env.VITE_BACKEND_PORT, 8000)

// In production (Docker/nginx), the frontend is served by nginx which proxies
// /api/* to the backend. So the API base URL should be empty (same origin).
// In development (vite dev server), we use VITE proxy or direct localhost.
export const BACKEND_ORIGIN =
  (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '') ||
  (import.meta.env.DEV ? '' : '')  // Production: empty = same origin (nginx proxy)

export const backendLocalhostUrl = (port: number = BACKEND_PORT) =>
  `http://127.0.0.1:${port}`
