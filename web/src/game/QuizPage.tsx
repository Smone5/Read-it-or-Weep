// A quiz from the player's side: join → lobby → game → results.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ErrorNote, FullScreenMessage, Logo, Modal, SoundToggle, Spinner, Stage, untilText } from '../components/ui'
import { startAfterParty, stopAfterParty } from '../lib/afterParty'
import { api, ApiError, session } from '../lib/api'
import { HostVoice, type TalkState } from '../lib/hostVoice'
import { unlockMusic } from '../lib/music'
import { play, preloadSounds, unlockAudio } from '../lib/sound'
import { applyBranding, applyDecade, DECADE_FLAVOUR, DECADE_LABEL } from '../lib/theme'
import type { Leaderboard, ModeId, PlayerView, QuizInfo, Summary as SummaryData, Turn } from '../lib/types'
import { HighScores, ReadinessMap, type TalkControl } from './parts'
import Play from './Play'
import Summary from './Summary'

type Screen = 'loading' | 'join' | 'lobby' | 'play' | 'summary'

const HOST_KEY = 'quiz.host'

function savedHost(): string | null {
  try {
    return localStorage.getItem(HOST_KEY)
  } catch {
    return null
  }
}

export default function QuizPage() {
  const { slug = '' } = useParams()
  const [info, setInfo] = useState<QuizInfo | null>(null)
  const [screen, setScreen] = useState<Screen>('loading')
  const [fatal, setFatal] = useState('')
  const [turn, setTurn] = useState<Turn | null>(null)
  const [summary, setSummary] = useState<SummaryData | null>(null)
  const [hostLine, setHostLine] = useState('')
  const [hostSpeaking, setHostSpeaking] = useState(false)
  const hostRef = useRef<HostVoice | null>(null)
  const partyFor = useRef<string | null>(null) // the game whose results-screen music is due (or playing)
  const [resumed, setResumed] = useState(false)
  const [talkState, setTalkState] = useState<TalkState>('idle')
  const [talkAvailable, setTalkAvailable] = useState(false)
  const [talkProblem, setTalkProblem] = useState('')
  const [hostId, setHostId] = useState<string | null>(savedHost)

  const chooseHost = (id: string) => {
    setHostId(id)
    try {
      localStorage.setItem(HOST_KEY, id)
    } catch {
      /* ignore */
    }
  }

  const load = useCallback(async () => {
    try {
      const data = await api<QuizInfo>(`/api/quiz/${slug}`)
      applyBranding(data.branding)
      setInfo(data)
      setScreen((current) => (current === 'play' || current === 'summary' ? current : data.player ? 'lobby' : 'join'))
    } catch (caught) {
      setFatal((caught as Error).message)
    }
  }, [slug])

  useEffect(() => {
    void load()
    preloadSounds()
    return () => {
      hostRef.current?.close()
      partyFor.current = null
      stopAfterParty(0)
      applyDecade(null)
    }
  }, [load])

  const closeHost = () => {
    hostRef.current?.close()
    hostRef.current = null
    setHostLine('')
    setHostSpeaking(false)
    setTalkAvailable(false)
    setTalkState('idle')
  }

  const begin = (first: Turn, isResume = false) => {
    partyFor.current = null
    stopAfterParty()
    closeHost()
    setResumed(isResume)
    applyDecade(first.state.decade)
    // The Straight Quiz has no host; everything else gets the voice channel.
    if (first.state.mode !== 'straight' && session.playerToken) {
      const host = new HostVoice(
        { runId: first.state.runId, token: session.playerToken, hostId },
        {
          onLine: setHostLine,
          onSpeaking: setHostSpeaking,
          onTalk: (state, available) => {
            setTalkState(state)
            setTalkAvailable(available)
            if (state !== 'idle') setTalkProblem('')
          },
          onTalkProblem: setTalkProblem,
        },
      )
      host.connect()
      hostRef.current = host
    }
    setTurn(first)
    setSummary(null)
    setScreen('play')
  }

  const start = async (mode: ModeId, decade: string | null) => {
    unlockAudio()
    unlockMusic()
    play('click')
    // The game is created here; its first question is dealt behind the opening title card, and its
    // clock starts only when that question appears.
    const first = await api<Turn>(`/api/quiz/${slug}/runs`, { body: { mode, decade } })
    begin(first)
  }

  const resume = async (runId: string) => {
    unlockAudio()
    unlockMusic()
    const first = await api<Turn>(`/api/runs/${runId}`)
    if (first.finished) await load()
    else begin(first, true)
  }

  const finished = (result: SummaryData) => {
    setSummary(result)
    setScreen('summary')
    void load()
    // The ending, in order: the game-over sting, then the host's sign-off (applause can run under
    // him), and only then the music – hits from the game's decade, swelling in behind the results.
    const party = result.afterParty
    partyFor.current = result.runId
    const music = () => {
      if (party && partyFor.current === result.runId) startAfterParty(party.tracks)
    }
    const host = hostRef.current
    if (result.status !== 'completed' || !host) {
      window.setTimeout(music, 2200)
      return
    }
    const celebrating = result.personalBest && result.score > 0
    window.setTimeout(() => {
      host.cue('summary')
      void host.whenQuiet().then(music)
    }, celebrating ? 500 : 1500)
  }

  const backToLobby = () => {
    partyFor.current = null
    stopAfterParty()
    closeHost()
    applyDecade(null)
    setScreen('lobby')
    void load()
  }

  if (fatal) {
    return <FullScreenMessage title="Hold on" action={<a className="btn btn-primary" href="/">Start page</a>}>{fatal}</FullScreenMessage>
  }
  if (!info || screen === 'loading') {
    return <Stage className="grid place-items-center"><Spinner /></Stage>
  }

  const hostName = info.branding.host.choices.find((choice) => choice.id === hostId)?.name ?? info.branding.host.name
  const talk: TalkControl | null = talkAvailable
    ? {
        state: talkState,
        problem: talkProblem || undefined,
        onPress: () => void hostRef.current?.startTalking(),
        onRelease: () => void hostRef.current?.stopTalking(),
      }
    : null
  return (
    <Stage>
      {screen === 'join' && <JoinForm info={info} onJoined={() => void load().then(() => setScreen('lobby'))} />}
      {screen === 'lobby' && info.player && (
        <Lobby info={info} player={info.player} hostId={hostId} onChooseHost={chooseHost} onStart={start} onResume={resume} onSwitchPlayer={() => { session.setPlayerToken(null); setInfo({ ...info, player: null }); setScreen('join') }} />
      )}
      {screen === 'play' && turn && (
        <Play key={turn.state.runId} firstTurn={turn} resumed={resumed} hostName={hostName} hostLine={hostLine} hostSpeaking={hostSpeaking} host={hostRef.current} talk={talk} onDone={finished} />
      )}
      {screen === 'summary' && summary && (
        <Summary slug={slug} summary={summary} hostName={hostName} hostLine={hostLine} hostSpeaking={hostSpeaking} talk={talk} showLeaderboard={info.quiz.leaderboard} onPlayAgain={backToLobby} />
      )}
    </Stage>
  )
}

