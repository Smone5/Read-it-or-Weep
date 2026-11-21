// The big screen for a live game: join code, the question, the reveal, the scoreboard.
// Opened from the admin with a one-time control key in the link; that key is what lets
// this screen (and only this screen) move the game on.
//
// Timing. The host reads each question to the room while the phones are still locked; when he
// has finished, this screen tells the server to open the question, and the clock (and a music
// clip) start together a beat later. Moving on while he is talking cuts him off cleanly.

import confetti from 'canvas-confetti'
import QRCode from 'qrcode'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { IconBolt, IconCheck, IconCrown, IconPlay, IconStar } from '../components/Icons'
import { NowPlaying } from '../components/NowPlaying'
import { FullScreenMessage, SoundToggle, Spinner, Stage } from '../components/ui'
import { formatScore, HostBox, LETTERS, TONE_INKS, TONES } from '../game/parts'
import { startParty, stopJukebox } from '../lib/jukebox'
import { api } from '../lib/api'
import { HostVoice } from '../lib/hostVoice'
import { fadeOutMusic, musicElement, playClip, preloadClip, resumeClip, stopMusic, unlockMusic } from '../lib/music'
import { play, preloadSounds, unlockAudio } from '../lib/sound'
import { applyBranding, applyDecade } from '../lib/theme'
import { trace } from '../lib/trace'
import type { Branding, LiveState } from '../lib/types'
import { useLive, useTicker } from './useLive'

function readKey(code: string): string | null {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get('k')
  const storageKey = `quiz.live.key.${code}`
  try {
    if (fromHash) {
      sessionStorage.setItem(storageKey, fromHash)
      // Take the key out of the address bar so it is not on show to the room.
      window.history.replaceState(null, '', window.location.pathname)
      return fromHash
    }
    return sessionStorage.getItem(storageKey)
  } catch {
    return fromHash
  }
}

const MIN_LEAD_MS = 3000 // a question is on screen at least this long before the phones unlock
const WELCOME_WAIT_MS = 12000 // the longest the lobby waits for the host's welcome

const pause = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))

