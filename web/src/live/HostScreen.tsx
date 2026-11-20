// The big screen for a live game: join code, the question, the reveal, the scoreboard.
// Opened from the admin with a one-time control key in the link; that key is what lets
// this screen (and only this screen) move the game on.
//
// Timing. The host reads each question to the room while the phones are still locked; when he
// has finished, this screen tells the server to open the question, and the clock (and a music
// clip) start together a beat later. Moving on while he is talking cuts him off cleanly.

import QRCode from 'qrcode'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { FullScreenMessage, SoundToggle, Spinner, Stage } from '../components/ui'
import { NowPlaying } from '../components/NowPlaying'
import { HostBox, LETTERS, TONES } from '../game/parts'
import { startParty, stopJukebox } from '../lib/jukebox'
import { api } from '../lib/api'
import { HostVoice } from '../lib/hostVoice'
import { fadeOutMusic, playClip, preloadClip, stopMusic, unlockMusic } from '../lib/music'
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
  const voice = useRef<HostVoice | null>(null)
  const seen = useRef('')
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

  const control = useCallback(
    async (action: 'next' | 'end') => {
      if (busy) return
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
      play('cheer')
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
      if ([' ', 'Enter', 'ArrowRight'].includes(event.key) && state && state.status !== 'finished') {
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
    lobby: busy ? 'Here we go…' : state.players ? 'Start the game ▶' : 'Waiting for players…',
    question: locked ? 'Start the clock ▶' : 'Reveal now ▶',
    reveal: 'Scoreboard ▶',
    scoreboard: state.index + 1 >= state.total ? 'Final results ▶' : 'Next question ▶',
  }

  return (
    <Stage>
      <div className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-[1500px] flex-col gap-3 lg:gap-4">
        <header className="flex items-center gap-4">
          <div className="min-w-0 flex-1">
            <p className="pixel truncate text-[10px] text-muted lg:text-xs">{branding?.companyName} · {state.quizTitle}</p>
            <p className="pixel neon truncate text-sm lg:text-xl">{branding?.gameTitle ?? 'Live game'}</p>
          </div>
          <div className="hud text-right">Join at {window.location.host}/live<b className="neon-accent !text-xl lg:!text-3xl">{state.code}</b></div>
          <div className="hud text-right">Players<b className="!text-xl lg:!text-3xl">{state.players}</b></div>
          <SoundToggle />
        </header>

        <main className="flex flex-1 flex-col">
          {state.status === 'lobby' && <LobbyView state={state} qr={qr} joinUrl={joinUrl} />}
          {(state.status === 'question' || state.status === 'reveal') && state.question && <QuestionView state={state} until={until} />}
          {state.status === 'scoreboard' && <Scoreboard state={state} />}
          {state.status === 'finished' && <Finale state={state} />}
        </main>

        <footer className="flex items-end gap-4">
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
                {nextLabel[state.status]}
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
    <div className="grid flex-1 items-center gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:gap-10">
      {/* Sized from the screen's height so the whole lobby fits a 720p projector without scrolling. */}
      <section className="card flex flex-col items-center gap-3 p-5 text-center lg:p-7">
        <p className="pixel text-xs text-muted lg:text-sm">Grab your phone and go to</p>
        <p className="pixel neon break-all text-sm lg:text-xl">{joinUrl.replace(/^https?:\/\//, '')}</p>
        {qr && <img src={qr} alt="QR code to join the game" className="rounded border-4 border-white" style={{ width: 'min(17rem, 30dvh)' }} />}
        <p className="pixel text-xs text-muted">or enter the code</p>
        <p className="pixel neon-accent tracking-[0.2em]" style={{ fontSize: 'clamp(2rem, 9dvh, 4.5rem)', lineHeight: 1.2 }}>{state.code}</p>
      </section>
      <section className="card flex min-h-[36dvh] flex-col p-5 lg:p-7">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-sm lg:text-xl">In the room</h2>
          <p className="pixel text-[10px] text-muted lg:text-xs"><span className="glyph" style={{ color: 'var(--accent)' }}>★</span> = did the pre-read{state.headStart ? ` (+${state.headStart} head start)` : ''}</p>
        </div>
        {roster.length === 0 ? (
          <p className="pixel blink m-auto text-xs text-muted lg:text-base">Waiting for the first player</p>
        ) : (
          <ul className="mt-5 flex flex-wrap content-start gap-2.5 lg:gap-3">
            {roster.map((person, index) => (
              <li key={`${person.name}-${index}`} className="zoom-in rounded-sm border-2 px-3 py-1.5 text-base lg:text-2xl" style={{ borderColor: person.prepared ? 'var(--accent)' : 'var(--line)', background: 'color-mix(in oklab, var(--page) 60%, transparent)' }}>
                {person.prepared && <span className="glyph" style={{ color: 'var(--accent)' }}>★ </span>}
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
  return (
    <div className="flex flex-1 flex-col gap-4 lg:gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="chip lg:!text-xs">Question {question.number} of {state.total}</span>
          {question.section && <span className="chip chip-accent lg:!text-xs">{question.section}</span>}
        </div>
        {!reveal && (
          <div className="flex items-center gap-5">
            <div className="hud text-right">Answers in<b className="!text-xl lg:!text-3xl">{answered}/{state.players}</b></div>
            <div className="pixel grid h-16 w-16 place-items-center rounded border-4 text-xl lg:h-24 lg:w-24 lg:text-3xl" style={{ borderColor: seconds <= 5 && opensIn <= 0 ? 'var(--bad)' : 'var(--accent)', color: seconds <= 5 && opensIn <= 0 ? 'var(--bad)' : 'var(--accent)' }}>
              {opensIn > 0 ? '…' : seconds}
            </div>
          </div>
        )}
      </div>

      <div className={`grid flex-1 items-center gap-5 ${hasPicture ? 'lg:grid-cols-2' : ''}`}>
        <div>
          {question.title && question.kind !== 'music' && <p className="pixel neon-accent mb-3 text-xs lg:text-base">“{question.title}”</p>}
          <h1 className="!normal-case text-2xl sm:text-3xl lg:text-[clamp(1.8rem,5.6dvh,3rem)]" style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.25 }}>{question.text}</h1>
          {question.kind === 'music' && !reveal && (
            <div className="mt-6 flex items-center gap-5">
              <div className="eq !h-20" data-on={opensIn <= 0}><i /><i /><i /><i /><i /></div>
              <p className="pixel neon-accent text-sm lg:text-xl">Listen up</p>
            </div>
          )}
          {reveal?.music && (
            <div className="zoom-in mt-6 flex items-center gap-5">
              {reveal.music.artworkUrl && <img src={reveal.music.artworkUrl} alt="" className="h-28 w-28 rounded-sm border-4 border-black lg:h-44 lg:w-44" />}
              <div>
                <p className="text-2xl lg:text-4xl">{reveal.music.title}</p>
                <p className="text-xl text-muted lg:text-3xl">{reveal.music.artist}</p>
              </div>
            </div>
          )}
          {reveal?.explanation && <p className="rise mt-5 text-lg text-muted lg:text-2xl">{reveal.explanation}</p>}
        </div>
        {hasPicture && <img src={question.imageUrl} alt="" className="picture-frame relative max-h-[46dvh] w-full rounded border-4 bg-white object-contain" style={{ borderColor: 'var(--accent)' }} />}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:gap-4">
        {question.options.map((option, index) => {
          const right = reveal && index === reveal.correctIndex
          return (
            <div key={index} className="tile relative min-h-[64px] overflow-hidden px-4 py-2.5 text-lg lg:min-h-[10dvh] lg:px-6 lg:text-[clamp(1.25rem,3.4dvh,1.9rem)]" data-dim={Boolean(reveal) && !right} style={{ ['--tone' as string]: TONES[index % 4] }}>
              {reveal && <span className="absolute inset-y-0 left-0 bg-black/25 transition-[width] duration-700" style={{ width: `${(reveal.counts[index] / most) * 100}%` }} aria-hidden />}
              <span className="pixel relative grid h-10 w-10 flex-none place-items-center rounded-sm bg-black/30 text-base lg:h-14 lg:w-14 lg:text-xl">{LETTERS[index]}</span>
              <span className="relative min-w-0 flex-1 break-words">{option}</span>
              {reveal && <span className="pixel relative text-sm lg:text-xl">{right ? '✔ ' : ''}{reveal.counts[index]}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Scoreboard({ state }: { state: LiveState }) {
  const standings = state.standings ?? []
  const top = Math.max(1, ...standings.map((s) => s.score))
  return (
    <div className="flex flex-1 flex-col justify-center gap-5">
      <h1 className="neon text-center text-xl lg:text-4xl">Scoreboard</h1>
      <ol className="mx-auto grid w-full max-w-4xl gap-3">
        {standings.map((row) => (
          <li key={row.rank} className="rise flex items-center gap-4">
            <span className="pixel w-12 text-base lg:text-2xl" style={{ color: 'var(--accent)' }}>{String(row.rank).padStart(2, '0')}</span>
            <div className="relative flex-1 overflow-hidden rounded-sm border-2 px-4 py-3 text-xl lg:text-3xl" style={{ borderColor: 'var(--line)' }}>
              <span className="absolute inset-y-0 left-0 transition-[width] duration-1000" style={{ width: `${(row.score / top) * 100}%`, background: 'color-mix(in oklab, var(--brand) 55%, transparent)' }} aria-hidden />
              <span className="relative">{row.name}{row.team ? <span className="text-muted"> · {row.team}</span> : null}{row.streak >= 3 ? <span style={{ color: 'var(--accent)' }}> x{row.streak}</span> : null}</span>
            </div>
            <span className="pixel w-28 text-right text-base lg:text-2xl">{row.score}</span>
          </li>
        ))}
      </ol>
      <Teams state={state} />
    </div>
  )
}

function Teams({ state }: { state: LiveState }) {
  if (!state.teams || state.teams.length < 2) return null
  return (
    <div className="mx-auto flex flex-wrap justify-center gap-3">
      {state.teams.map((team, index) => (
        <span key={team.team} className="chip chip-accent lg:!text-sm">{index + 1}. {team.team} · avg {team.average}</span>
      ))}
    </div>
  )
}

function Finale({ state }: { state: LiveState }) {
  const [first, second, third] = state.standings ?? []
  const podium = [second, first, third]
  const heights = ['h-28 lg:h-40', 'h-40 lg:h-56', 'h-20 lg:h-28']
  const missed = state.roomMissed ?? []
  return (
    <div className="grid flex-1 items-center gap-8 lg:grid-cols-2">
      <section className="text-center">
        <h1 className="neon text-xl lg:text-4xl">Final results</h1>
        <div className="mt-8 flex items-end justify-center gap-3 lg:gap-5">
          {podium.map((person, index) =>
            person ? (
              <div key={index} className="zoom-in flex w-28 flex-col items-center lg:w-48" style={{ animationDelay: `${[0.5, 1.1, 0][index]}s` }}>
                <p className="truncate text-lg lg:text-2xl">{person.name}</p>
                <p className="pixel mb-2 text-xs lg:text-base" style={{ color: 'var(--accent)' }}>{person.score}</p>
                <div className={`pixel grid w-full place-items-start justify-center rounded-t-sm pt-3 text-2xl lg:text-4xl ${heights[index]}`} style={{ background: index === 1 ? 'var(--accent)' : 'var(--brand)', color: index === 1 ? '#1c1300' : 'var(--brand-ink)', boxShadow: '5px 0 0 #000' }}>
                  {person.rank}
                </div>
              </div>
            ) : (
              <div key={index} className="w-28 lg:w-48" />
            ),
          )}
        </div>
        <div className="mt-6"><Teams state={state} /></div>
      </section>
      <section className="card card-accent p-6 lg:p-8">
        <h2 className="neon-accent text-sm lg:text-xl">What the room got wrong</h2>
        {missed.length === 0 ? (
          <p className="mt-4 text-lg text-muted">Nothing stood out. Suspiciously well read, all of you.</p>
        ) : (
          <ol className="mt-5 grid gap-5">
            {missed.map((item, index) => (
              <li key={index}>
                <p className="pixel text-[10px] lg:text-xs" style={{ color: 'var(--bad)' }}>{item.percentCorrect}% got it{item.section ? ` · ${item.section}` : ''}</p>
                <p className="mt-1 text-lg lg:text-2xl">{item.text}</p>
                <p className="mt-1 text-lg lg:text-2xl" style={{ color: 'var(--good)' }}>✔ {item.correctAnswer}</p>
                {item.explanation && <p className="mt-1 text-muted lg:text-xl">{item.explanation}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}
