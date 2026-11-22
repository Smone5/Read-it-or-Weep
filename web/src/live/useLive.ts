// Follow a live game. A streamed connection delivers each change the moment it happens;
// if the stream drops (flaky phone signal, strict office proxy) we fall back to asking
// every couple of seconds, so a player is never left staring at a stale screen.

import { useCallback, useEffect, useRef, useState } from 'react'
import { session } from '../lib/api'
import type { LiveState } from '../lib/types'

type Options = { code: string; hostKey?: string | null; enabled: boolean }

export function useLive({ code, hostKey, enabled }: Options) {
  const [state, setState] = useState<LiveState | null>(null)
  const [error, setError] = useState('')
  const [connected, setConnected] = useState(false)
  const offset = useRef(0) // server clock minus this device's clock, in ms
  const lastMessage = useRef(0)
  const latestVersion = useRef(-1)

  const headers = useCallback((): Record<string, string> => {
    const result: Record<string, string> = {}
    if (hostKey) result['X-Host-Key'] = hostKey
    else if (session.playerToken) result.Authorization = `Bearer ${session.playerToken}`
    return result
  }, [hostKey])

  const accept = useCallback((next: LiveState) => {
    if (next.version < latestVersion.current) return
    latestVersion.current = next.version
    offset.current = new Date(next.serverNow).getTime() - Date.now()
    lastMessage.current = Date.now()
    setState(next)
    setError('')
  }, [])

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/live/${code}/state`, { headers: headers() })
      if (response.status === 404) {
        setError('No live game with that code.')
        return
      }
      if (response.status === 401 || response.status === 403) {
        setError('Join the live game to follow along, or ask the host for a fresh link.')
        return
      }
      if (response.ok) accept((await response.json()) as LiveState)
    } catch {
      /* offline for a moment; the next poll will try again */
    }
  }, [code, headers, accept])

  useEffect(() => {
    latestVersion.current = -1
    lastMessage.current = 0
  }, [code])

  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let controller: AbortController | null = null
    let attempt = 0
    let wakeReconnect: (() => void) | null = null

    const sleepWithWake = (ms: number) =>
      new Promise<void>((resolve) => {
        const id = window.setTimeout(() => {
          wakeReconnect = null
          resolve()
        }, ms)
        wakeReconnect = () => {
          window.clearTimeout(id)
          wakeReconnect = null
          resolve()
        }
      })

    const onResume = () => {
      if (document.hidden) return
      void refresh()
      wakeReconnect?.()
    }
    window.addEventListener('online', onResume)
    document.addEventListener('visibilitychange', onResume)

    const stream = async () => {
      while (!stopped) {
        controller = new AbortController()
        try {
          const response = await fetch(`/api/live/${code}/events`, { headers: headers(), signal: controller.signal })
          if (response.status === 404) {
            setError('No live game with that code.')
            return
          }
          if (response.status === 401 || response.status === 403) {
            setError('Session access expired or was taken over on another screen. Refresh or rejoin to continue.')
            return
          }
          if (!response.ok || !response.body) throw new Error('stream unavailable')
          attempt = 0
          setConnected(true)
          lastMessage.current = Date.now()
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          for (;;) {
            const { value, done } = await reader.read()
            if (done) break
            lastMessage.current = Date.now()
            buffer += decoder.decode(value, { stream: true })
            let boundary = buffer.indexOf('\n\n')
            while (boundary >= 0) {
              const chunk = buffer.slice(0, boundary)
              buffer = buffer.slice(boundary + 2)
              if (chunk.trim().startsWith(':')) {
                lastMessage.current = Date.now()
              } else {
                const data = chunk
                  .split('\n')
                  .filter((line) => line.startsWith('data: '))
                  .map((line) => line.slice(6))
                  .join('')
                if (data) accept(JSON.parse(data) as LiveState)
              }
              boundary = buffer.indexOf('\n\n')
            }
          }
        } catch {
          /* fall through to reconnect */
        }
        setConnected(false)
        if (!stopped) {
          const base = Math.min(15000, 1000 * 2 ** Math.min(attempt, 4))
          const jitter = Math.round(Math.random() * 600)
          attempt += 1
          await sleepWithWake(base + jitter)
        }
      }
    }

    void refresh()
    void stream()
    const poll = window.setInterval(() => {
      if (Date.now() - lastMessage.current > 6000) void refresh()
    }, 2500)
    return () => {
      stopped = true
      wakeReconnect?.()
      controller?.abort()
      window.clearInterval(poll)
      window.removeEventListener('online', onResume)
      document.removeEventListener('visibilitychange', onResume)
    }
  }, [code, enabled, headers, refresh, accept])

  /** Milliseconds until a server timestamp, corrected for this device's clock. */
  const until = useCallback((iso: string | null | undefined) => (iso ? new Date(iso).getTime() - (Date.now() + offset.current) : 0), [])

  return { state, error, connected, refresh, until, headers }
}

/** Re-render on a steady beat while a countdown is on screen. */
export function useTicker(active: boolean, everyMs = 200) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setTick((n) => n + 1), everyMs)
    return () => window.clearInterval(id)
  }, [active, everyMs])
}
