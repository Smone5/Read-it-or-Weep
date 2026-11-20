// A player's phone during a live game: four big buttons, your result, your rank.
// The question itself is on the big screen; the phone is the buzzer.

import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ErrorNote, FullScreenMessage, Logo, Spinner, Stage } from '../components/ui'
import { LETTERS, TONES } from '../game/parts'
import { api, ApiError, session } from '../lib/api'
import { buzz, play, unlockAudio } from '../lib/sound'
import { applyBranding, applyDecade } from '../lib/theme'
import type { Branding, LiveState } from '../lib/types'
import { useLive, useTicker } from './useLive'

type Info = { code: string; status: string; quizTitle: string; teams: string[]; branding: Branding }

export default function LivePlayer() {
  const { code: rawCode } = useParams()
  const navigate = useNavigate()
  const code = (rawCode ?? '').toUpperCase()
  const [info, setInfo] = useState<Info | null>(null)
  const [fatal, setFatal] = useState('')
  const [joined, setJoined] = useState(false)
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (!code) return
    api<Info>(`/api/live/${code}/info`, { as: 'none' })
      .then(async (data) => {
        applyBranding(data.branding)
        setInfo(data)
        if (session.playerToken) {
          // Already known on this phone: take a seat straight away.
          try {
            await api(`/api/live/${code}/join`, { body: {} })
            setJoined(true)
          } catch {
            session.setPlayerToken(null)
          }
        }
      })
      .catch((caught: Error) => setFatal(caught.message))
    return () => applyDecade(null)
  }, [code])

  if (!code) {
    return (
      <Stage className="grid place-items-center">
        <form className="card w-full max-w-sm p-6 text-center" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) navigate(`/live/${typed.trim().toUpperCase()}`) }}>
          <h1 className="neon text-base">Join the live game</h1>
          <label className="label mt-5" htmlFor="code">Code on the big screen</label>
          <input id="code" className="field pixel text-center !text-xl tracking-[0.3em]" value={typed} onChange={(e) => setTyped(e.target.value.toUpperCase())} maxLength={6} autoFocus autoCapitalize="characters" autoComplete="off" />
          <button className="btn btn-primary btn-lg mt-5 w-full" disabled={typed.trim().length < 4}>Join ▶</button>
        </form>
      </Stage>
    )
  }
  if (fatal) return <FullScreenMessage title="Hmm" action={<a className="btn btn-primary" href="/live">Try another code</a>}>{fatal}</FullScreenMessage>
  if (!info) return <Stage className="grid place-items-center"><Spinner /></Stage>
  if (!joined) return <Stage><JoinLive info={info} onJoined={() => setJoined(true)} /></Stage>
  return <Stage><Buzzer code={code} info={info} /></Stage>
}

