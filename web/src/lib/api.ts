// One small client for the backend. Every screen (game, live, admin) goes through here.

const PLAYER_KEY = 'quiz.player.token'
const ADMIN_KEY = 'quiz.admin.token'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // private browsing with storage blocked: the session simply lasts for this page
  }
}

function write(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}

let playerToken = read(PLAYER_KEY)
let adminToken = read(ADMIN_KEY)

const adminTokenListeners = new Set<(token: string | null) => void>()

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== ADMIN_KEY) return
    adminToken = event.newValue
    adminTokenListeners.forEach((listener) => listener(adminToken))
  })
}

export function onAdminTokenChange(listener: (token: string | null) => void): () => void {
  adminTokenListeners.add(listener)
  return () => adminTokenListeners.delete(listener)
}

export const session = {
  get playerToken() {
    return playerToken
  },
  setPlayerToken(token: string | null) {
    playerToken = token
    write(PLAYER_KEY, token)
  },
  get adminToken() {
    return adminToken
  },
  setAdminToken(token: string | null) {
    adminToken = token
    write(ADMIN_KEY, token)
  },
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

type Options = {
  method?: string
  body?: unknown
  as?: 'player' | 'admin' | 'none'
  headers?: Record<string, string>
  signal?: AbortSignal
  /**
   * How many more times to try if the server cannot be reached or stumbles (a dropped connection,
   * a restart, a 5xx). Only for requests that are safe to repeat – every step of a game is: asking
   * again for the next question, or sending the same answer twice, changes nothing on the server.
   */
  retry?: number
}

const RETRY_WAITS_MS = [400, 1000, 2000, 4000, 6000]

// Screens can show "reconnecting…" while a request is being tried again.
let struggling = 0
const troubleListeners = new Set<(retrying: boolean) => void>()

export function onConnectionTrouble(listener: (retrying: boolean) => void): () => void {
  troubleListeners.add(listener)
  listener(struggling > 0)
  return () => troubleListeners.delete(listener)
}

function trouble(change: 1 | -1) {
  const before = struggling > 0
  struggling = Math.max(0, struggling + change)
  if (before !== struggling > 0) troubleListeners.forEach((listener) => listener(struggling > 0))
}

const pause = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))

function messageFrom(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] }
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : ''
    return field ? `${field}: ${first.msg ?? 'is not valid'}` : (first.msg ?? 'That did not look right.')
  }
  if (status === 0) return 'Could not reach the server. Check your connection and try again.'
  return 'Something went wrong. Please try again.'
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const attempts = 1 + Math.max(0, options.retry ?? 0)
  let flagged = false
  try {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await request<T>(path, options)
      } catch (error) {
        const status = error instanceof ApiError ? error.status : -1
        const worthAnotherGo = status === 0 || status === 502 || status === 503 || status === 504 || status === 500
        if (!worthAnotherGo || attempt >= attempts || options.signal?.aborted) throw error
        if (!flagged) {
          flagged = true
          trouble(1)
        }
        await pause(RETRY_WAITS_MS[Math.min(attempt - 1, RETRY_WAITS_MS.length - 1)])
      }
    }
  } finally {
    if (flagged) trouble(-1)
  }
}

async function request<T>(path: string, options: Options): Promise<T> {
  const headers: Record<string, string> = { ...options.headers }
  const as = options.as ?? 'player'
  if (as === 'player' && playerToken) headers.Authorization = `Bearer ${playerToken}`
  if (as === 'admin' && adminToken) headers.Authorization = `Bearer ${adminToken}`
  // An admin who is signed in may play-test a quiz that is still a draft.
  if (as === 'player' && adminToken) headers['X-Admin-Token'] = adminToken

  let body: BodyInit | undefined
  if (options.body instanceof FormData) body = options.body
  else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await fetch(path, { method: options.method ?? (body ? 'POST' : 'GET'), headers, body, signal: options.signal })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError(messageFrom(null, 0), 0)
  }
  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }
  if (!response.ok) {
    if (response.status === 401 && as === 'admin') session.setAdminToken(null)
    throw new ApiError(messageFrom(payload, response.status), response.status)
  }
  return payload as T
}

export const adminApi = <T = unknown>(path: string, options: Omit<Options, 'as'> = {}) =>
  api<T>(`/api/admin${path}`, { ...options, as: 'admin' })

/** Download a file from an admin endpoint (e.g. the results CSV). */
export async function adminDownload(path: string, filename: string) {
  let response: Response
  try {
    response = await fetch(`/api/admin${path}`, { headers: { Authorization: `Bearer ${adminToken ?? ''}` } })
  } catch {
    throw new ApiError(messageFrom(null, 0), 0)
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    let payload: unknown = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = text
      }
    }
    throw new ApiError(messageFrom(payload, response.status), response.status)
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
