// Building blocks shared by the solo game, the summary and the live screens.

import { useEffect, useRef, useState } from 'react'
import { HostCrt, type HostCrtState } from '../components/HostCrt'
import { IconBolt, IconCheck, IconClose, IconCrown, IconExpand, IconHeart, IconMic, IconPause, IconPlay, IconStar, IconTrophy } from '../components/Icons'
import { Meter } from '../components/ui'
import { PictureViewer, ZoomImage } from '../components/ZoomImage'
import type { TalkState } from '../lib/hostVoice'
import { musicElement, musicFading, playClip, resumeClip, stopMusic } from '../lib/music'
import { play, buzz } from '../lib/sound'
import { trace } from '../lib/trace'
import type { ItemImage, Leaderboard, LeaderboardEntry, Readiness, RunState } from '../lib/types'

export const TONES = ['var(--a0)', 'var(--a1)', 'var(--a2)', 'var(--a3)']
export const TONE_INKS = ['var(--a0-ink)', 'var(--a1-ink)', 'var(--a2-ink)', 'var(--a3-ink)']
export const LETTERS = ['A', 'B', 'C', 'D']

// --------------------------------------------------------------------------- host

export type TalkControl = {
  state: TalkState
  problem?: string
  onPress: () => void
  onRelease: () => void
}

const TAP_MS = 400 // a press shorter than this is a tap: the microphone stays open until the next tap
const LONGEST_TURN_MS = 15000 // ...or until this long has passed

/**
 * The host's dialogue box with an animated retro CRT monitor and hold/tap microphone control.
 */
