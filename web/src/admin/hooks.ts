import { useCallback, useEffect, useRef, useState } from 'react'
import { call, errorText, isAbort } from './api'
import type { Meta } from './types'

type LoadState<T> = { path: string | null; data: T | null; error: string | null; loading: boolean }

export type Loaded<T> = {
  data: T | null
  error: string | null
  loading: boolean
  /** Fetch again and show the loading state (for a "Try again" button). */
  reload: () => void
  /** Fetch again quietly, keeping what is on screen until the new data arrives. */
  refresh: () => Promise<void>
  /** Change the loaded data in place (after a small edit the server has already accepted). */
  mutate: (update: (data: T) => T) => void
}

/** GET an admin endpoint and keep the result, with loading and error states. Pass null to wait. */
export function useLoad<T>(path: string | null): Loaded<T> {
  const [state, setState] = useState<LoadState<T>>({ path, data: null, error: null, loading: path !== null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (path === null) return
    const controller = new AbortController()
    setState((previous) => ({ path, data: previous.path === path ? previous.data : null, error: null, loading: true }))
    call<T>(path, { signal: controller.signal }).then(
      (data) => setState({ path, data, error: null, loading: false }),
      (error: unknown) => {
        if (isAbort(error)) return
        setState((previous) => ({
          path,
          data: previous.path === path ? previous.data : null,
          error: errorText(error),
          loading: false,
        }))
      },
    )
    return () => controller.abort()
  }, [path, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])

  const refresh = useCallback(async () => {
    if (path === null) return
    const data = await call<T>(path)
    setState((previous) => (previous.path === path ? { path, data, error: null, loading: false } : previous))
  }, [path])

  const mutate = useCallback((update: (data: T) => T) => {
    setState((previous) => (previous.data === null ? previous : { ...previous, data: update(previous.data) }))
  }, [])

  const current = state.path === path
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: path !== null && (!current || state.loading),
    reload,
    refresh,
    mutate,
  }
}

// The lists of play styles, decades, voices and file types never change while the app is
// open, so every screen shares one request.
let metaRequest: Promise<Meta> | null = null

function loadMeta(): Promise<Meta> {
  if (!metaRequest) {
    metaRequest = call<Meta>('/meta').catch((error: unknown) => {
      metaRequest = null
      throw error
    })
  }
  return metaRequest
}

export function forgetMeta() {
  metaRequest = null
}

export function useMeta(): { meta: Meta | null; error: string | null; retry: () => void } {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    setError(null)
    loadMeta().then(
      (value) => {
        if (active) setMeta(value)
      },
      (reason: unknown) => {
        if (active) setError(errorText(reason))
      },
    )
    return () => {
      active = false
    }
  }, [attempt])

  return { meta, error, retry: useCallback(() => setAttempt((n) => n + 1), []) }
}

/** Run `callback` every `delayMs` while `delayMs` is a number. Always calls the latest callback. */
export function useInterval(callback: () => void, delayMs: number | null) {
  const latest = useRef(callback)
  useEffect(() => {
    latest.current = callback
  }, [callback])
  useEffect(() => {
    if (delayMs === null) return
    const timer = window.setInterval(() => latest.current(), delayMs)
    return () => window.clearInterval(timer)
  }, [delayMs])
}
