// A player's phone during a live game: four big buttons, your result, your rank.
// The question itself is on the big screen; the phone is the buzzer.

import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { IconBolt, IconCheck, IconCrown, IconMusic, IconPlay, IconStar } from '../components/Icons'
import { ErrorNote, FullScreenMessage, Logo, Spinner, Stage } from '../components/ui'
import { formatScore, LETTERS, PictureView, TONE_INKS, TONES } from '../game/parts'
import { api, ApiError, session } from '../lib/api'
import { buzz } from '../lib/sound'
import { applyBranding, applyDecade } from '../lib/theme'
import type { Branding, LiveState } from '../lib/types'
import { useLive, useTicker } from './useLive'

type Info = { code: string; status: string; quizTitle: string; teams: string[]; requiresCode?: boolean; branding: Branding }

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
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) {
              session.setPlayerToken(null)
            }
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
          <input id="code" className="field pixel text-center text-xl tracking-[0.3em]" value={typed} onChange={(e) => setTyped(e.target.value.toUpperCase())} maxLength={6} autoFocus autoCapitalize="characters" autoComplete="off" />
          <button className="btn btn-primary btn-lg mt-5 w-full" disabled={typed.trim().length < 4}>
            Join <IconPlay size={14} />
          </button>
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
  const [accessCode, setAccessCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await api<{ token: string | null }>(`/api/live/${info.code}/join`, {
        as: 'none',
        body: { name, email, team: team || null, accessCode: accessCode || null },
      })
      if (result.token) session.setPlayerToken(result.token)
      buzz(25)
      onJoined()
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-4 py-4" onSubmit={submit}>
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
        {info.requiresCode && (
          <div>
            <label className="label" htmlFor="live-access-code">Access code</label>
            <input id="live-access-code" className="field" value={accessCode} onChange={(e) => setAccessCode(e.target.value)} required maxLength={40} autoComplete="off" />
          </div>
        )}
        <ErrorNote>{error}</ErrorNote>
        <button className="btn btn-primary btn-lg" disabled={busy}>
          {busy ? 'Joining…' : <>Join the game <IconPlay size={14} /></>}
        </button>
      </div>
    </form>
  )
}

