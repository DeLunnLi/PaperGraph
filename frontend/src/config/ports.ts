/** Keep frontend ports aligned with ports.env. */

function parsePort(raw: string | undefined, fallback: number): number {
  const n = Number.parseInt(String(raw ?? '').trim(), 10)
  if (!Number.isFinite(n) || n < 1 || n > 65535) return fallback
  return n
}

export const BACKEND_PORT = parsePort(import.meta.env.VITE_BACKEND_PORT, 8000)

// Empty = same origin (production: nginx proxies /api/* to the backend;
// dev: vite proxy). Set VITE_API_BASE_URL to override.
export const BACKEND_ORIGIN = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export const backendLocalhostUrl = (port: number = BACKEND_PORT) =>
  `http://127.0.0.1:${port}`
