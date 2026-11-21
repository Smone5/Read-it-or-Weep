// Legal YouTube video clip card for question reveals and summary reviews.
// Never downloads or re-hosts video: plays via YouTube's official privacy-enhanced
// embed when permitted, and always provides a direct "Watch on YouTube" link
// (essential for official NFL highlights that restrict third-party iframe embeds).

import { useEffect, useState } from 'react'
import { IconClose, IconExternal, IconPlay } from '../components/Icons'
import { setJukeboxLevel } from '../lib/jukebox'
import type { VideoClip } from '../lib/types'

type Props = {
  video: VideoClip
  compact?: boolean
  restoreLevel?: 'wait' | 'party'
}

export function VideoClipCard({ video, compact = false, restoreLevel = 'wait' }: Props) {
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    setPlaying(false)
  }, [video.videoId, video.startSec])

  useEffect(() => {
    if (!playing) return
    setJukeboxLevel('out')
    return () => setJukeboxLevel(restoreLevel)
  }, [playing, restoreLevel])

  const canEmbed = Boolean(video.embedUrl)

  if (playing && video.embedUrl) {
    return (
      <div className="mt-3 overflow-hidden rounded-[6px] border border-line bg-black">
        <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-xs">
          <span className="truncate font-semibold">{video.title}</span>
          <div className="flex flex-none items-center gap-2">
            <a
              href={video.watchUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
            >
              YouTube <IconExternal size={12} />
            </a>
            <button
              type="button"
              onClick={() => setPlaying(false)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted hover:text-ink"
              aria-label="Close video player"
            >
              <IconClose size={12} />
            </button>
          </div>
        </div>
        <div className="relative aspect-video w-full bg-black">
          <iframe
            src={video.embedUrl}
            title={video.title}
            className="absolute inset-0 h-full w-full"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
          />
        </div>
      </div>
    )
  }

  return (
    <div
      className={`mt-3 flex items-center gap-3 rounded-[6px] border border-line bg-surface-2 ${compact ? 'p-2' : 'p-2.5'}`}
    >
      {canEmbed ? (
        <button
          type="button"
          onClick={() => setPlaying(true)}
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
        <div className="mt-1 flex flex-wrap items-center gap-3 text-xs">
          {canEmbed && (
            <button
              type="button"
              onClick={() => setPlaying(true)}
              className="inline-flex items-center gap-1 font-semibold hover:underline"
              style={{ color: 'var(--accent-text)' }}
            >
              <IconPlay size={11} /> Play clip here
            </button>
          )}
          <a
            href={video.watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium text-muted hover:text-ink hover:underline"
          >
            {canEmbed ? 'Open on YouTube' : 'Watch official clip on YouTube'} <IconExternal size={12} />
          </a>
        </div>
      </div>
    </div>
  )
}
