// Building blocks shared by the solo game, the summary and the live screens.

import { useEffect, useRef, useState } from 'react'
import { Meter } from '../components/ui'
import { PictureViewer, ZoomImage } from '../components/ZoomImage'
import type { TalkState } from '../lib/hostVoice'
import { musicElement, musicFading, playClip, resumeClip, stopMusic } from '../lib/music'
import { play } from '../lib/sound'
import { trace } from '../lib/trace'
import type { ItemImage, Leaderboard, Readiness, RunState } from '../lib/types'

export const TONES = ['var(--a0)', 'var(--a1)', 'var(--a2)', 'var(--a3)']
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
 * The host's dialogue box, with a microphone button when the player may answer back.
 *
 * The button works both ways people expect: tap it, talk, tap again to send – or hold it while
 * talking and let go. (The first press may bring up the browser's permission question, which is
 * why a tap must not need to be held.)
 */
export function HostBox({ name, line, speaking, talk }: { name: string; line: string; speaking: boolean; talk?: TalkControl | null }) {
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
  let words: React.ReactNode = line || '…'
  if (listening) words = <span style={{ color: 'var(--accent)' }}>{tapped ? 'Listening… tap the mic again to send.' : 'Listening… let go when you are done.'}</span>
  else if (!line && thinking) words = <span className="text-white/60">…</span>
  else if (!line && !speaking) {
    words = <span className="text-white/55">{talk?.problem ?? `Tap the mic to talk to ${name}: heckle, ask your score, or have the question read out.`}</span>
  }
  return (
    <div className="dialogue rise flex items-center gap-3 px-3 pb-2.5 pt-3.5 text-[15px] leading-snug sm:text-base">
      <span className="who">{name}</span>
      <p className="min-w-0 flex-1" aria-live="polite">
        <span className="mr-2 inline-flex align-middle">
          <span className="host-wave" data-on={speaking || listening}>
            <i /><i /><i /><i />
          </span>
        </span>
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
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden fill="currentColor">
            <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 0 0-7 0v5A3.5 3.5 0 0 0 12 15Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.900V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.600a6 6 0 0 0 5-5.900Z" />
          </svg>
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
        if (label.current) label.current.textContent = String(seconds)
        second.current?.(seconds)
        if (!quiet && seconds <= 5 && seconds > 0) play('tick')
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
      <span ref={label} className="pixel w-8 text-right text-[11px]">{Math.ceil(remainingMs / 1000)}</span>
    </div>
  )
}

/** Counts a deadline down to zero (used for the Speed Run clock). */
export function useDeadline(deadlineIso: string | undefined, onZero: () => void): number {
  const [left, setLeft] = useState(() => (deadlineIso ? Math.max(0, new Date(deadlineIso).getTime() - Date.now()) : 0))
  const callback = useRef(onZero)
  callback.current = onZero
  useEffect(() => {
    if (!deadlineIso) return
    let fired = false
    const update = () => {
      const remaining = Math.max(0, new Date(deadlineIso).getTime() - Date.now())
      setLeft(remaining)
      if (remaining <= 0 && !fired) {
        fired = true
        callback.current()
      }
    }
    update()
    const id = window.setInterval(update, 200)
    return () => window.clearInterval(id)
  }, [deadlineIso])
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
            style={{ ['--tone' as string]: TONES[index % 4] }}
            disabled={disabled || gone}
            onClick={() => onPick(index)}
            aria-label={`Answer ${LETTERS[index]}: ${option}`}
          >
            <span className="badge pixel" aria-hidden>{LETTERS[index]}</span>
            <span className="min-w-0 flex-1 break-words">{option}</span>
            {revealed && index === correctIndex && <span aria-hidden>✔</span>}
            {revealed && index === picked && index !== correctIndex && <span aria-hidden>✘</span>}
          </button>
        )
      })}
    </div>
  )
}

