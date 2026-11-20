// Thin wrapper over the shared client: the same calls, plus one place that notices an
// expired session so the whole admin can return to the sign-in screen.

import { adminApi, adminDownload, ApiError, session } from '../lib/api'

type CallOptions = { method?: string; body?: unknown; signal?: AbortSignal }

let unauthorizedHandler: (() => void) | null = null

/** Called by AdminApp. The handler runs whenever a signed-in call is refused with 401. */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler
}

/** Call the admin API. `path` is relative to /api/admin. Throws ApiError with a readable message. */
export async function call<T>(path: string, options: CallOptions = {}): Promise<T> {
  try {
    return await adminApi<T>(path, options)
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) unauthorizedHandler?.()
    throw error
  }
}

/** Download a file from the admin API (the shared helper does not clear an expired session itself). */
export async function download(path: string, filename: string): Promise<void> {
  try {
    await adminDownload(path, filename)
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      session.setAdminToken(null)
      unauthorizedHandler?.()
    }
    throw error
  }
}

export function isAbort(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'AbortError'
}

/** A message fit to show a person. The shared client already words API errors plainly. */
export function errorText(error: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (error instanceof ApiError && error.message) return error.message
  return fallback
}