function JoinLive({ info, onJoined }: { info: Info; onJoined: () => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [team, setTeam] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    unlockAudio()
    setBusy(true)
    setError('')
    try {
      const result = await api<{ token: string | null }>(`/api/live/${info.code}/join`, { as: 'none', body: { name, email, team: team || null } })
      if (result.token) session.setPlayerToken(result.token)
      play('levelup')
      onJoined()
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-md flex-col justify-center gap-4" onSubmit={submit}>
      <header className="text-center">
        <div className="mb-3 flex justify-center"><Logo branding={info.branding} size={48} /></div>
        <p className="chip chip-accent">Live game · {info.code}</p>
        <h1 className="neon mt-3 text-base sm:text-xl">{info.quizTitle}</h1>
      </header>
      <div className="card grid gap-4 p-5">
        <div>
          <label className="label" htmlFor="live-name">Your name</label>
          <input id="live-name" className="field" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={60} autoComplete="name" />
        </div>
        <div>
          <label className="label" htmlFor="live-email">Work email</label>
          <input id="live-email" className="field" type="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        </div>
        {info.teams.length > 0 && (
          <div>
            <label className="label" htmlFor="live-team">Team</label>
            <select id="live-team" className="field" value={team} onChange={(e) => setTeam(e.target.value)} required>
              <option value="" disabled>Pick your team</option>
              {info.teams.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
        )}
        <ErrorNote>{error}</ErrorNote>
        <button className="btn btn-primary btn-lg" disabled={busy}>{busy ? 'Joining…' : 'Join the game ▶'}</button>
      </div>
    </form>
  )
}

function Buzzer({ code, info }: { code: string; info: Info }) {
  const { state, error, until } = useLive({ code, enabled: true })
  const [picked, setPicked] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [notSent, setNotSent] = useState(false)
  const lastIndex = useRef(-2)
  const lastStatus = useRef('')
  useTicker(state?.status === 'question')

  useEffect(() => {
    if (!state) return
    applyDecade(state.decade)
    if (state.index !== lastIndex.current) {
      lastIndex.current = state.index
      setPicked(null)
      setNotSent(false)
    }
    if (state.status !== lastStatus.current) {
      lastStatus.current = state.status
      if (state.status === 'reveal' && state.yourAnswer) {
        play(state.yourAnswer.correct ? 'correct' : 'wrong')
        buzz(state.yourAnswer.correct ? 40 : [70, 40, 70])
      } else if (state.status === 'question') buzz(25)
      else if (state.status === 'finished') play('cheer')
    }
  }, [state])

  if (error) return <FullScreenMessage title="Lost the game">{error}</FullScreenMessage>
  if (!state) return <div className="grid min-h-[70dvh] place-items-center"><Spinner label="Connecting" /></div>

  const you = state.you
  const header = (
    <header className="mb-4 flex items-center justify-between gap-3">
      <div className="hud">Code<b>{state.code}</b></div>
      {you && <div className="hud text-center">Rank<b style={{ color: 'var(--accent)' }}>#{you.rank}</b></div>}
      {you && <div className="hud text-right">Score<b className="neon-accent">{String(you.score).padStart(5, '0')}</b></div>}
    </header>
  )

  const answer = async (index: number) => {
    if (picked !== null || sending) return
    setPicked(index)
    setSending(true)
    play('click')
    buzz(20)
    try {
      // Sending the same answer twice changes nothing, so a dropped connection is simply tried again.
      await api(`/api/live/${code}/answer`, { body: { choiceIndex: index }, retry: 3 })
      setNotSent(false)
    } catch (caught) {
      // "Too late" stands. Anything else means the answer never arrived: unlock so they can tap again.
      if (!(caught instanceof ApiError) || caught.status !== 409) {
        setPicked(null)
        setNotSent(true)
      }
    } finally {
      setSending(false)
    }
  }

  let body: React.ReactNode = null
  if (state.status === 'lobby') {
    body = (
      <div className="grid flex-1 place-items-center text-center">
        <div className="rise">
          <p className="pixel blink text-[10px] text-muted">Waiting for the host</p>
          <h1 className="neon mt-4 text-lg">You're in{you ? `, ${you.name.split(' ')[0]}` : ''}!</h1>
          <p className="mt-4 text-muted">Eyes on the big screen. {state.players} player{state.players === 1 ? '' : 's'} so far.</p>
          {you && you.headStart > 0 && (
            <p className="chip chip-accent mt-5"><span className="glyph">★</span> You did the pre-read: +{you.headStart} head start</p>
          )}
        </div>
      </div>
    )
  } else if (state.status === 'question' && state.question) {
    const question = state.question
    const opensIn = until(question.opensAt)
    const left = Math.max(0, until(question.endsAt))
    const total = question.limitSec * 1000
    const locked = picked !== null || state.yourAnswer?.selectedIndex !== undefined && state.yourAnswer?.selectedIndex !== null
    const chosen = picked ?? state.yourAnswer?.selectedIndex ?? null
    body = (
      <div className="flex flex-1 flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <span className="chip">Question {question.number}/{state.total}</span>
          <span className="pixel text-sm" style={{ color: left < 5000 ? 'var(--bad)' : 'var(--accent)' }}>{opensIn > 0 ? '' : Math.ceil(left / 1000)}</span>
        </div>
        <p className="text-[17px] leading-snug">{question.kind === 'music' ? '🎵 ' : ''}{question.text}</p>
        {notSent && <p className="pixel blink text-[10px]" style={{ color: 'var(--bad)' }} role="alert">That did not send – tap your answer again</p>}
        {opensIn <= 0 && (
          <div className="timer-track"><div className="timer-fill" data-urgent={left < 5000} style={{ transform: `scaleX(${total ? left / total : 0})` }} /></div>
        )}
        {opensIn > 0 ? (
          <div className="grid flex-1 place-items-center"><p className="pixel neon blink text-xl">Get ready</p></div>
        ) : locked ? (
          <div className="grid flex-1 place-items-center text-center">
            <div className="zoom-in">
              <span className="pixel grid h-20 w-20 place-items-center rounded text-2xl text-white" style={{ background: TONES[chosen ?? 0], boxShadow: '5px 5px 0 #000', margin: '0 auto' }}>{LETTERS[chosen ?? 0]}</span>
              <p className="pixel mt-5 text-xs">Locked in</p>
              <p className="mt-2 text-muted">Waiting for everyone else…</p>
            </div>
          </div>
        ) : (
          <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
            {question.options.map((option, index) => (
              <button key={index} className="tile min-h-[68px] px-4 py-3 text-[17px]" style={{ ['--tone' as string]: TONES[index % 4] }} onClick={() => void answer(index)}>
                <span className="pixel grid h-9 w-9 flex-none place-items-center rounded-sm bg-black/30 text-sm">{LETTERS[index]}</span>
                <span className="min-w-0 flex-1 break-words">{option}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    )
  } else if (state.status === 'reveal' && state.question && state.reveal) {
    const mine = state.yourAnswer
    const correct = Boolean(mine?.correct)
    body = (
      <div className="grid flex-1 place-items-center text-center">
        <div className="zoom-in w-full">
          <h1 className="text-2xl" style={{ color: correct ? 'var(--good)' : 'var(--bad)' }}>{correct ? 'Correct!' : mine?.selectedIndex == null ? 'No answer' : 'Wrong'}</h1>
          {correct && <p className="pixel neon-accent mt-4 text-lg">+{mine?.points}</p>}
          <div className="card mx-auto mt-6 max-w-sm p-4 text-left">
            <p className="pixel text-[8px] text-muted">The answer</p>
            <p className="mt-1 text-lg">{state.reveal.music ? `${state.reveal.music.title} – ${state.reveal.music.artist}` : state.question.options[state.reveal.correctIndex]}</p>
            {state.reveal.explanation && <p className="mt-2 text-[15px] text-muted">{state.reveal.explanation}</p>}
          </div>
        </div>
      </div>
    )
  } else if (state.status === 'scoreboard' || state.status === 'finished') {
    const finished = state.status === 'finished'
    body = (
      <div className="flex flex-1 flex-col justify-center gap-4 text-center">
        <p className="pixel blink text-[10px] text-muted">{finished ? 'Game over' : 'Scoreboard'}</p>
        {you ? (
          <>
            <h1 className="neon text-3xl">#{you.rank}</h1>
            <p className="text-muted">of {state.players} · {you.score} points{you.streak >= 2 ? ` · ${you.streak} in a row` : ''}</p>
          </>
        ) : null}
        <ol className="card mx-auto grid w-full max-w-sm gap-1.5 p-4 text-left">
          {(state.standings ?? []).map((row) => (
            <li key={row.rank} className="flex items-center gap-3" style={you && row.name === you.name && row.score === you.score ? { color: 'var(--accent)' } : undefined}>
              <span className="pixel w-8 text-[10px]">{String(row.rank).padStart(2, '0')}</span>
              <span className="flex-1 truncate">{row.name}</span>
              <span className="pixel text-[10px]">{row.score}</span>
            </li>
          ))}
        </ol>
        {finished && <a className="btn btn-primary btn-lg" href={`/q/${state.quizSlug}`}>Play the full quiz ▶</a>}
      </div>
    )
  }

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-xl flex-col">
      {header}
      {body}
      <p className="mt-4 text-center text-xs text-muted">{info.quizTitle}</p>
    </div>
  )
}

export type { LiveState }