export function FactFakeButtons({ picked, correctIndex, disabled, onPick }: Omit<AnswerListProps, 'options' | 'removed'>) {
  const revealed = correctIndex !== null
  const look = (index: number) => {
    if (!revealed) return picked === index ? 'picked' : 'idle'
    return index === correctIndex ? 'right' : index === picked ? 'wrong' : 'dim'
  }
  return (
    <div className="grid grid-cols-2 gap-3">
      {['FACT', 'FAKE'].map((label, index) => (
        <button
          key={label}
          className="answer justify-center !min-h-[84px]"
          data-state={look(index)}
          style={{ ['--tone' as string]: index === 0 ? 'var(--good)' : 'var(--bad)' }}
          disabled={disabled}
          onClick={() => onPick(index)}
        >
          <span className="pixel text-base sm:text-lg" style={{ color: index === 0 ? 'var(--good)' : 'var(--bad)' }}>{label}</span>
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
}

/**
 * A picture from the report. It can be pinched larger right where it is, and a tap opens it
 * full screen, where a wide chart is turned on its side to use the whole phone.
 */
export function PictureView({ image, secondsLeft, caption }: PictureProps) {
  const [open, setOpen] = useState(false)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const holder = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState({ w: 0, h: 0 })
  const frame = { borderColor: 'var(--accent)', boxShadow: '4px 4px 0 #000' }

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
  const strip = fitted > 0 && fitted < wanted * 0.8
  if (image.mode === 'memory') {
    return (
      <div className="relative overflow-hidden rounded border-2 bg-white" style={frame}>
        <img src={image.url} alt="" className="mx-auto max-h-[38dvh] w-full object-contain" />
        <span className="pixel absolute inset-x-0 bottom-0 bg-black/75 px-2 py-2 text-center text-[9px] text-white">From memory – no peeking</span>
      </div>
    )
  }
  return (
    <>
      <div ref={holder} className="picture-frame relative overflow-hidden rounded border-2 bg-white" style={frame}>
        <ZoomImage
          src={image.url}
          alt={image.alt}
          cover={strip}
          onTap={() => setOpen(true)}
          onSize={setSize}
          className="w-full"
          style={strip ? { height: wanted } : { aspectRatio: size ? `${size.w} / ${size.h}` : '16 / 9', maxHeight: '46dvh' }}
        />
        <button
          className="absolute bottom-1.5 right-1.5 grid h-9 w-9 place-items-center rounded-sm bg-black/80 text-lg leading-none text-white"
          style={{ fontFamily: 'system-ui, sans-serif' }}
          onClick={() => setOpen(true)}
          aria-label="Open the picture full screen"
          title="Full screen"
        >
          ⤢
        </button>
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
    // Report a clip as broken only for a genuine media failure on *this* clip while it is on screen
    // (tearing the player down also fires events, which must never count).
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
    // The clip was loading while the round was being introduced; if it already failed, say so now.
    if (audio.dataset.clip === src && audio.error) onError()
    else playClip(src).catch(refused) // already buffered, so it swells in at once
    // A clip that has not produced a sound after a good while is as broken as one that errors
    // (a stalled download, a network that blocks the music host). The server double-checks before
    // it blames the clip, so reporting it costs nothing if the trouble is on this side.
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
      if (!musicFading()) stopMusic() // a fade already in progress finishes by itself
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
        {playing ? '❚❚' : '▶'}
      </button>
    </div>
  )
}

// --------------------------------------------------------------------------- HUD

export function Hud({ state, clockMs, position }: { state: RunState; clockMs?: number; position?: string }) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div className="hud">
        Score<b className="neon-accent">{String(state.score).padStart(6, '0')}</b>
      </div>
      {state.mode === 'sudden_death' && (
        <div className="hud text-center">
          Lives
          <b className="flex justify-center gap-1 pt-0.5">
            {Array.from({ length: state.maxStrikes ?? 3 }, (_, i) => (
              <span key={i} className="heart" data-lost={i >= (state.maxStrikes ?? 3) - (state.strikes ?? 0)} />
            ))}
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
          Streak<b style={{ color: 'var(--accent)' }}>x{state.streak}</b>
        </div>
      )}
      {position && (
        <div className="hud text-right">
          {state.mode === 'show' || state.mode === 'straight' ? 'Question' : 'Answered'}<b>{position}</b>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- readiness + leaderboard

export function ReadinessMap({ readiness, compact }: { readiness: Readiness; compact?: boolean }) {
  if (!readiness.total) return null
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="pixel text-[9px] text-muted">Readiness</p>
        <p className="pixel text-[10px]" style={{ color: readiness.ready ? 'var(--good)' : 'var(--accent)' }}>
          {readiness.cleared}/{readiness.total} sections {readiness.ready ? '· ready!' : ''}
        </p>
      </div>
      <Meter value={readiness.cleared} max={readiness.total} tone={readiness.ready ? 'var(--good)' : 'var(--accent)'} />
      {!compact && (
        <ul className="mt-3 grid gap-1.5 sm:grid-cols-2">
          {readiness.sections.map((section) => (
            <li key={section.id} className="flex items-center gap-2 text-[15px]">
              <span
                aria-hidden
                className="grid h-5 w-5 flex-none place-items-center rounded-sm text-[11px] font-bold"
                style={{
                  background: section.state === 'cleared' ? 'var(--good)' : section.state === 'tried' ? 'var(--accent)' : 'color-mix(in oklab, var(--ink) 16%, transparent)',
                  color: section.state === 'new' ? 'var(--muted)' : '#111',
                }}
              >
                {section.state === 'cleared' ? '✔' : section.state === 'tried' ? '!' : '·'}
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

export function HighScores({ board }: { board: Leaderboard }) {
  if (!board.entries.length) {
    return <p className="py-6 text-center text-muted">No scores yet. Be the first on the board.</p>
  }
  return (
    <div>
      <ol className="grid gap-1">
        {board.entries.map((entry) => (
          <li
            key={`${entry.rank}-${entry.name}`}
            className="flex items-center gap-3 rounded-sm px-2 py-1.5"
            style={entry.you ? { background: 'color-mix(in oklab, var(--accent) 22%, transparent)', outline: '2px solid var(--accent)' } : undefined}
          >
            <span className="pixel w-9 text-[10px]" style={{ color: entry.rank <= 3 ? 'var(--accent)' : 'var(--muted)' }}>{String(entry.rank).padStart(2, '0')}</span>
            <span className="min-w-0 flex-1 truncate">
              {entry.name}
              {entry.you && <span className="pixel ml-2 text-[8px]" style={{ color: 'var(--accent)' }}>you</span>}
              {entry.team && <span className="ml-2 text-sm text-muted">{entry.team}</span>}
            </span>
            <span className="pixel text-[11px]">{String(entry.score).padStart(6, '0')}</span>
          </li>
        ))}
      </ol>
      {board.you && board.you.rank > board.entries.length && (
        <p className="mt-3 text-center text-muted">You are #{board.you.rank} with {board.you.score}.</p>
      )}
      {board.teams.length >= 2 && (
        <div className="mt-5">
          <p className="pixel mb-2 text-[9px] text-muted">Teams · average score</p>
          <ol className="grid gap-1.5">
            {board.teams.map((team, index) => (
              <li key={team.team} className="flex items-center gap-3">
                <span className="pixel w-9 text-[10px]" style={{ color: 'var(--accent)' }}>{String(index + 1).padStart(2, '0')}</span>
                <span className="flex-1 truncate">{team.team} <span className="text-sm text-muted">({team.players})</span></span>
                <span className="pixel text-[11px]">{String(team.average).padStart(6, '0')}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}