// --------------------------------------------------------------------------- join

function JoinForm({ info, onJoined }: { info: QuizInfo; onJoined: () => void }) {
  const { quiz, branding } = info
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [team, setTeam] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const meeting = untilText(quiz.meetingAt)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await api<{ token: string; player: PlayerView }>(`/api/quiz/${quiz.slug}/join`, {
        as: 'none',
        headers: session.adminToken ? { 'X-Admin-Token': session.adminToken } : undefined,
        body: { name, email, team: team || null, accessCode: code || null },
      })
      session.setPlayerToken(result.token)
      play('levelup')
      onJoined()
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-md flex-col justify-center gap-5">
      <header className="text-center">
        <div className="mb-4 flex justify-center"><Logo branding={branding} size={52} /></div>
        <h1 className="neon text-lg sm:text-2xl">{branding.gameTitle}</h1>
        <p className="mt-3 text-muted">{branding.tagline}</p>
      </header>

      <form className="card rise grid gap-4 p-5" onSubmit={submit}>
        <div>
          <p className="chip chip-accent">{quiz.status === 'draft' ? 'Play-test · not open yet' : 'Now playing'}</p>
          <h2 className="mt-3 !normal-case text-[21px]" style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.3 }}>{quiz.title}</h2>
          {quiz.description && <p className="mt-1 text-[15px] text-muted">{quiz.description}</p>}
          {meeting && <p className="mt-2 text-[15px]" style={{ color: 'var(--accent)' }}>Meeting {meeting}</p>}
        </div>
        <div>
          <label className="label" htmlFor="join-name">Your name</label>
          <input id="join-name" className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required minLength={2} maxLength={60} placeholder="Dana Reyes" />
        </div>
        <div>
          <label className="label" htmlFor="join-email">Work email</label>
          <input id="join-email" className="field" type="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required placeholder="you@company.com" />
        </div>
        {quiz.teams.length > 0 && (
          <div>
            <label className="label" htmlFor="join-team">Team</label>
            <select id="join-team" className="field" value={team} onChange={(e) => setTeam(e.target.value)} required>
              <option value="" disabled>Pick your team</option>
              {quiz.teams.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        )}
        {quiz.requiresCode && (
          <div>
            <label className="label" htmlFor="join-code">Access code</label>
            <input id="join-code" className="field" value={code} onChange={(e) => setCode(e.target.value)} required autoCapitalize="none" placeholder="From your invitation" />
          </div>
        )}
        <ErrorNote>{error}</ErrorNote>
        <button className="btn btn-primary btn-lg" disabled={busy}>{busy ? 'Joining…' : 'Insert coin ▶'}</button>
        <p className="text-center text-sm text-muted">No password. Your results are saved under your email so the organiser can see who is ready.</p>
      </form>
    </div>
  )
}