export function HostBox({
  name,
  line,
  speaking,
  talk,
  mood = 'idle',
  tone = 'accent',
  idleHint,
}: {
  name: string
  line: string
  speaking: boolean
  talk?: TalkControl | null
  mood?: 'idle' | 'happy' | 'wince'
  tone?: 'accent' | 'brand' | 'cyan' | 'rose'
  idleHint?: string
}) {
  const pressedAt = useRef(0)
  const pressing = useRef(false)
  const limit = useRef<number | undefined>(undefined)
  const [tapped, setTapped] = useState(false) // listening because of a tap, not a hold
  const state = talk?.state ?? 'idle'

  // Whatever ends the listening (sent, refused, lost connection) also ends the tap.
  useEffect(() => {
    if (state !== 'listening') {
      setTapped(false)
      window.clearTimeout(limit.current)
    }
  }, [state])
  useEffect(() => () => window.clearTimeout(limit.current), [])

  const press = () => {
    if (!talk) return
    if (state === 'listening' && tapped) {
      talk.onRelease() // the second tap: send it
      return
    }
    if (state !== 'idle') return
    pressing.current = true
    pressedAt.current = performance.now()
    talk.onPress()
  }
  const release = () => {
    if (!talk || !pressing.current) return
    pressing.current = false
    if (performance.now() - pressedAt.current >= TAP_MS) {
      talk.onRelease() // held and let go
      return
    }
    setTapped(true)
    window.clearTimeout(limit.current)
    limit.current = window.setTimeout(() => talk.onRelease(), LONGEST_TURN_MS)
  }

  if (!line && !speaking && !talk) return <div className="h-0" aria-hidden />
  const listening = state === 'listening'
  const thinking = state === 'thinking'
  const crtState: HostCrtState = listening
    ? 'listening'
    : mood !== 'idle' && !speaking
      ? mood
      : speaking
        ? 'speaking'
        : thinking
          ? 'thinking'
          : mood !== 'idle'
            ? mood
            : 'idle'

  let words: React.ReactNode = line || '…'
  if (listening) words = <span style={{ color: 'var(--accent)' }}>{tapped ? 'Listening… tap the mic again to send.' : 'Listening… let go when you are done.'}</span>
  else if (!line && thinking) words = <span className="text-white/60">…</span>
  else if (!line && !speaking) {
    words = (
      <span className="text-white/60">
        {talk?.problem ?? idleHint ?? `Tap the mic to talk to ${name}: heckle, ask your score, or have the question read out.`}
      </span>
    )
  }
  return (
    <div className="dialogue rise flex items-center gap-3 px-3 pb-2.5 pt-3.5 text-[15px] leading-snug sm:gap-3.5 sm:px-3.5 sm:text-base">
      <span className="who">{name}</span>
      <HostCrt state={crtState} mood={mood} tone={tone} />
      <p className="min-w-0 flex-1" aria-live="polite">
        <span className={speaking && !listening ? 'caret' : ''}>{words}</span>
      </p>
      {talk && (
        <button
          type="button"
          className="mic"
          data-on={listening}
          aria-pressed={listening}
          aria-label={listening ? `Send what you said to ${name}` : `Talk to ${name}`}
          title={`Talk to ${name}`}
          onPointerDown={(event) => {
            event.preventDefault()
            try {
              event.currentTarget.setPointerCapture(event.pointerId)
            } catch {
              /* nothing to capture */
            }
            press()
          }}
          onPointerUp={release}
          onPointerCancel={release}
          onLostPointerCapture={release}
          onContextMenu={(event) => event.preventDefault()}
          onKeyDown={(event) => {
            if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
              event.preventDefault()
              press()
            }
          }}
          onKeyUp={(event) => (event.key === ' ' || event.key === 'Enter') && release()}
        >
          <IconMic size={22} />
        </button>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- timer

type TimerProps = {
  /** Changes whenever a new countdown should start. */
  timerKey: string
  totalMs: number
  remainingMs: number
  running: boolean
  /** Keep the bar full and the clock stopped until this is released (a music clip has not started yet). */
  hold?: boolean
  onExpire: () => void
  /** Called as each second ticks over, for anything else that shows the time. */
  onSecond?: (seconds: number) => void
  quiet?: boolean
}

/** An energy-bar countdown. Animates outside React so it stays smooth on slow phones. */
export function TimerBar({ timerKey, totalMs, remainingMs, running, hold = false, onExpire, onSecond, quiet }: TimerProps) {
  const fill = useRef<HTMLDivElement>(null)
  const label = useRef<HTMLSpanElement>(null)
  const expire = useRef(onExpire)
  expire.current = onExpire
  const second = useRef(onSecond)
  second.current = onSecond
  const endsAt = useRef(0)
  const frozenAt = useRef<number | null>(null)

  useEffect(() => {
    endsAt.current = performance.now() + remainingMs // counted from the moment the hold is released
    frozenAt.current = null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timerKey, hold])

  useEffect(() => {
    if (hold) return
    if (!running) {
      frozenAt.current = Math.max(0, endsAt.current - performance.now())
      return
    }
    if (frozenAt.current !== null) {
      endsAt.current = performance.now() + frozenAt.current
      frozenAt.current = null
    }
    let frame = 0
    let lastSecond = -1
    let done = false
    const tick = () => {
      const left = Math.max(0, endsAt.current - performance.now())
      const fraction = totalMs > 0 ? left / totalMs : 0
      if (fill.current) {
        fill.current.style.transform = `scaleX(${fraction})`
        fill.current.dataset.urgent = String(left <= 5000)
      }
      const seconds = Math.ceil(left / 1000)
      if (seconds !== lastSecond) {
        lastSecond = seconds
        if (label.current) {
          label.current.textContent = String(seconds)
          label.current.style.color = left <= 5000 ? 'var(--bad)' : ''
        }
        second.current?.(seconds)
        if (!quiet && seconds <= 5 && seconds > 0) {
          play(seconds <= 3 ? 'fasttick' : 'tick')
          if (seconds <= 3) buzz(12)
        }
      }
      if (left <= 0) {
        if (!done) {
          done = true
          expire.current()
        }
        return
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [running, hold, timerKey, totalMs, quiet])

  return (
    <div className="flex items-center gap-2.5" role="timer" aria-label="Time left">
      <div className="timer-track flex-1">
        <div ref={fill} className="timer-fill" style={{ transform: `scaleX(${totalMs ? remainingMs / totalMs : 0})` }} />
      </div>
      <span ref={label} className="pixel w-9 text-right text-[11px] tabular-nums">{Math.ceil(remainingMs / 1000)}</span>
    </div>
  )
}

/** Counts a server-provided remaining duration down to zero without trusting client wall-clock skew (Speed Run). */
export function useDeadline(remainingMs: number | undefined, resetKey: string | undefined, onZero: () => void): number {
  const [left, setLeft] = useState(() => (remainingMs !== undefined ? Math.max(0, remainingMs) : 0))
  const callback = useRef(onZero)
  callback.current = onZero
  useEffect(() => {
    if (remainingMs === undefined || !resetKey) return
    const target = performance.now() + Math.max(0, remainingMs)
    let fired = false
    const update = () => {
      const remaining = Math.max(0, target - performance.now())
      setLeft(remaining)
      if (remaining <= 0 && !fired) {
        fired = true
        callback.current()
      }
    }
    update()
    const id = window.setInterval(update, 150)
    return () => window.clearInterval(id)
  }, [remainingMs, resetKey])
  return left
}

// --------------------------------------------------------------------------- answers

type AnswerListProps = {
  options: string[]
  removed?: number[]
  picked: number | null
  correctIndex: number | null
  disabled: boolean
  onPick: (index: number) => void
}

export function AnswerList({ options, removed = [], picked, correctIndex, disabled, onPick }: AnswerListProps) {
  const revealed = correctIndex !== null
  return (
    <div className="grid gap-2.5 sm:grid-cols-2">
      {options.map((option, index) => {
        const gone = removed.includes(index)
        let state = 'idle'
        if (gone) state = 'removed'
        else if (revealed) state = index === correctIndex ? 'right' : index === picked ? 'wrong' : 'dim'
        else if (picked === index) state = 'picked'
        return (
          <button
            key={index}
            className="answer"
            data-state={state}
            style={{ ['--tone' as string]: TONES[index % 4], ['--tone-ink' as string]: TONE_INKS[index % 4] }}
            disabled={disabled || gone}
            onClick={() => onPick(index)}
            aria-label={`Answer ${LETTERS[index]}: ${option}`}
          >
            <span className="badge pixel" aria-hidden>{LETTERS[index]}</span>
            <span className="min-w-0 flex-1 break-words">{option}</span>
            {revealed && index === correctIndex && <IconCheck size={18} style={{ color: 'var(--good)' }} />}
            {revealed && index === picked && index !== correctIndex && <IconClose size={16} style={{ color: 'var(--bad)' }} />}
          </button>
        )
      })}
    </div>
  )
}

export function FactFakeButtons({ picked, correctIndex, disabled, onPick, labels = ['FACT', 'FAKE'] }: Omit<AnswerListProps, 'options' | 'removed'> & { labels?: string[] }) {
  const revealed = correctIndex !== null
  const look = (index: number) => {
    if (!revealed) return picked === index ? 'picked' : 'idle'
    return index === correctIndex ? 'right' : index === picked ? 'wrong' : 'dim'
  }
  return (
    <div className="grid grid-cols-2 gap-3">
      {labels.map((label, index) => (
        <button
          key={label}
          className="answer min-h-[84px] justify-center"
          data-state={look(index)}
          style={{ ['--tone' as string]: index === 0 ? 'var(--good)' : 'var(--bad)' }}
          disabled={disabled}
          onClick={() => onPick(index)}
        >
          <span className="pixel text-sm sm:text-lg" style={{ color: index === 0 ? 'var(--good)' : 'var(--bad)' }}>{label}</span>
        </button>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- pictures + music

type PictureProps = {
  image: ItemImage
  /** While the question's clock is running, so the full-screen view can show it. */
  secondsLeft?: number | null
  /** The question, shown under the full-screen picture so it stays in mind. */
  caption?: string | null
  /** True when mounted in the side-by-side desktop stage. */
  wide?: boolean
}

/**
 * A picture from the report, framed as an exhibit monitor above the scanlines.
 * It can be pinched larger right where it is, and a tap opens it full screen.
 */
export function PictureView({ image, secondsLeft, caption, wide = false }: PictureProps) {
  const [open, setOpen] = useState(false)
  const initialSize = image.width && image.height ? { w: image.width, h: image.height } : null
  const [size, setSize] = useState<{ w: number; h: number } | null>(initialSize)
  const holder = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState({ w: 0, h: 0 })
  const frame = { borderColor: 'var(--accent)', boxShadow: '0 0 0 2px #000, 4px 4px 0 2px #000' }

  useEffect(() => {
    const measure = () => setRoom({ w: holder.current?.clientWidth ?? 0, h: window.innerHeight })
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  // A wide chart squeezed to a phone's width comes out a centimetre or two tall, its labels unreadable.
  // When that would happen the frame is made taller and the picture fills it: readable straight away,
  // with a sideways swipe for the rest (and full screen one tap away for the whole thing at once).
  const fitted = size && room.w ? (room.w * size.h) / size.w : 0
  const wanted = Math.min(room.h * 0.36, size?.h ?? 0)
  const strip = !wide && fitted > 0 && fitted < wanted * 0.8

  if (image.mode === 'memory') {
    return (
      <div className="picture-frame overflow-hidden rounded-[5px] border-2 bg-white" style={frame}>
        <div className="exhibit-bar">
          <span className="pixel text-[10px]" style={{ color: 'var(--accent)' }}>Exhibit · Memory</span>
        </div>
        <div className="relative">
          <img
            src={image.url}
            alt=""
            width={image.width ?? undefined}
            height={image.height ?? undefined}
            className={`mx-auto w-full object-contain ${wide ? 'max-h-[56dvh]' : 'max-h-[38dvh]'}`}
          />
          <span className="pixel absolute inset-x-0 bottom-0 bg-black/80 px-2.5 py-2 text-center text-[10px] text-white">From memory – no peeking</span>
        </div>
      </div>
    )
  }

  return (
    <>
      <div ref={holder} className="picture-frame overflow-hidden rounded-[5px] border-2 bg-white" style={frame}>
        <div className="exhibit-bar">
          <span className="text-xs font-bold tracking-widest uppercase" style={{ color: 'var(--accent)' }}>
            Report Exhibit
          </span>
          <button
            type="button"
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-[3px] border border-white/25 bg-white/10 px-3 text-xs font-bold tracking-wider text-white uppercase hover:bg-white/20 sm:min-h-[36px]"
            onClick={() => setOpen(true)}
            aria-label="Open the picture full screen"
            title="Full screen"
          >
            <IconExpand size={13} />
            <span>Zoom</span>
          </button>
        </div>
        <ZoomImage
          src={image.url}
          alt={image.alt}
          cover={strip}
          onTap={() => setOpen(true)}
          onSize={setSize}
          className="w-full"
          style={
            strip
              ? { height: wanted }
              : {
                  aspectRatio: size ? `${size.w} / ${size.h}` : '16 / 9',
                  maxHeight: wide ? '62dvh' : '46dvh',
                }
          }
        />
      </div>
      {open && <PictureViewer src={image.url} alt={image.alt} secondsLeft={secondsLeft} caption={caption} onClose={() => setOpen(false)} />}
    </>
  )
}

const SILENT_CLIP_MS = 7000 // how long a clip may stay silent before it counts as broken

type MusicPlayerProps = {
  src: string
  /** False once the question has been answered: the clip plays on, but there is nothing left to guess. */
  active: boolean
  onBroken: () => void
  /** The clip is actually coming out of the speaker (so the question's clock can start). */
  onPlaying?: () => void
  /** The clip has played to its end (true) - or been started again from there (false). */
  onClipOver?: (over: boolean) => void
}

export function MusicPlayer({ src, active, onBroken, onPlaying, onClipOver }: MusicPlayerProps) {
  const [playing, setPlaying] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const broken = useRef(onBroken)
  broken.current = onBroken
  const started = useRef(onPlaying)
  started.current = onPlaying
  const over = useRef(onClipOver)
  over.current = onClipOver

  useEffect(() => {
    const audio = musicElement()
    const onPlay = () => {
      setPlaying(true)
      setBlocked(false)
      over.current?.(false)
    }
    const onEnd = () => {
      setPlaying(false)
      trace('clip.ended')
      over.current?.(true)
    }
    const onSound = () => {
      trace('clip.heard')
      started.current?.()
    }
    // Only a refusal by the browser means "needs a tap"; an interrupted start does not.
    const refused = (error: unknown) => (error as Error)?.name === 'NotAllowedError' && setBlocked(true)
    const onPause = () => setPlaying(false)
    let live = true
    const onError = () => {
      const failure = audio.error?.code
      const genuine = failure === MediaError.MEDIA_ERR_NETWORK || failure === MediaError.MEDIA_ERR_DECODE || failure === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
      if (live && genuine && audio.currentSrc === src) broken.current()
    }
    audio.addEventListener('play', onPlay)
    audio.addEventListener('playing', onSound)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnd)
    audio.addEventListener('error', onError)
    if (audio.dataset.clip === src && audio.error) onError()
    else playClip(src).catch(refused)
    const watchdog = window.setTimeout(() => {
      const waitingForTap = audio.paused && audio.dataset.clip === src && !audio.error
      if (live && !heard && !waitingForTap) {
        trace('clip.silent')
        broken.current()
      }
    }, SILENT_CLIP_MS)
    const onHeard = () => {
      heard = true
    }
    let heard = false
    audio.addEventListener('playing', onHeard)
    return () => {
      window.clearTimeout(watchdog)
      audio.removeEventListener('playing', onHeard)
      live = false
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('playing', onSound)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnd)
      audio.removeEventListener('error', onError)
      if (!musicFading()) stopMusic()
    }
  }, [src])

  const toggle = () => {
    const audio = musicElement()
    if (audio.paused) {
      setBlocked(false)
      void resumeClip().catch((error: unknown) => (error as Error)?.name === 'NotAllowedError' && setBlocked(true))
    } else audio.pause()
  }

  return (
    <div className="card card-accent flex items-center gap-4 p-4">
      <div className="eq" data-on={playing} aria-hidden>
        <i /><i /><i /><i /><i />
      </div>
      <div className="min-w-0 flex-1">
        <p className="pixel neon-accent text-[10px] sm:text-xs">Now playing</p>
        <p className="mt-1 text-muted">{blocked ? 'Tap play to hear the clip.' : !playing ? 'Paused.' : active ? 'Listen closely…' : 'Turn it up.'}</p>
      </div>
      <button className={`btn btn-accent ${blocked ? 'animate-pulse' : ''}`} onClick={toggle} aria-label={playing ? 'Pause the clip' : 'Play the clip'}>
        {playing ? <IconPause size={16} /> : <IconPlay size={16} />}
      </button>
    </div>
  )
}

// --------------------------------------------------------------------------- HUD

/** Smooth arcade odometer that rolls toward the latest score (instant when reduced motion is enabled). */
export function RollingScore({
  value,
  from,
  pad = 6,
  format = 'pad',
  durationMs = 420,
}: {
  value: number
  from?: number
  pad?: number
  format?: 'pad' | 'comma'
  durationMs?: number
}) {
  const initial = from !== undefined ? from : value
  const [shown, setShown] = useState(initial)
  const fromRef = useRef(initial)

  useEffect(() => {
    const startVal = fromRef.current
    if (startVal === value) return
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      fromRef.current = value
      setShown(value)
      return
    }
    const startTime = performance.now()
    let frame = 0
    const step = (now: number) => {
      const t = Math.min(1, (now - startTime) / durationMs)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = Math.round(startVal + (value - startVal) * eased)
      fromRef.current = next
      setShown(next)
      if (t < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [value, durationMs])

  const safe = Math.max(0, shown)
  return <>{format === 'comma' ? safe.toLocaleString() : String(safe).padStart(pad, '0')}</>
}

export function Hud({ state, clockMs, position }: { state: RunState; clockMs?: number; position?: string }) {
  if (state.mode === 'straight') {
    const answered = state.reportAnswered ?? state.answered
    const correct = state.reportCorrect ?? state.correct
    const pct = answered > 0 ? Math.round((100 * correct) / answered) : null
    const passMark = state.passMark ?? 80
    return (
      <div className="flex min-w-0 flex-wrap items-end justify-between gap-x-3 gap-y-1.5">
        <div className="hud">
          Correct
          <b className="tabular-nums" style={{ color: pct !== null && pct >= passMark ? 'var(--good)' : 'var(--accent-text)' }}>
            {correct}/{answered}
          </b>
        </div>
        <div className="hud text-center">
          Pass mark<b className="tabular-nums">{passMark}%</b>
        </div>
        {position && (
          <div className="hud text-right">
            Question<b>{position}</b>
          </div>
        )}
      </div>
    )
  }
  return (
    <div className="flex min-w-0 flex-wrap items-end justify-between gap-x-3 gap-y-1.5">
      <div className="hud">
        Score<b className="neon-accent tabular-nums"><RollingScore value={state.score} pad={6} /></b>
      </div>
      {state.mode === 'sudden_death' && (
        <div className="hud text-center">
          Lives
          <b className="flex justify-center gap-1 pt-0.5">
            {Array.from({ length: state.maxStrikes ?? 3 }, (_, i) => {
              const lost = i >= (state.maxStrikes ?? 3) - (state.strikes ?? 0)
              return (
                <span key={i} className="heart" data-lost={lost} aria-hidden>
                  <IconHeart size={16} />
                </span>
              )
            })}
          </b>
        </div>
      )}
      {state.mode === 'speed' && clockMs !== undefined && (
        <div className="hud text-center">
          Time<b className={clockMs < 10_000 ? 'blink' : ''} style={{ color: clockMs < 10_000 ? 'var(--bad)' : undefined }}>{(clockMs / 1000).toFixed(1)}</b>
        </div>
      )}
      {state.streak >= 2 && (
        <div className="hud text-center">
          Streak
          <b className="inline-flex items-center gap-1" style={{ color: 'var(--accent-text)' }}>
            <IconBolt size={12} />x{state.streak}
          </b>
        </div>
      )}
      {position && (
        <div className="hud text-right">
          {state.mode === 'show' || state.mode === 'jukebox' ? 'Question' : 'Answered'}<b>{position}</b>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- readiness + leaderboard

export function ReadinessMap({ readiness, compact, listOnly }: { readiness: Readiness; compact?: boolean; listOnly?: boolean }) {
  if (!readiness.total) return null
  return (
    <div>
      {!listOnly && (
        <>
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="pixel text-[10px] text-muted">Readiness</p>
            <p className="pixel text-[10px]" style={{ color: readiness.ready ? 'var(--good)' : 'var(--accent-text)' }}>
              {readiness.cleared}/{readiness.total} sections {readiness.ready ? '· ready!' : ''}
            </p>
          </div>
          <Meter value={readiness.cleared} max={readiness.total} tone={readiness.ready ? 'var(--good)' : 'var(--accent)'} label="Report readiness" />
        </>
      )}
      {!compact && (
        <ul className={`${listOnly ? '' : 'mt-3'} grid gap-1.5 sm:grid-cols-2`}>
          {readiness.sections.map((section) => (
            <li key={section.id} className="flex items-center gap-2 text-[15px]">
              <span
                aria-hidden
                className="grid h-5 w-5 flex-none place-items-center rounded-sm text-[11px] font-bold"
                style={{
                  background: section.state === 'cleared' ? 'var(--good)' : section.state === 'tried' ? 'var(--accent)' : 'color-mix(in oklab, var(--ink) 16%, transparent)',
                  color: section.state === 'cleared' ? 'var(--good-ink)' : section.state === 'tried' ? 'var(--accent-ink)' : 'var(--muted)',
                }}
              >
                {section.state === 'cleared' ? <IconCheck size={12} /> : section.state === 'tried' ? '!' : '·'}
              </span>
              <span className={section.state === 'new' ? 'text-muted' : ''}>{section.heading}</span>
              <span className="sr-only">
                {section.state === 'cleared' ? ' – cleared' : section.state === 'tried' ? ' – tried, not cleared yet' : ' – not played yet'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function formatScore(value: number): string {
  return value.toLocaleString()
}

function rankTone(rank: number): { color: string; border: string } {
  if (rank === 1) return { color: 'var(--rank-1)', border: 'color-mix(in oklab, var(--rank-1) 60%, transparent)' }
  if (rank === 2) return { color: 'var(--rank-2)', border: 'color-mix(in oklab, var(--rank-2) 55%, transparent)' }
  if (rank === 3) return { color: 'var(--rank-3)', border: 'color-mix(in oklab, var(--rank-3) 55%, transparent)' }
  return { color: 'var(--muted)', border: 'color-mix(in oklab, var(--ink) 16%, transparent)' }
}

function ScoreRow({
  entry,
  topScore,
  rowRef,
}: {
  entry: LeaderboardEntry
  topScore: number
  rowRef?: React.Ref<HTMLLIElement>
}) {
  const medal = rankTone(entry.rank)
  const share = topScore > 0 ? Math.max(0.03, Math.min(1, entry.score / topScore)) : 0
  return (
    <li ref={rowRef} className="sb-row" data-you={entry.you}>
      <span className="sb-bar" style={{ transform: `scaleX(${share})` }} aria-hidden />
      <span className="sb-rank" style={{ color: medal.color, borderColor: medal.border }}>
        {entry.rank === 1 && <IconCrown size={11} />}
        {String(entry.rank).padStart(2, '0')}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-bold text-ink">{entry.name}</span>
          {entry.you && <span className="sb-you-pill">You</span>}
        </div>
        {(entry.team || entry.badge || entry.passed !== null) && (
          <p className="mt-0.5 truncate text-xs text-muted">
            {[
              entry.team,
              entry.passed === true ? 'Passed' : entry.passed === false ? 'In progress' : entry.badge,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        )}
      </div>
      <span
        className="sb-score"
        style={{ color: entry.you ? 'var(--accent-text)' : entry.rank === 1 ? 'var(--rank-1)' : 'var(--ink)' }}
      >
        {formatScore(entry.score)}
      </span>
    </li>
  )
}

type HighScoresProps = {
  board: Leaderboard
  /** Inline summary mode: caps visible rows and offers a button to open the full board. */
  compact?: boolean
  /** Calm embedded mode (used inside Summary's Standings tab): flat top-5 list + 1-line standing, no nested cards. */
  minimal?: boolean
  onOpenFull?: () => void
  onLoadMore?: () => void
  loadingMore?: boolean
}

export function HighScores({ board, compact = false, minimal = false, onOpenFull, onLoadMore, loadingMore = false }: HighScoresProps) {
  const yourRowRef = useRef<HTMLLIElement | null>(null)
  const [runsOpen, setRunsOpen] = useState(false)

  const totalPlayers = board.players
  const topScore = Math.max(1, board.entries[0]?.score ?? 0, board.you?.score ?? 0)
  const isCompact = compact || minimal
  const visibleLimit = minimal ? 5 : compact ? 8 : board.entries.length
  const visibleEntries = board.entries.slice(0, visibleLimit)
  const youInVisible = visibleEntries.some((entry) => entry.you)

  // If the player is outside the currently displayed slice, build their local neighborhood (rank - 1, you, rank + 1).
  let neighborhood: LeaderboardEntry[] = []
  if (board.you && !youInVisible) {
    const idxInLoaded = board.entries.findIndex((entry) => entry.you)
    if (idxInLoaded >= 0) {
      const start = Math.max(visibleLimit, idxInLoaded - 1)
      neighborhood = board.entries.slice(start, idxInLoaded + 2)
    } else if (board.aroundYou && board.aroundYou.length > 0) {
      neighborhood = board.aroundYou
    } else {
      neighborhood = [board.you]
    }
  }

  const hiddenCount = Math.max(0, totalPlayers - visibleEntries.length)
  const jumpToMe = () => {
    const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    yourRowRef.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' })
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Personal standing: 1-line strip in minimal mode, prominent card in full views */}
      {board.you ? (
        minimal ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface-2/70 px-3 py-2 text-sm">
            <span className="min-w-0">
              <b style={{ color: board.you.rank === 1 ? 'var(--rank-1)' : 'var(--accent-text)' }}>
                #{board.you.rank}
              </b>
              <span className="text-muted"> of {totalPlayers}</span>
              {' · '}
              {board.you.rank === 1 ? (
                <span className="font-semibold" style={{ color: 'var(--rank-1)' }}>Leading the board!</span>
              ) : board.you.gapToNext !== undefined ? (
                <span className="text-muted">
                  <b className="text-ink">{formatScore(board.you.gapToNext)}</b> behind {board.you.aheadName ?? `#${board.you.rank - 1}`}
                </span>
              ) : (
                <span className="text-muted">Best {formatScore(board.you.score)}</span>
              )}
            </span>
            <span className="text-xs font-bold tabular-nums" style={{ color: 'var(--accent-text)' }}>
              Best {formatScore(board.you.score)}
            </span>
          </div>
        ) : (
          <div className="sb-you-card flex flex-wrap items-center justify-between gap-3 px-3.5 py-3">
            <div className="flex items-center gap-3">
              <span
                className="pixel grid h-11 min-w-[48px] place-items-center rounded-[4px] border-2 px-2 text-sm tabular-nums"
                style={{
                  borderColor: 'var(--accent)',
                  background: 'var(--pill-bg)',
                  color: board.you.rank === 1 ? 'var(--rank-1)' : 'var(--accent-text)',
                }}
              >
                #{board.you.rank}
              </span>
              <div className="min-w-0">
                <p className="text-xs font-bold tracking-wider uppercase text-muted">
                  Your standing · of {totalPlayers}
                </p>
                <p className="mt-0.5 text-sm text-ink">
                  {board.you.rank === 1 || board.you.tied ? (
                    <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: 'var(--rank-1)' }}>
                      <IconCrown size={13} /> {board.you.tied ? 'Tied for #1!' : 'Leading the board!'}
                    </span>
                  ) : board.you.gapToNext !== undefined ? (
                    <>
                      <b style={{ color: 'var(--accent-text)' }}>{formatScore(board.you.gapToNext)}</b> pts behind{' '}
                      {board.you.aheadName ?? `#${board.you.rank - 1}`}
                    </>
                  ) : (
                    `Rank #${board.you.rank} of ${totalPlayers}`
                  )}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2.5">
              <div className="text-right">
                <p className="text-xs font-bold tracking-wider uppercase text-muted">Best</p>
                <p className="sb-score !text-xl" style={{ color: 'var(--accent-text)' }}>
                  {formatScore(board.you.score)}
                </p>
              </div>
              {!isCompact && visibleEntries.length > 8 && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={jumpToMe} title="Scroll to your row">
                  Find me
                </button>
              )}
            </div>
          </div>
        )
      ) : (
        <p className="rounded-[4px] border border-line bg-surface-2 px-3 py-2 text-sm text-muted">
          Finish a {board.modeName} game to put your score on this board.
        </p>
      )}

      {/* Player's own recent completed games in this mode (omitted in minimal embedded mode; full history lives on HighScoresScreen). */}
      {!minimal && board.yourRuns && board.yourRuns.length > 0 && (
        <div className="rounded-[4px] border border-line bg-surface-2 px-3 py-2.5">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-bold tracking-wider uppercase text-muted">Your scores ({board.yourRuns.length})</p>
            {board.yourRuns.length > 4 && (
              <button
                type="button"
                className="inline-flex min-h-[44px] items-center px-2 -my-2 text-xs font-bold underline text-muted hover:text-ink"
                onClick={() => setRunsOpen((open) => !open)}
              >
                {runsOpen ? 'Show fewer' : `Show all ${board.yourRuns.length}`}
              </button>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(runsOpen ? board.yourRuns : board.yourRuns.slice(0, 4)).map((run, index) => (
              <span
                key={run.id}
                className="inline-flex items-center gap-1.5 rounded-[3px] border px-2.5 py-1 text-sm tabular-nums"
                style={{
                  borderColor: run.isBest ? 'var(--accent)' : 'var(--line)',
                  background: run.isBest ? 'color-mix(in oklab, var(--accent) 16%, transparent)' : 'var(--pill-bg)',
                  color: run.isBest ? 'var(--accent-text)' : 'var(--ink)',
                }}
                title={run.answered ? `${run.correct}/${run.answered} correct · Best streak x${run.streakMax}` : undefined}
              >
                {run.isBest && <IconStar size={11} />}
                <b>{formatScore(run.score)}</b>
                {run.answered > 0 && (
                  <span className="text-xs text-muted">
                    {Math.round((100 * run.correct) / run.answered)}%
                  </span>
                )}
                {index === 0 && <span className="text-[11px] text-muted">latest</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Ranked list */}
      {board.entries.length === 0 ? (
        <p className="py-5 text-center text-sm text-muted">No scores yet. Be the first on the board.</p>
      ) : (
        <div
          className={!isCompact ? 'scroll-thin max-h-[46dvh] overflow-y-auto pr-1' : undefined}
          role={!isCompact ? 'region' : undefined}
          aria-label={!isCompact ? `${board.modeName} rankings` : undefined}
          tabIndex={!isCompact ? 0 : undefined}
        >
          <ol className={minimal ? 'sb-list-flat grid' : 'grid gap-1.5'}>
            {visibleEntries.map((entry) => (
              <ScoreRow
                key={`${entry.rank}-${entry.name}`}
                entry={entry}
                topScore={topScore}
                rowRef={entry.you ? yourRowRef : undefined}
              />
            ))}
          </ol>

          {neighborhood.length > 0 && (
            <div className="mt-2">
              <div className="my-1.5 flex items-center gap-2 text-xs text-muted" aria-hidden>
                <span className="h-px flex-1 bg-line" />
                <span className="text-[11px] font-bold tracking-wider uppercase">Around you</span>
                <span className="h-px flex-1 bg-line" />
              </div>
              <ol className={minimal ? 'sb-list-flat grid' : 'grid gap-1.5'}>
                {neighborhood.map((entry) => (
                  <ScoreRow
                    key={`near-${entry.rank}-${entry.name}`}
                    entry={entry}
                    topScore={topScore}
                    rowRef={entry.you ? yourRowRef : undefined}
                  />
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {/* Show more / open full-page high scores button */}
      {isCompact && onOpenFull && totalPlayers > 0 && (
        <button type="button" className="btn btn-ghost btn-sm w-full" onClick={onOpenFull}>
          <IconTrophy size={13} />
          <span>
            {hiddenCount > 0 ? `Full high scores & history (${totalPlayers} players)` : 'Full high scores, teams & history'}
          </span>
        </button>
      )}

      {!isCompact && totalPlayers > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>
            Showing {visibleEntries.length} of {totalPlayers} player{totalPlayers === 1 ? '' : 's'}
            {board.team ? ` in ${board.team}` : ''}
          </span>
          {board.hasMore && onLoadMore && (
            <button type="button" className="btn btn-ghost btn-sm" disabled={loadingMore} onClick={onLoadMore}>
              {loadingMore ? 'Loading…' : `Show all ${Math.min(totalPlayers, 500)} scores`}
            </button>
          )}
        </div>
      )}

      {/* Team averages (full view only; keeps embedded summary clean) */}
      {!minimal && board.teams.length >= 2 && (
        <div className="mt-2 border-t border-line pt-3">
          <p className="pixel mb-2 text-[10px] text-muted">Teams · average score</p>
          <ol className="grid gap-1.5">
            {board.teams.map((team, index) => {
              const topAvg = Math.max(1, board.teams[0]?.average ?? 1)
              const medal = rankTone(index + 1)
              return (
                <li key={team.team} className="sb-row">
                  <span className="sb-bar" style={{ transform: `scaleX(${team.average / topAvg})` }} aria-hidden />
                  <span className="sb-rank" style={{ color: medal.color, borderColor: medal.border }}>
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-bold">
                    {team.team} <span className="font-normal text-sm text-muted">({team.players})</span>
                  </span>
                  <span className="sb-score" style={{ color: index === 0 ? 'var(--rank-1)' : 'var(--ink)' }}>
                    {formatScore(team.average)}
                  </span>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}
