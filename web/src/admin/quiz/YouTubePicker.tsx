// Reusable Admin YouTube clip picker with live thumbnail preview, auto-title lookup,
// and a simple "Open on YouTube instead of playing inline" toggle (replacing #external).

import { useEffect, useRef, useState } from 'react'
import { IconExternal, IconPlay } from '../../components/Icons'
import { call, errorText } from '../api'
import { Field, Note, Switch } from '../components'
import type { VideoInspectResult } from '../types'

export function splitVideoUrl(raw: string | null | undefined): { url: string; externalOnly: boolean } {
  const value = (raw ?? '').trim()
  if (!value) return { url: '', externalOnly: false }
  const lower = value.toLowerCase()
  if (lower.endsWith('#external')) {
    return { url: value.slice(0, -'#external'.length), externalOnly: true }
  }
  return { url: value, externalOnly: /[?&]noembed=(1|true)\b/i.test(value) }
}

export function combineVideoUrl(url: string, externalOnly: boolean): string {
  const clean = url.trim().replace(/#external$/i, '')
  if (!clean) return ''
  return externalOnly ? `${clean}#external` : clean
}

function extractVideoIdClient(raw: string): string | null {
  try {
    const parsed = new URL(raw.trim())
    const host = parsed.hostname.toLowerCase()
    if (!['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com'].includes(host)) {
      return null
    }
    const parts = parsed.pathname.split('/').filter(Boolean)
    const id = host.endsWith('youtu.be')
      ? parts[0]
      : parts[0] && ['embed', 'shorts', 'live'].includes(parts[0])
        ? parts[1]
        : parsed.searchParams.get('v')
    return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null
  } catch {
    return null
  }
}

type Props = {
  /** Stored URL value (may carry #external internally). */
  value: string
  title: string
  disabled?: boolean
  onChange: (next: { videoUrl: string; videoTitle: string }) => void
}

export function YouTubePicker({ value, title, disabled = false, onChange }: Props) {
  const { url, externalOnly } = splitVideoUrl(value)
  const [looking, setLooking] = useState(false)
  const [info, setInfo] = useState<VideoInspectResult | null>(null)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const lastLooked = useRef<string>(url.trim())
  const lastAutoTitle = useRef<string>('')
  const inspectSeq = useRef(0)

  const previewId = info?.videoId ?? extractVideoIdClient(url)
  const thumbUrl = info?.thumbnailUrl ?? (previewId ? `https://i.ytimg.com/vi/${previewId}/hqdefault.jpg` : null)

  const inspect = async (rawUrl: string, currentTitle: string, preserveExternal = false) => {
    const clean = rawUrl.trim()
    if (!clean || !extractVideoIdClient(clean)) return
    const seq = ++inspectSeq.current
    setLooking(true)
    setLookupError(null)
    lastLooked.current = clean
    try {
      const result = await call<VideoInspectResult>('/video/inspect', {
        method: 'POST',
        body: { url: clean },
      })
      if (seq !== inspectSeq.current) return
      setInfo(result)
      const nextExternal = result.suggestedExternal ? true : preserveExternal ? externalOnly : false
      const fetchedTitle = (result.title ?? '').slice(0, 120)
      const wasAuto = !currentTitle.trim() || currentTitle.trim() === lastAutoTitle.current
      const nextTitle = wasAuto && fetchedTitle ? fetchedTitle : currentTitle
      if (fetchedTitle) lastAutoTitle.current = fetchedTitle
      lastLooked.current = result.watchUrl
      onChange({
        videoUrl: combineVideoUrl(result.watchUrl, nextExternal),
        videoTitle: nextTitle,
      })
    } catch (reason) {
      if (seq !== inspectSeq.current) return
      setInfo(null)
      setLookupError(errorText(reason))
    } finally {
      if (seq === inspectSeq.current) setLooking(false)
    }
  }

  // Re-inspect whenever the pasted YouTube URL changes (even if a title is already filled).
  useEffect(() => {
    const clean = url.trim()
    if (!clean) {
      setInfo(null)
      setLookupError(null)
      lastLooked.current = ''
      lastAutoTitle.current = ''
      return
    }
    if (extractVideoIdClient(clean) && clean !== lastLooked.current) {
      const timer = window.setTimeout(() => void inspect(clean, title, externalOnly), 350)
      return () => window.clearTimeout(timer)
    }
  }, [url]) // eslint-disable-line react-hooks/exhaustive-deps

  const updateUrl = (nextRaw: string) => {
    const nextSplit = splitVideoUrl(nextRaw)
    const sameVideo = extractVideoIdClient(nextSplit.url) === extractVideoIdClient(url)
    const keepExternal = nextSplit.externalOnly || (sameVideo && externalOnly)
    onChange({
      videoUrl: combineVideoUrl(nextSplit.url, keepExternal),
      videoTitle: title,
    })
  }

  const toggleExternal = (nextExternal: boolean) => {
    onChange({
      videoUrl: combineVideoUrl(url, nextExternal),
      videoTitle: title,
    })
  }

  return (
    <div className="grid gap-3 rounded-lg border border-line bg-surface-2/60 p-3.5">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="YouTube clip link (optional)"
          hint="Paste any youtube.com or youtu.be link (optional &t=45 timestamp supported)."
        >
          {(props) => (
            <div className="flex gap-2">
              <input
                className="field min-w-0 flex-1"
                type="url"
                placeholder="https://www.youtube.com/watch?v=..."
                {...props}
                value={url}
                disabled={disabled}
                onChange={(event) => updateUrl(event.target.value)}
                maxLength={290}
              />
              <button
                type="button"
                className="btn btn-ghost btn-sm flex-none"
                disabled={disabled || looking || !extractVideoIdClient(url)}
                onClick={() => void inspect(url, title, true)}
              >
                {looking ? 'Checking…' : 'Preview'}
              </button>
            </div>
          )}
        </Field>
        <Field label="Clip title (optional)" hint="Short label on the replay card, e.g. 'Elway Helicopter Dive (SB XXXII)'.">
          {(props) => (
            <input
              className="field"
              {...props}
              value={title}
              disabled={disabled}
              onChange={(event) => onChange({ videoUrl: value, videoTitle: event.target.value })}
              maxLength={120}
            />
          )}
        </Field>
      </div>

      {lookupError && <Note tone="warn">{lookupError}</Note>}

      {thumbUrl && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface p-2.5">
          <div className="relative h-[54px] w-[96px] flex-none overflow-hidden rounded border border-line bg-black">
            <img src={thumbUrl} alt="" className="h-full w-full object-cover" />
            <span className="absolute inset-0 grid place-items-center bg-black/30">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-accent text-black">
                <IconPlay size={11} />
              </span>
            </span>
          </div>
          <div className="min-w-0 flex-1 text-sm">
            <p className="truncate font-semibold">{title.trim() || info?.title || 'YouTube clip ready'}</p>
            <p className="mt-0.5 text-xs text-muted">
              {info?.author ? `${info.author} · ` : ''}
              {externalOnly ? 'Opens on YouTube in a new tab' : 'Plays inline inside the game'}
            </p>
            <a
              href={info?.watchUrl ?? url}
              target="_blank"
              rel="noopener noreferrer"
              className="ad-link mt-1 inline-flex items-center gap-1 text-xs"
            >
              Test on YouTube <IconExternal size={11} />
            </a>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={disabled}
            onClick={() => {
              setInfo(null)
              setLookupError(null)
              onChange({ videoUrl: '', videoTitle: '' })
            }}
          >
            Remove
          </button>
        </div>
      )}

      {info?.note && <Note tone="neutral">{info.note}</Note>}

      {url.trim() && (
        <Switch
          checked={externalOnly}
          onChange={toggleExternal}
          label="Open on YouTube instead of playing inline"
          description="Turn on for official NFL or sports channels that disable third-party embeds."
          disabled={disabled}
        />
      )}
    </div>
  )
}