// --------------------------------------------------------------------------- lobby

type LobbyProps = {
  info: QuizInfo
  player: PlayerView
  hostId: string | null
  onChooseHost: (id: string) => void
  onStart: (mode: ModeId, decade: string | null) => Promise<void>
  onResume: (runId: string) => Promise<void>
  onSwitchPlayer: () => void
}

const REGISTER_LABEL: Record<string, string> = { deeper: 'Deeper voice', middle: 'Mid voice', brighter: 'Brighter voice' }

function Lobby({ info, player, hostId, onChooseHost, onStart, onResume, onSwitchPlayer }: LobbyProps) {
  const { quiz, branding } = info
  const [mode, setMode] = useState<ModeId>(quiz.defaultMode)
  const [decade, setDecade] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [boardOpen, setBoardOpen] = useState(false)
  const chosen = quiz.modes.find((m) => m.id === mode) ?? quiz.modes[0]
  const wantsDecade = chosen?.music && quiz.decades.length > 0
  const meeting = untilText(quiz.meetingAt)
  const firstName = player.name.split(' ')[0]

  useEffect(() => {
    applyDecade(wantsDecade ? decade : null)
  }, [decade, wantsDecade])

  const go = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not start the game. Please try again.')
      setBusy(false)
    }
  }

  const openBoard = async () => {
    setBoardOpen(true)
    setBoard(await api<Leaderboard>(`/api/quiz/${quiz.slug}/leaderboard?mode=${mode}`).catch(() => null))
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 pb-8">
      <header className="flex items-center gap-3">
        <Logo branding={branding} size={40} />
        <div className="min-w-0 flex-1">
          <p className="pixel truncate text-[9px] text-muted">{branding.companyName}</p>
          <p className="pixel neon truncate text-[11px] sm:text-sm">{branding.gameTitle}</p>
        </div>
        <SoundToggle />
      </header>

      <section className="card rise p-5">
        <p className="pixel text-[9px] text-muted">Player 1</p>
        <h1 className="mt-2 text-base sm:text-xl">Ready, {firstName}?</h1>
        <p className="mt-3 !normal-case text-[19px]" style={{ fontFamily: 'var(--font-sans)' }}>{quiz.title}</p>
        {meeting && <p className="mt-1" style={{ color: 'var(--accent)' }}>Meeting {meeting}</p>}
        {!quiz.ready && <p className="mt-3" style={{ color: 'var(--bad)' }}>This quiz is still being prepared. Check back soon.</p>}
      </section>

      {player.readiness.total > 0 && (
        <section className="card p-5">
          <ReadinessMap readiness={player.readiness} />
        </section>
      )}

      {quiz.modes.length > 1 && (
        <section>
          <p className="pixel mb-2 text-[9px] text-muted">Choose your game</p>
          <div className="grid gap-2.5 sm:grid-cols-2">
            {quiz.modes.map((option) => (
              <button
                key={option.id}
                className="answer !items-start flex-col !gap-1.5"
                data-state={option.id === mode ? 'picked' : 'idle'}
                style={{ ['--tone' as string]: 'var(--brand)' }}
                onClick={() => { setMode(option.id); play('click') }}
                aria-pressed={option.id === mode}
              >
                <span className="pixel text-[11px]">{option.name}</span>
                <span className="text-[15px] text-muted">{option.tagline}</span>
                {player.best[option.id] !== undefined && <span className="pixel text-[8px]" style={{ color: 'var(--accent)' }}>Your best {player.best[option.id]}</span>}
              </button>
            ))}
          </div>
        </section>
      )}

      {wantsDecade && (
        <section>
          <p className="pixel mb-2 text-[9px] text-muted">Pick the music</p>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
            <button className="answer !min-h-[54px] justify-center !px-1" data-state={decade === null ? 'picked' : 'idle'} style={{ ['--tone' as string]: 'var(--accent)' }} onClick={() => setDecade(null)} aria-pressed={decade === null} title="Surprise me">
              <span className="pixel text-[10px]">Any</span>
            </button>
            {quiz.decades.map((d) => (
              <button key={d} className="answer !min-h-[54px] justify-center !px-1" data-state={decade === d ? 'picked' : 'idle'} style={{ ['--tone' as string]: 'var(--accent)' }} onClick={() => { setDecade(d); play('click') }} aria-pressed={decade === d} title={DECADE_LABEL[d]}>
                <span className="pixel text-[10px]">{d}</span>
              </button>
            ))}
          </div>
          <p className="mt-2 text-[15px] text-muted">{decade ? `${DECADE_LABEL[decade]} · ${DECADE_FLAVOUR[decade]}` : 'Surprise me'}</p>
        </section>
      )}

      {branding.host.choices.length > 1 && chosen?.id !== 'straight' && (
        <section>
          <p className="pixel mb-2 text-[9px] text-muted">Choose your host</p>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
            {branding.host.choices.map((choice, index) => {
              const selected = hostId ? choice.id === hostId : index === 0
              return (
                <button key={choice.id} className="answer !items-start flex-col !gap-1" data-state={selected ? 'picked' : 'idle'} style={{ ['--tone' as string]: 'var(--accent)' }} onClick={() => { onChooseHost(choice.id); play('click') }} aria-pressed={selected}>
                  <span className="pixel text-[11px]">{choice.name}</span>
                  <span className="text-[14px] text-muted">{REGISTER_LABEL[choice.register] ?? 'Host'}</span>
                </button>
              )
            })}
          </div>
        </section>
      )}

      <ErrorNote>{error}</ErrorNote>

      {player.unfinishedRun && (
        <button className="btn btn-accent btn-lg" disabled={busy} onClick={() => void go(() => onResume(player.unfinishedRun!.id))}>Continue your game ▶</button>
      )}
      <button className={`btn btn-primary btn-lg ${busy ? '' : 'blink-off'}`} disabled={busy || !quiz.ready} onClick={() => void go(() => onStart(mode, wantsDecade ? decade : null))}>
        {busy ? 'Loading…' : player.unfinishedRun ? 'Start a new game' : 'Press start ▶'}
      </button>
      <div className="grid grid-cols-2 gap-3">
        {quiz.leaderboard ? <button className="btn btn-ghost" onClick={() => void openBoard()}>High scores</button> : <span />}
        <button className="btn btn-ghost" onClick={onSwitchPlayer}>Not {firstName}?</button>
      </div>
      {branding.supportEmail && <p className="text-center text-sm text-muted">Trouble? <a className="underline" href={`mailto:${branding.supportEmail}`}>{branding.supportEmail}</a></p>}

      <Modal open={boardOpen} onClose={() => setBoardOpen(false)}>
        <div className="arcade">
          <h2 className="neon mb-4 text-sm">High scores · {chosen?.name}</h2>
          {board ? <HighScores board={board} /> : <Spinner />}
          <button className="btn btn-primary mt-5 w-full" onClick={() => setBoardOpen(false)}>Close</button>
        </div>
      </Modal>
    </div>
  )
}