export default function HostScreen() {
  const { code: rawCode = '' } = useParams()
  const code = rawCode.toUpperCase()
  const [hostKey] = useState(() => readKey(code))
  const { state, error, until, headers } = useLive({ code, hostKey, enabled: Boolean(hostKey) })
  const [branding, setBranding] = useState<Branding | null>(null)
  const [qr, setQr] = useState('')
  const [busy, setBusy] = useState(false)
  const [hostLine, setHostLine] = useState('')
  const [hostSpeaking, setHostSpeaking] = useState(false)
  const [showQr, setShowQr] = useState(false)
  const voice = useRef<HostVoice | null>(null)
  const seen = useRef('')
  const wasLocked = useRef(true)
  const unlockedAt = useRef(0)
  const joinUrl = `${window.location.origin}/live/${code}`
  useTicker(state?.status === 'question')

  useEffect(() => {
    api<{ branding: Branding }>(`/api/live/${code}/info`, { as: 'none' })
      .then((info) => {
        applyBranding(info.branding)
        setBranding(info.branding)
      })
      .catch(() => undefined)
    void QRCode.toDataURL(joinUrl, { width: 420, margin: 1 }).then(setQr)
    preloadSounds()
    return () => {
      voice.current?.close()
      stopMusic()
      stopJukebox(0)
      applyDecade(null)
    }
  }, [code, joinUrl])

  const send = useCallback(
    (action: 'next' | 'end' | 'open') =>
      fetch(`/api/live/${code}/control`, { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) }).catch(() => undefined),
    [code, headers],
  )

  const question = state?.status === 'question' ? state.question : undefined
  const locked = Boolean(question) && until(question?.opensAt) > 0 // on screen, being read out; phones not open yet

  useEffect(() => {
    if (wasLocked.current && !locked && state?.status === 'question') {
      unlockedAt.current = performance.now()
    }
    wasLocked.current = locked
  }, [locked, state?.status])

  const control = useCallback(
    async (action: 'next' | 'end') => {
      if (busy) return
      // Guard against a Spacebar / clicker race right after auto-open so we never skip straight to Reveal with 0 answers.
      if (action === 'next' && state?.status === 'question' && !locked && performance.now() - unlockedAt.current < 2500) {
        return
      }
      setBusy(true)
      unlockAudio()
      unlockMusic()
      if (!voice.current && hostKey) {
        voice.current = new HostVoice({ liveCode: code, hostKey }, { onLine: setHostLine, onSpeaking: setHostSpeaking })
        voice.current.connect()
      }
      const host = voice.current
      try {
        if (action === 'next' && state?.status === 'lobby') {
          // The welcome is heard over the lobby; the first question follows it.
          host?.cue('live_welcome')
          if (host) await Promise.race([host.whenQuiet(), pause(WELCOME_WAIT_MS)])
          await send('next')
        } else if (action === 'next' && locked) {
          host?.skip() // "Start the clock": no need to hear the rest of the question
          await send('open')
        } else {
          host?.skip() // moving on: the host stops rather than talk over the next screen
          await send(action)
        }
      } finally {
        setBusy(false)
      }
    },
    [busy, code, hostKey, locked, send, state?.status],
  )

  // React to each phase change exactly once: sounds, the host's line, the music clip.
  useEffect(() => {
    if (!state) return
    applyDecade(state.decade)
    const marker = `${state.status}:${state.index}`
    if (marker === seen.current) return
    seen.current = marker
    trace('live', { phase: marker })
    const host = voice.current
    const current = state.question
    if (state.status === 'question' && current) {
      stopMusic()
      play('levelup')
      if (current.kind === 'music' && current.previewUrl) preloadClip(current.previewUrl)
      host?.cue('live_question')
      // Open the question once it has been on screen a moment and the host has finished reading it.
      void Promise.all([pause(MIN_LEAD_MS), host?.whenQuiet()]).then(() => {
        if (seen.current !== marker) return
        trace('live.open')
        void send('open')
      })
    } else if (state.status === 'reveal') {
      play('rimshot')
      host?.cue('live_reveal')
      // A music clip runs on under his reaction, then bows out.
      void Promise.race([host?.whenQuiet() ?? pause(0), pause(9000)])
        .then(() => pause(2500))
        .then(() => {
          if (seen.current === marker) fadeOutMusic(1500)
        })
    } else if (state.status === 'scoreboard') {
      fadeOutMusic()
      host?.cue('live_scoreboard')
    } else if (state.status === 'finished') {
      fadeOutMusic()
      window.setTimeout(() => {
        if (seen.current === marker) play('cheer')
      }, 1050)
      host?.cue('live_final')
      // Once the winner has been crowned, music for the room: hits from the game's decade.
      const party = state.afterParty
      if (party) {
        void Promise.all([pause(2200), Promise.race([host?.whenQuiet(), pause(15000)])]).then(() => {
          if (seen.current === marker) startParty(party.tracks)
        })
      }
    }
  }, [state, send])

  // A music clip starts with the clock, not before it.
  const clip = question?.kind === 'music' && !locked ? question.previewUrl : undefined
  useEffect(() => {
    if (clip) void playClip(clip).catch(() => undefined)
  }, [clip])
  useEffect(() => {
    if (question) trace(locked ? 'live.locked' : 'live.unlocked', { question: question.number })
  }, [locked, question?.number]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ([' ', 'Enter', 'ArrowRight', 'PageDown'].includes(event.key) && state && state.status !== 'finished') {
        event.preventDefault()
        void control('next')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [control, state])

  if (!hostKey) {
    return <FullScreenMessage title="Big screen link needed">Open the big screen from the admin page's Live tab. That link carries the key that controls the game.</FullScreenMessage>
  }
  if (error) return <FullScreenMessage title="Could not open the game">{error}</FullScreenMessage>
  if (!state) return <Stage className="grid place-items-center"><Spinner label="Connecting" /></Stage>
  if (state.role !== 'host') {
    return <FullScreenMessage title="This link has expired">Start a new live game from the admin page to get a fresh big-screen link.</FullScreenMessage>
  }

  const nextLabel: Record<string, string> = {
    lobby: busy ? 'Here we go…' : state.players ? 'Start the game' : 'Waiting for players…',
    question: locked ? 'Start the clock' : 'Reveal now',
    reveal: 'Scoreboard',
    scoreboard: state.index + 1 >= state.total ? 'Final results' : 'Next question',
  }

  return (
    <Stage>
      <div className="mx-auto flex w-full max-w-[1500px] flex-1 flex-col gap-3 lg:h-[calc(100dvh-26px)] lg:gap-4 lg:overflow-hidden">
        <header className="flex flex-none items-center gap-4">
          <div className="min-w-0 flex-1">
            <p className="pixel truncate text-[10px] text-muted lg:text-xs">{branding?.companyName} · {state.quizTitle}</p>
            <p className="pixel neon truncate text-sm lg:text-xl">{branding?.gameTitle ?? 'Live game'}</p>
          </div>
          {state.status !== 'lobby' && state.status !== 'finished' && (
            <div className="relative flex-none">
              <button
                type="button"
                className="btn btn-ghost btn-sm text-xs"
                onClick={() => setShowQr((v) => !v)}
                title="Show late-join QR code"
              >
                Join · {joinUrl.replace(/^https?:\/\//, '')}
              </button>
              {showQr && qr && (
                <div className="card absolute right-0 top-12 z-40 flex flex-col items-center gap-2 p-3 text-center shadow-2xl">
                  <img src={qr} alt="Late-join QR code" className="h-40 w-40 rounded border-4 border-white" />
                  <p className="pixel neon-accent text-sm tracking-[0.18em]">{state.code}</p>
                </div>
              )}
            </div>
          )}
          <div className="hud text-right">Code<b className="neon-accent !text-xl lg:!text-3xl">{state.code}</b></div>
          <div className="hud text-right">Players<b className="!text-xl lg:!text-3xl">{state.players}</b></div>
          <SoundToggle />
        </header>

        <main className="flex min-h-0 flex-1 flex-col">
          {state.status === 'lobby' && <LobbyView state={state} qr={qr} joinUrl={joinUrl} />}
          {(state.status === 'question' || state.status === 'reveal') && state.question && <QuestionView state={state} until={until} />}
          {state.status === 'scoreboard' && <Scoreboard state={state} />}
          {state.status === 'finished' && <Finale state={state} />}
        </main>

        <footer className="flex flex-none items-end gap-4">
          <div className="min-w-0 flex-1">
            <HostBox name={branding?.host.name ?? 'Host'} line={hostLine} speaking={hostSpeaking} />
          </div>
          {state.status === 'finished' && (
            <div className="w-[min(46%,600px)] flex-none">
              <NowPlaying large decade={state.afterParty?.decade} />
            </div>
          )}
          {state.status !== 'finished' && (
            <>
              <button className="btn btn-ghost" disabled={busy} onClick={() => window.confirm('End the live game now?') && void control('end')}>End</button>
              <button className="btn btn-primary btn-lg" disabled={busy || (state.status === 'lobby' && !state.players)} onClick={() => void control('next')}>
                <span>{nextLabel[state.status]}</span>
                {!(state.status === 'lobby' && (!state.players || busy)) && <IconPlay size={14} />}
                <span className="kbd">SPACE</span>
              </button>
            </>
          )}
        </footer>
      </div>
    </Stage>
  )
}

function LobbyView({ state, qr, joinUrl }: { state: LiveState; qr: string; joinUrl: string }) {
  const roster = state.roster ?? []
  return (
    <div className="grid min-h-0 flex-1 items-center gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:gap-10">
      {/* Sized from the screen's height so the whole lobby fits a 720p projector without scrolling. */}
      <section className="card flex flex-col items-center gap-3 p-5 text-center lg:p-7">
        <p className="pixel text-xs text-muted lg:text-sm">Grab your phone and go to</p>
        <p className="pixel neon break-all text-sm lg:text-xl">{joinUrl.replace(/^https?:\/\//, '')}</p>
        {qr && <img src={qr} alt="QR code to join the game" className="rounded border-4 border-white" style={{ width: 'min(17rem, 30dvh)' }} />}
        <p className="pixel text-xs text-muted">or enter the code</p>
        <p className="pixel neon-accent tracking-[0.2em]" style={{ fontSize: 'clamp(2rem, 8.5dvh, 4.5rem)', lineHeight: 1.2 }}>{state.code}</p>
      </section>
      <section className="card flex max-h-full min-h-[36dvh] flex-col overflow-y-auto p-5 lg:p-7">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-sm lg:text-xl">In the room</h2>
          <p className="pixel inline-flex items-center gap-1.5 text-[10px] text-muted lg:text-xs">
            <IconStar size={12} style={{ color: 'var(--accent)' }} /> = did the pre-read{state.headStart ? ` (+${state.headStart} head start)` : ''}
          </p>
        </div>
        {roster.length === 0 ? (
          <p className="pixel blink m-auto text-xs text-muted lg:text-base">Waiting for the first player</p>
        ) : (
          <ul className="mt-5 flex flex-wrap content-start gap-2.5 lg:gap-3">
            {roster.map((person, index) => (
              <li key={`${person.name}-${index}`} className="zoom-in inline-flex items-center gap-1.5 rounded-sm border-2 px-3 py-1.5 text-base lg:text-2xl" style={{ borderColor: person.prepared ? 'var(--accent)' : 'var(--line)', background: 'color-mix(in oklab, var(--page) 60%, transparent)' }}>
                {person.prepared && <IconStar size={16} style={{ color: 'var(--accent)' }} />}
                {person.name}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function QuestionView({ state, until }: { state: LiveState; until: (iso: string | null | undefined) => number }) {
  const question = state.question!
  const reveal = state.status === 'reveal' ? state.reveal : undefined
  const opensIn = until(question.opensAt)
  const left = Math.max(0, until(question.endsAt))
  const seconds = Math.ceil(left / 1000)
  const answered = state.answersIn ?? 0
  const most = reveal ? Math.max(1, ...reveal.counts) : 1
  const hasPicture = Boolean(question.imageUrl)

  // Animate reveal vote bars from 0 -> final on mount.
  const [barsReady, setBarsReady] = useState(false)
  useEffect(() => {
    if (!reveal) {
      setBarsReady(false)
      return
    }
    const id = requestAnimationFrame(() => setBarsReady(true))
    return () => cancelAnimationFrame(id)
  }, [reveal, question.number])

  // Track real <audio> playback state so the music equalizer never dances while audio is blocked/paused.
  const [clipPlaying, setClipPlaying] = useState(false)
  useEffect(() => {
    if (question.kind !== 'music') return
    const audio = musicElement()
    const sync = () => setClipPlaying(!audio.paused && !audio.ended)
    sync()
    audio.addEventListener('playing', sync)
    audio.addEventListener('pause', sync)
    audio.addEventListener('ended', sync)
    audio.addEventListener('error', sync)
    return () => {
      audio.removeEventListener('playing', sync)
      audio.removeEventListener('pause', sync)
      audio.removeEventListener('ended', sync)
      audio.removeEventListener('error', sync)
    }
  }, [question.kind, question.number])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 lg:gap-5">
      <div className="flex flex-none items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="chip lg:text-xs">Question {question.number} of {state.total}</span>
          {question.doublePoints && <span className="chip chip-accent lg:text-xs"><IconBolt size={12} /> 2× DOUBLE POINTS</span>}
          {question.section && <span className="chip chip-accent lg:text-xs">{question.section}</span>}
        </div>
        {!reveal && (
          <div className="flex items-center gap-5">
            <div className="hud text-right">Answers in<b className="!text-xl lg:!text-3xl">{answered}/{state.players}</b></div>
            <div className="pixel grid h-16 w-16 place-items-center rounded border-4 text-xl tabular-nums lg:h-20 lg:w-20 lg:text-3xl" style={{ borderColor: seconds <= 5 && opensIn <= 0 ? 'var(--bad)' : 'var(--accent)', color: seconds <= 5 && opensIn <= 0 ? 'var(--bad)' : 'var(--accent-text)' }}>
              {opensIn > 0 ? '…' : seconds}
            </div>
          </div>
        )}
      </div>

      <div className={`grid min-h-0 flex-1 items-center gap-5 ${hasPicture ? 'lg:grid-cols-2' : ''}`}>
        <div className="min-h-0">
          {question.title && question.kind !== 'music' && <p className="pixel neon-accent mb-2 text-xs lg:text-base">“{question.title}”</p>}
          <h1 className="!normal-case text-2xl sm:text-3xl lg:text-[clamp(1.6rem,4.8dvh,2.8rem)]" style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.25 }}>{question.text}</h1>
          {question.kind === 'music' && !reveal && (
            <div className="mt-5 flex items-center gap-5">
              <div className="eq !h-20" data-on={opensIn <= 0 && clipPlaying}><i /><i /><i /><i /><i /></div>
              <div>
                <p className="pixel neon-accent text-sm lg:text-xl">{opensIn > 0 ? 'Get ready to listen' : clipPlaying ? 'Listen up' : 'Clip paused'}</p>
                {opensIn <= 0 && !clipPlaying && question.previewUrl && (
                  <button type="button" className="btn btn-accent btn-sm mt-2" onClick={() => void resumeClip().catch(() => void playClip(question.previewUrl!))}>
                    <IconPlay size={12} /> Replay clip
                  </button>
                )}
              </div>
            </div>
          )}
          {reveal?.fastest && (
            <p className="zoom-in mt-4 inline-flex items-center gap-2 rounded-[4px] border-2 px-3 py-1.5 text-sm font-bold lg:text-lg" style={{ borderColor: 'var(--accent)', background: 'color-mix(in oklab, var(--accent) 16%, transparent)', color: 'var(--accent-text)' }}>
              <IconBolt size={15} /> Fastest finger: {reveal.fastest.name} · {(reveal.fastest.timeMs / 1000).toFixed(1)}s
            </p>
          )}
          {reveal?.music && (
            <div className="zoom-in mt-5 flex items-center gap-5">
              {reveal.music.artworkUrl && <img src={reveal.music.artworkUrl} alt="" width={160} height={160} decoding="async" className="h-24 w-24 rounded-sm border-4 border-black object-cover lg:h-36 lg:w-36" />}
              <div className="min-w-0">
                <p className="truncate text-2xl lg:text-4xl">{reveal.music.title}</p>
                <p className="truncate text-xl text-muted lg:text-3xl">{reveal.music.artist}</p>
              </div>
            </div>
          )}
          {reveal?.explanation && <p className="rise mt-4 text-lg text-muted lg:text-2xl">{reveal.explanation}</p>}
        </div>
        {hasPicture && (
          <div className="picture-frame overflow-hidden rounded-[6px] border-4 bg-white" style={{ borderColor: 'var(--accent)', boxShadow: '0 0 0 2px #000, 5px 5px 0 2px #000' }}>
            <img src={question.imageUrl} alt="" className="max-h-[42dvh] w-full object-contain" />
          </div>
        )}
      </div>

      {!reveal && opensIn > 0 ? (
        <div className="card flex-none px-5 py-4 text-center">
          <p className="pixel blink text-xs lg:text-base" style={{ color: 'var(--accent-text)' }}>
            Read the question… answers unlock on phones in a moment
          </p>
        </div>
      ) : (
        <div className="grid flex-none gap-3 sm:grid-cols-2 lg:gap-4">
          {question.options.map((option, index) => {
            const right = reveal && index === reveal.correctIndex
            return (
              <div
                key={index}
                className="tile relative min-h-[64px] overflow-hidden px-4 py-2.5 text-lg lg:min-h-[9dvh] lg:px-6 lg:text-[clamp(1.15rem,3.1dvh,1.75rem)]"
                data-dim={Boolean(reveal) && !right}
                style={{ ['--tone' as string]: TONES[index % 4], ['--tone-ink' as string]: TONE_INKS[index % 4] }}
              >
                {reveal && (
                  <span
                    className="absolute inset-0 origin-left bg-black/22 transition-transform duration-700"
                    style={{ transform: `scaleX(${barsReady ? reveal.counts[index] / most : 0})` }}
                    aria-hidden
                  />
                )}
                <span className="pixel relative grid h-10 w-10 flex-none place-items-center rounded-sm bg-black/25 text-base lg:h-12 lg:w-12 lg:text-xl">{LETTERS[index]}</span>
                <span className="relative min-w-0 flex-1 break-words">{option}</span>
                {reveal && (
                  <span className="pixel relative inline-flex items-center gap-1.5 text-sm tabular-nums lg:text-xl">
                    {right && <IconCheck size={18} />}
                    {reveal.counts[index]}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function BigScreenRow({
  row,
  top,
  dense,
}: {
  row: NonNullable<LiveState['standings']>[number]
  top: number
  dense: boolean
}) {
  const rankColor = row.rank === 1 ? '#ffd60a' : row.rank === 2 ? '#e2e8f0' : row.rank === 3 ? '#f59e0b' : 'var(--muted)'
  const share = top > 0 ? Math.max(0.04, row.score / top) : 0
  return (
    <li
      className={`rise sb-row ${dense ? 'px-3.5 py-2.5 lg:px-4 lg:py-3' : 'px-4 py-3 lg:px-5 lg:py-3.5'}`}
      style={{
        borderColor: row.rank === 1 ? 'var(--accent)' : row.rank <= 3 ? 'color-mix(in oklab, var(--brand) 55%, var(--line))' : 'var(--line)',
      }}
    >
      <span
        className="sb-bar !h-1"
        style={{
          transform: `scaleX(${share})`,
          background: row.rank === 1 ? 'var(--accent)' : 'var(--brand)',
        }}
        aria-hidden
      />
      <span
        className={`sb-rank ${dense ? 'min-w-[44px] h-8 text-xs lg:min-w-[54px] lg:h-10 lg:text-sm' : 'min-w-[52px] h-10 text-sm lg:min-w-[64px] lg:h-12 lg:text-base'}`}
        style={{ color: rankColor, borderColor: row.rank <= 3 ? rankColor : undefined }}
      >
        {row.rank === 1 && <IconCrown size={dense ? 13 : 16} />}
        {String(row.rank).padStart(2, '0')}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`truncate font-bold text-ink ${dense ? 'text-lg lg:text-2xl' : 'text-xl lg:text-3xl'}`}>
            {row.name}
          </span>
          {row.streak >= 3 && (
            <span
              className="inline-flex flex-none items-center gap-0.5 rounded-sm bg-black/45 px-1.5 py-0.5 text-xs font-bold lg:text-sm"
              style={{ color: 'var(--accent)' }}
            >
              <IconBolt size={12} />x{row.streak}
            </span>
          )}
          {row.rankDelta !== undefined && row.rankDelta !== 0 && (
            <span
              className="flex-none text-xs font-bold tabular-nums lg:text-sm"
              style={{ color: row.rankDelta > 0 ? 'var(--good)' : 'var(--bad)' }}
            >
              {row.rankDelta > 0 ? `▲${row.rankDelta}` : `▼${Math.abs(row.rankDelta)}`}
            </span>
          )}
        </div>
        {row.team && <p className="truncate text-sm text-muted lg:text-base">{row.team}</p>}
      </div>
      <div className="flex flex-none flex-col items-end">
        <span
          className={`sb-score ${dense ? '!text-xl lg:!text-2xl' : '!text-2xl lg:!text-3xl'}`}
          style={{ color: row.rank === 1 ? '#ffd60a' : 'var(--ink)' }}
        >
          {formatScore(row.score)}
        </span>
        {row.roundPoints !== undefined && row.roundPoints > 0 && (
          <span className="text-xs font-bold tabular-nums lg:text-sm" style={{ color: 'var(--good)' }}>
            +{formatScore(row.roundPoints)}
          </span>
        )}
      </div>
    </li>
  )
}

function Scoreboard({ state }: { state: LiveState }) {
  const standings = state.standings ?? []
  const top = Math.max(1, ...standings.map((s) => s.score))
  const twoCol = standings.length > 6
  const split = Math.ceil(standings.length / 2)
  const leftCol = twoCol ? standings.slice(0, split) : standings
  const rightCol = twoCol ? standings.slice(split) : []

  return (
    <div className="flex min-h-0 flex-1 flex-col justify-center gap-4 overflow-y-auto">
      <div className="text-center">
        <h1 className="neon text-xl lg:text-4xl">Scoreboard</h1>
        {state.biggestMover && state.biggestMover.delta > 0 && (
          <p className="mt-2 inline-flex items-center gap-1.5 rounded-[4px] border px-3 py-1 text-sm font-bold lg:text-base" style={{ borderColor: 'var(--good)', color: 'var(--good)', background: 'color-mix(in oklab, var(--good) 12%, transparent)' }}>
            ▲ Biggest mover: {state.biggestMover.name} (+{state.biggestMover.delta} {state.biggestMover.delta === 1 ? 'spot' : 'spots'} to #{state.biggestMover.rank})
          </p>
        )}
        {state.players > standings.length && (
          <p className="mt-1.5 text-sm text-muted lg:text-base">
            Top {standings.length} of {state.players} players · check your phone for your exact rank
          </p>
        )}
      </div>

      {twoCol ? (
        <div className="mx-auto grid w-full max-w-6xl gap-2.5 lg:grid-cols-2 lg:gap-x-6">
          <ol className="grid content-start gap-2.5">
            {leftCol.map((row) => <BigScreenRow key={row.rank} row={row} top={top} dense />)}
          </ol>
          <ol className="grid content-start gap-2.5">
            {rightCol.map((row) => <BigScreenRow key={row.rank} row={row} top={top} dense />)}
          </ol>
        </div>
      ) : (
        <ol className="mx-auto grid w-full max-w-4xl gap-3">
          {leftCol.map((row) => <BigScreenRow key={row.rank} row={row} top={top} dense={false} />)}
        </ol>
      )}

      <Teams state={state} />
    </div>
  )
}

function Teams({ state }: { state: LiveState }) {
  if (!state.teams || state.teams.length < 2) return null
  return (
    <div className="mx-auto flex flex-wrap justify-center gap-3">
      {state.teams.map((team, index) => (
        <span key={team.team} className="chip chip-accent lg:text-sm">{index + 1}. {team.team} · avg {formatScore(team.average)}</span>
      ))}
    </div>
  )
}

function Finale({ state }: { state: LiveState }) {
  const standings = state.standings ?? []
  const [first, second, third] = standings
  const runnersUp = standings.slice(3, 10)
  const podium = [second, first, third]
  const heights = ['h-24 lg:h-36', 'h-36 lg:h-48', 'h-16 lg:h-24']
  const missed = state.roomMissed ?? []

  useEffect(() => {
    if (!first || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setTimeout(() => {
      const colors = [getComputedStyle(document.documentElement).getPropertyValue('--brand').trim() || '#6d5ef8', '#ffb020', '#ffffff', '#2fbf71']
      void confetti({ particleCount: 130, spread: 85, origin: { x: 0.3, y: 0.45 }, colors, shapes: ['square'], scalar: 1.05 })
    }, 1150)
    return () => window.clearTimeout(timer)
  }, [first])

  return (
    <div className="grid min-h-0 flex-1 items-center gap-6 lg:grid-cols-2 lg:gap-8">
      <section className="flex min-h-0 flex-col items-center text-center">
        <h1 className="neon text-xl lg:text-4xl">Final results</h1>
        <div className="mt-6 flex items-end justify-center gap-3 lg:gap-5">
          {podium.map((person, index) =>
            person ? (
              <div key={index} className="flex w-28 flex-col items-center lg:w-44">
                <div className="zoom-in flex w-full flex-col items-center" style={{ animationDelay: `${[0.5, 1.1, 0][index]}s` }}>
                  {index === 1 && <IconCrown size={28} className="mb-1" style={{ color: '#ffd60a' }} />}
                  <p className="w-full max-w-full truncate text-lg font-bold lg:text-2xl">{person.name}</p>
                  <p className="mb-2 font-bold tabular-nums lg:text-xl" style={{ color: 'var(--accent-text)' }}>{formatScore(person.score)}</p>
                </div>
                <div
                  className={`podium-rise pixel grid w-full place-items-start justify-center rounded-t-[4px] border-2 pt-3 text-2xl lg:text-4xl ${heights[index]}`}
                  style={{
                    animationDelay: `${[0.4, 0.95, 0][index]}s`,
                    background: index === 1 ? 'var(--accent)' : 'var(--brand)',
                    color: index === 1 ? 'var(--accent-ink)' : 'var(--brand-ink)',
                    borderColor: index === 1 ? 'color-mix(in oklab, var(--accent) 55%, white)' : 'color-mix(in oklab, var(--brand) 55%, white)',
                    boxShadow: '5px 5px 0 #000',
                  }}
                >
                  {person.rank}
                </div>
              </div>
            ) : (
              <div key={index} className="w-28 lg:w-44" />
            ),
          )}
        </div>
        {runnersUp.length > 0 && (
          <ol className="mt-5 grid w-full max-w-xl grid-cols-2 gap-1.5 text-left">
            {runnersUp.map((row) => (
              <li key={row.rank} className="sb-row !py-1.5 !px-2.5">
                <span className="sb-rank !h-6 !min-w-[32px] !text-[9px]">#{row.rank}</span>
                <span className="min-w-0 flex-1 truncate text-sm font-bold lg:text-base">{row.name}</span>
                <span className="sb-score !text-sm lg:!text-base">{formatScore(row.score)}</span>
              </li>
            ))}
          </ol>
        )}
        <div className="mt-4"><Teams state={state} /></div>
      </section>
      <section className="card card-accent max-h-full overflow-y-auto p-6 lg:p-8">
        <h2 className="neon-accent text-sm lg:text-xl">What the room got wrong</h2>
        {missed.length === 0 ? (
          <p className="mt-4 text-lg text-muted">Nothing stood out. Suspiciously well read, all of you.</p>
        ) : (
          <ol className="mt-5 grid gap-5">
            {missed.map((item, index) => (
              <li key={index}>
                <p className="pixel text-[10px] lg:text-xs" style={{ color: 'var(--bad)' }}>{item.percentCorrect}% got it{item.section ? ` · ${item.section}` : ''}</p>
                <p className="mt-1 text-lg lg:text-2xl">{item.text}</p>
                <p className="mt-1 inline-flex items-center gap-2 text-lg lg:text-2xl" style={{ color: 'var(--good)' }}>
                  <IconCheck size={18} /> {item.correctAnswer}
                </p>
                {item.explanation && <p className="mt-1 text-muted lg:text-xl">{item.explanation}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}