function Buzzer({ code, info }: { code: string; info: Info }) {
  const { state, error, until } = useLive({ code, enabled: true })
  const [picked, setPicked] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [notSent, setNotSent] = useState(false)
  const [showAllScores, setShowAllScores] = useState(false)
  const [showMissed, setShowMissed] = useState(false)
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
      setShowAllScores(false)
    }
    if (state.status !== lastStatus.current) {
      lastStatus.current = state.status
      // Phones stay silent haptic buzzers in live rooms so 50 phones never step over the big screen's host & music.
      if (state.status === 'reveal' && state.yourAnswer) {
        buzz(state.yourAnswer.correct ? 40 : [70, 40, 70])
      } else if (state.status === 'question') buzz(25)
      else if (state.status === 'finished') buzz([35, 45, 70])
    }
  }, [state])

  if (error) return <FullScreenMessage title="Lost the game">{error}</FullScreenMessage>
  if (!state) return <div className="grid min-h-[70dvh] place-items-center"><Spinner label="Connecting" /></div>

  const you = state.you
  const header = (
    <header className="mb-4 flex items-center justify-between gap-3">
      <div className="hud">Code<b>{state.code}</b></div>
      {you && <div className="hud text-center">Rank<b style={{ color: 'var(--accent-text)' }}>#{you.rank}</b></div>}
      {you && <div className="hud text-right">Score<b className="neon-accent">{String(you.score).padStart(5, '0')}</b></div>}
    </header>
  )

  const answer = async (index: number) => {
    if (picked !== null || sending) return
    setPicked(index)
    setSending(true)
    buzz(20)
    try {
      // Sending the same answer twice changes nothing, so a dropped connection is simply tried again.
      try {
        await api(`/api/live/${code}/answer`, { body: { choiceIndex: index }, retry: 3 })
      } catch (firstErr) {
        // 425 = phone clock was slightly ahead of server's unlock timestamp; retry once after a short beat.
        if (firstErr instanceof ApiError && firstErr.status === 425) {
          await new Promise((r) => window.setTimeout(r, 280))
          await api(`/api/live/${code}/answer`, { body: { choiceIndex: index }, retry: 1 })
        } else {
          throw firstErr
        }
      }
      setNotSent(false)
    } catch (caught) {
      // 409 ("Too late") stands. Anything else (including 425 still early) means the answer was not recorded: unlock!
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
            <p className="chip chip-accent mt-5">
              <IconStar size={12} /> You did the pre-read: +{you.headStart} head start
            </p>
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
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="chip">Question {question.number}/{state.total}</span>
            {question.doublePoints && <span className="chip chip-accent"><IconBolt size={11} /> 2× PTS</span>}
          </div>
          <span className="pixel text-sm tabular-nums" style={{ color: left < 5000 ? 'var(--bad)' : 'var(--accent-text)' }}>{opensIn > 0 ? '' : Math.ceil(left / 1000)}</span>
        </div>
        <p className="text-[17px] leading-snug">
          {question.kind === 'music' && <IconMusic size={16} className="mr-1.5" style={{ color: 'var(--accent)' }} />}
          {question.text}
        </p>
        {question.imageUrl && (
          <PictureView
            image={{ url: question.imageUrl, alt: 'Chart from the report', mode: 'show' }}
            secondsLeft={opensIn <= 0 ? Math.ceil(left / 1000) : null}
            caption={question.text}
          />
        )}
        {notSent && <p className="pixel blink text-[10px]" style={{ color: 'var(--bad)' }} role="alert">That did not send – tap your answer again</p>}
        {opensIn <= 0 && (
          <div className="timer-track"><div className="timer-fill" data-urgent={left < 5000} style={{ transform: `scaleX(${total ? left / total : 0})` }} /></div>
        )}
        {opensIn > 0 ? (
          <div className="grid flex-1 place-items-center"><p className="pixel neon blink text-xl">Get ready</p></div>
        ) : locked ? (
          <div className="grid flex-1 place-items-center text-center">
            <div className="zoom-in">
              <span
                className="pixel grid h-20 w-20 place-items-center rounded text-2xl"
                style={{ background: TONES[(chosen ?? 0) % 4], color: TONE_INKS[(chosen ?? 0) % 4], boxShadow: '5px 5px 0 #000', margin: '0 auto' }}
              >
                {LETTERS[chosen ?? 0]}
              </span>
              <p className="pixel mt-5 text-xs">Locked in</p>
              <p className="mt-2 text-muted">Waiting for everyone else…</p>
            </div>
          </div>
        ) : (
          <div className="grid flex-1 grid-cols-1 gap-3 min-[480px]:grid-cols-2">
            {question.options.map((option, index) => (
              <button
                key={index}
                className="tile min-h-[68px] px-4 py-3 text-[17px]"
                style={{ ['--tone' as string]: TONES[index % 4], ['--tone-ink' as string]: TONE_INKS[index % 4] }}
                onClick={() => void answer(index)}
              >
                <span className="pixel grid h-9 w-9 flex-none place-items-center rounded-sm bg-black/25 text-sm">{LETTERS[index]}</span>
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
            <p className="text-xs font-bold tracking-wider text-muted uppercase">The answer</p>
            <p className="mt-1 text-lg">{state.reveal.music ? `${state.reveal.music.title} – ${state.reveal.music.artist}` : state.question.options[state.reveal.correctIndex]}</p>
            {state.reveal.explanation && <p className="mt-2 text-[15px] text-muted">{state.reveal.explanation}</p>}
          </div>
        </div>
      </div>
    )
  } else if (state.status === 'scoreboard' || state.status === 'finished') {
    const finished = state.status === 'finished'
    const allStandings = state.standings ?? []
    const topScore = Math.max(1, allStandings[0]?.score ?? 0, you?.score ?? 0)
    const visible = showAllScores ? allStandings : allStandings.slice(0, 8)
    const isMeRow = (row: NonNullable<LiveState['standings']>[number]) =>
      Boolean(row.you ?? (you && row.rank === you.rank && row.name === you.name))
    const meInVisible = visible.some(isMeRow)

    let aroundRows: NonNullable<LiveState['standings']> = []
    if (you && !meInVisible) {
      const idx = allStandings.findIndex(isMeRow)
      if (idx >= 0) {
        aroundRows = allStandings.slice(Math.max(visible.length, idx - 1), idx + 2)
      } else if (state.aroundYou && state.aroundYou.length > 0) {
        aroundRows = state.aroundYou
      }
    }

    const renderRow = (row: NonNullable<LiveState['standings']>[number], keyPrefix = '') => {
      const mine = isMeRow(row)
      const rankColor = row.rank === 1 ? '#ffd60a' : row.rank === 2 ? '#e2e8f0' : row.rank === 3 ? '#f59e0b' : 'var(--muted)'
      return (
        <li key={`${keyPrefix}${row.rank}`} className="sb-row" data-you={mine}>
          <span className="sb-bar" style={{ transform: `scaleX(${Math.max(0.03, row.score / topScore)})` }} aria-hidden />
          <span className="sb-rank" style={{ color: rankColor, borderColor: row.rank <= 3 ? rankColor : undefined }}>
            {row.rank === 1 && <IconCrown size={11} />}
            {String(row.rank).padStart(2, '0')}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-bold text-ink">{row.name}</span>
              {mine && <span className="sb-you-pill">You</span>}
              {row.streak >= 3 && (
                <span className="inline-flex flex-none items-center text-xs font-bold" style={{ color: 'var(--accent)' }}>
                  <IconBolt size={11} />x{row.streak}
                </span>
              )}
            </div>
            {row.team && <p className="truncate text-xs text-muted">{row.team}</p>}
          </div>
          <span className="sb-score" style={{ color: mine ? 'var(--accent)' : row.rank === 1 ? '#ffd60a' : 'var(--ink)' }}>
            {formatScore(row.score)}
          </span>
        </li>
      )
    }

    const finaleHeading = !finished
      ? 'Scoreboard'
      : you?.rank === 1
        ? 'Champion!'
        : you?.rank === 2
          ? 'Runner-up!'
          : you?.rank === 3
            ? 'Podium finish!'
            : 'Final standings'

    body = (
      <div className="flex flex-1 flex-col justify-center gap-3.5">
        <p className="pixel blink text-center text-[10px]" style={{ color: finished && you && you.rank <= 3 ? '#ffd60a' : 'var(--muted)' }}>
          {finaleHeading}
        </p>

        {you && (
          <div className="sb-you-card flex items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-3">
              <span
                className="pixel grid h-12 min-w-[54px] place-items-center rounded-[4px] border-2 px-2 text-base tabular-nums"
                style={{
                  borderColor: 'var(--accent)',
                  background: 'rgb(0 0 0 / 0.5)',
                  color: you.rank === 1 ? '#ffd60a' : 'var(--accent)',
                }}
              >
                #{you.rank}
              </span>
              <div className="min-w-0">
                <p className="pixel text-[9px] text-muted">Your standing · of {state.players}</p>
                <p className="mt-0.5 text-sm text-ink">
                  {you.rank === 1 ? (
                    <span className="inline-flex items-center gap-1" style={{ color: '#ffd60a' }}>
                      <IconCrown size={13} /> Leading the room!
                    </span>
                  ) : you.gapToNext !== undefined ? (
                    <>
                      <b style={{ color: 'var(--accent)' }}>{formatScore(you.gapToNext)}</b> pts behind {you.aheadName ?? `#${you.rank - 1}`} (#{you.rank - 1})
                    </>
                  ) : (
                    `Rank #${you.rank} of ${state.players}`
                  )}
                </p>
                {you.streak >= 2 && (
                  <p className="mt-0.5 inline-flex items-center gap-1 text-xs" style={{ color: 'var(--accent)' }}>
                    <IconBolt size={11} /> {you.streak} in a row
                  </p>
                )}
              </div>
            </div>
            <div className="text-right">
              <p className="pixel text-[9px] text-muted">Score</p>
              <p className="sb-score !text-xl" style={{ color: 'var(--accent)' }}>{formatScore(you.score)}</p>
            </div>
          </div>
        )}

        <section className="card flex flex-col gap-2.5 p-3.5 text-left">
          <div className="flex items-center justify-between gap-2">
            <h2 className="pixel text-[10px] text-muted">Standings</h2>
            <span className="text-xs text-muted">
              Showing {visible.length} of {state.players}
            </span>
          </div>
          <div className="scroll-thin max-h-[42dvh] overflow-y-auto pr-0.5" role="region" aria-label="Live standings" tabIndex={0}>
            <ol className="grid gap-1.5">
              {visible.map((row) => renderRow(row))}
            </ol>
            {aroundRows.length > 0 && (
              <div className="mt-2">
                <div className="my-1.5 flex items-center gap-2 text-xs text-muted" aria-hidden>
                  <span className="h-px flex-1 bg-line" />
                  <span className="pixel text-[9px]">Around you</span>
                  <span className="h-px flex-1 bg-line" />
                </div>
                <ol className="grid gap-1.5">
                  {aroundRows.map((row) => renderRow(row, 'near-'))}
                </ol>
              </div>
            )}
          </div>
          {allStandings.length > 8 && (
            <button type="button" className="btn btn-ghost btn-sm w-full" onClick={() => setShowAllScores((open) => !open)}>
              {showAllScores ? 'Show top 8' : `Show top ${allStandings.length} scores`}
            </button>
          )}
        </section>

        {finished && (state.roomMissed?.length ?? 0) > 0 && (
          <section className="card card-accent p-3.5 text-left">
            <button
              type="button"
              className="flex min-h-[32px] w-full items-center justify-between gap-2 text-left"
              onClick={() => setShowMissed((v) => !v)}
              aria-expanded={showMissed}
            >
              <span className="pixel text-[10px]" style={{ color: 'var(--accent-text)' }}>
                What the room missed ({state.roomMissed!.length})
              </span>
              <span className="pixel text-xs" aria-hidden>{showMissed ? '−' : '+'}</span>
            </button>
            {showMissed && (
              <ol className="mt-3 grid gap-3">
                {state.roomMissed!.map((item, idx) => (
                  <li key={idx} className="border-l-2 pl-2.5" style={{ borderColor: 'var(--accent)' }}>
                    <p className="text-xs font-bold" style={{ color: 'var(--bad)' }}>{item.percentCorrect}% got it{item.section ? ` · ${item.section}` : ''}</p>
                    <p className="mt-0.5 text-sm">{item.text}</p>
                    <p className="mt-1 inline-flex items-center gap-1.5 text-sm font-bold" style={{ color: 'var(--good)' }}>
                      <IconCheck size={13} /> {item.correctAnswer}
                    </p>
                    {item.explanation && <p className="mt-0.5 text-xs text-muted">{item.explanation}</p>}
                  </li>
                ))}
              </ol>
            )}
          </section>
        )}

        {finished && (
          <a className="btn btn-primary btn-lg" href={`/q/${state.quizSlug}`}>
            Play the full quiz <IconPlay size={14} />
          </a>
        )}
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col">
      {header}
      {body}
      <p className="mt-4 text-center text-xs text-muted">{info.quizTitle}</p>
    </div>
  )
}

export type { LiveState }
