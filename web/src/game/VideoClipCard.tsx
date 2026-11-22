// Legal YouTube video clip card for question reveals and summary reviews.
// Never downloads or re-hosts video: plays via YouTube's official privacy-enhanced
// embed when permitted, and always provides a direct "Watch on YouTube" link
// (essential for official NFL highlights that restrict third-party iframe embeds).

import { useEffect, useRef, useState } from 'react'
import { IconClose, IconExternal, IconPlay } from '../components/Icons'
import { setJukeboxVideoPlaying } from '../lib/jukebox'
import type { VideoClip } from '../lib/types'

type Props = {
  video: VideoClip
  compact?: boolean
  onPlayStart?: () => void
  /** Optional hook so clipped layouts (e.g. big-screen HostScreen) can open the clip in a Theater modal instead of inline. */
  onOpenTheater?: () => void
  /** Start directly in the embedded iframe (used inside the Theater modal). */
  autoPlay?: boolean
}

const YT_ORIGINS = new Set(['https://www.youtube-nocookie.com', 'https://www.youtube.com'])

export function VideoClipCard({ video, compact = false, onPlayStart, onOpenTheater, autoPlay = false }: Props) {
  const [playing, setPlaying] = useState(autoPlay)
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const lockedRef = useRef(false)

  const setLock = (want: boolean) => {
    if (want === lockedRef.current) return
    lockedRef.current = want
    setJukeboxVideoPlaying(want)
  }

  useEffect(() => {
    setPlaying(autoPlay)
  }, [video.videoId, video.startSec, autoPlay])

  useEffect(() => {
    if (!playing) {
      setLock(false)
      return
    }
    onPlayStart?.()
    setLock(true)

    const onMessage = (event: MessageEvent) => {
      if (!YT_ORIGINS.has(event.origin)) return
      if (iframeRef.current && event.source !== iframeRef.current.contentWindow) return
      let data: unknown = event.data
      if (typeof data === 'string') {
        try {
          data = JSON.parse(data)
        } catch {
          return
        }
      }
      if (!data || typeof data !== 'object') return
      const payload = data as { event?: string; info?: { playerState?: number } | number }
      const state =
        payload.event === 'onStateChange' && typeof payload.info === 'number'
          ? payload.info
          : payload.event === 'infoDelivery' && payload.info && typeof payload.info === 'object'
            ? payload.info.playerState
            : undefined
      if (state === 0 || state === 2) setLock(false) // ended or paused -> resume soundtrack
      else if (state === 1 || state === 3) {
        onPlayStart?.()
        setLock(true)
      }
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      setLock(false)
    }
  }, [playing]) // eslint-disable-line react-hooks/exhaustive-deps

  const canEmbed = Boolean(video.embedUrl)

  if (playing && video.embedUrl) {
    return (
      <div className="relative z-[41] mt-3 overflow-hidden rounded-[6px] border border-line bg-black">
        <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1 text-xs">
          <span className="truncate font-semibold">{video.title}</span>
          <div className="flex flex-none items-center gap-1.5">
            <a
              href={video.watchUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => onPlayStart?.()}
              className="inline-flex min-h-[44px] items-center gap-1 rounded px-2.5 text-xs font-semibold hover:underline"
              style={{ color: 'var(--accent-text)' }}
            >
              YouTube <IconExternal size={12} />
            </a>
            {!autoPlay && (
              <button
                type="button"
                onClick={() => setPlaying(false)}
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded border border-line bg-surface text-muted hover:text-ink"
                aria-label="Close video player"
                title="Close video and resume music"
              >
                <IconClose size={14} />
              </button>
            )}
          </div>
        </div>
        <div className="relative aspect-video w-full bg-black">
          <iframe
            ref={iframeRef}
            src={video.embedUrl}
            title={video.title}
            className="absolute inset-0 h-full w-full"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            onLoad={() => {
              try {
                const win = iframeRef.current?.contentWindow
                win?.postMessage(JSON.stringify({ event: 'listening' }), 'https://www.youtube-nocookie.com')
                win?.postMessage(
                  JSON.stringify({ event: 'command', func: 'addEventListener', args: ['onStateChange'] }),
                  'https://www.youtube-nocookie.com',
                )
              } catch {
                /* cross-origin handshake optional */
              }
            }}
          />
        </div>
      </div>
    )
  }

  const handlePlay = () => {
    if (onOpenTheater) onOpenTheater()
    else setPlaying(true)
  }

  return (
    <div
      className={`relative z-[41] mt-3 flex items-center gap-3 rounded-[6px] border border-line bg-surface-2 ${compact ? 'p-2' : 'p-2.5'}`}
    >
      {canEmbed ? (
        <button
          type="button"
          onClick={handlePlay}
          className="group relative flex-none overflow-hidden rounded-[4px] border border-line bg-black focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          style={{ width: compact ? 88 : 112, height: compact ? 50 : 63 }}
          aria-label={`Play ${video.title}`}
        >
          <img src={video.thumbnailUrl} alt="" loading="lazy" className="h-full w-full object-cover opacity-85 transition group-hover:opacity-100" />
          <span className="absolute inset-0 grid place-items-center bg-black/35 transition group-hover:bg-black/20">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-accent text-black shadow">
              <IconPlay size={13} />
            </span>
          </span>
        </button>
      ) : (
        <a
          href={video.watchUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => onPlayStart?.()}
          className="group relative flex-none overflow-hidden rounded-[4px] border border-line bg-black"
          style={{ width: compact ? 88 : 112, height: compact ? 50 : 63 }}
          aria-label={`Watch ${video.title} on YouTube`}
        >
          <img src={video.thumbnailUrl} alt="" loading="lazy" className="h-full w-full object-cover opacity-85 transition group-hover:opacity-100" />
          <span className="absolute inset-0 grid place-items-center bg-black/35 transition group-hover:bg-black/20">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-accent text-black shadow">
              <IconPlay size={13} />
            </span>
          </span>
        </a>
      )}
      <div className="min-w-0 flex-1">
        <p className="pixel text-[9px] text-muted">VIDEO REPLAY · YOUTUBE</p>
        <p className="mt-0.5 truncate text-sm font-semibold">{video.title}</p>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {canEmbed && (
            <button
              type="button"
              onClick={() => setPlaying(true)}
              className="inline-flex min-h-[36px] items-center gap-1 py-1 font-semibold hover:underline"
              style={{ color: 'var(--accent-text)' }}
            >
              <IconPlay size={11} /> Play clip here
            </button>
          )}
          <a
            href={video.watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => onPlayStart?.()}
            className="inline-flex min-h-[36px] items-center gap-1 py-1 font-medium text-muted hover:text-ink hover:underline"
          >
            {canEmbed ? 'Open on YouTube' : 'Watch official clip on YouTube'} <IconExternal size={12} />
          </a>
        </div>
      </div>
    </div>
  )
}
