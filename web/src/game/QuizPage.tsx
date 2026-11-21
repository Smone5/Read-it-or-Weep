// A quiz from the player's side: join → lobby → game → results.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { HostCrt, hostToneFor } from '../components/HostCrt'
import { IconCheck, IconPlay, IconStop, IconTrophy } from '../components/Icons'
import { ErrorNote, FullScreenMessage, Logo, Modal, SoundToggle, Spinner, Stage, untilText } from '../components/ui'
import { api, ApiError, session } from '../lib/api'
import { HostVoice, type TalkState } from '../lib/hostVoice'
import { setJukeboxLevel, startJukebox, startParty, stopJukebox } from '../lib/jukebox'
import { unlockMusic } from '../lib/music'
import { parseChallenge, type Challenge } from '../lib/shareCard'
import { play, preloadSounds, unlockAudio } from '../lib/sound'
import { applyBranding, applyDecade, DECADE_FLAVOUR, DECADE_LABEL, DECADE_SWATCH } from '../lib/theme'
import type { ModeId, PlayerView, QuizInfo, Soundtrack, Summary as SummaryData, Turn } from '../lib/types'
import { HighScoresScreen } from './HighScoresScreen'
import { ReadinessMap, type TalkControl } from './parts'
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

// The tester link is updated while people play. A page loaded before an update runs the old game against the new
// server, which can go wrong in new ways (a final question that never appeared, 2026-11-20). So between games - in
// the lobby, or when a game is started - the page checks whether it is still the current version, and reloads if
// not. Never in the middle of a game.
async function pageIsStale(): Promise<boolean> {
  if (!import.meta.env.PROD) return false
  const mine = [...document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]')]
    .map((script) => new URL(script.src, location.href).pathname)
    .find((path) => path.startsWith('/assets/'))
  if (!mine) return false
  try {
    const response = await fetch('/', { cache: 'no-store' })
    return response.ok && !(await response.text()).includes(mine)
  } catch {
    return false // offline or unreachable: carry on with what we have
  }
}

// The end of a game: a moment of quiet for the results' sting, then the music under the host's sign-off; on his last
// word it comes up big for a few seconds before settling into the after-party playlist.
const ENDING_QUIET_MS = 2500
const FINALE_MS = 5000

export default function QuizPage() {
  const { slug = '' } = useParams()
  const [searchParams] = useSearchParams()
  const challenge = useMemo(() => parseChallenge(searchParams), [searchParams])
  const [info, setInfo] = useState<QuizInfo | null>(null)
  const [screen, setScreen] = useState<Screen>('loading')
  const [fatal, setFatal] = useState('')
  const [turn, setTurn] = useState<Turn | null>(null)
  const [summary, setSummary] = useState<SummaryData | null>(null)
  const [hostLine, setHostLine] = useState('')
  const [hostSpeaking, setHostSpeaking] = useState(false)
  const hostRef = useRef<HostVoice | null>(null)
  const partyFor = useRef<string | null>(null) // the game whose results-screen music is due (or playing)
  const soundtrackFor = useRef<string | null>(null) // the game being played, while its soundtrack is wanted
  const [resumed, setResumed] = useState(false)
  const [talkState, setTalkState] = useState<TalkState>('idle')
  const [talkAvailable, setTalkAvailable] = useState(false)
  const [talkProblem, setTalkProblem] = useState('')
  const [hostId, setHostId] = useState<string | null>(savedHost)
  const [scoresView, setScoresView] = useState<{ from: 'lobby' | 'summary'; mode: ModeId } | null>(null)

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
    setScreen('loading')
    setInfo(null)
    setSummary(null)
    setTurn(null)
    setScoresView(null)
    setFatal('')
    void load()
    preloadSounds()
    return () => {
      hostRef.current?.close()
      partyFor.current = null
      soundtrackFor.current = null
      stopJukebox(0)
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
    stopJukebox()
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
    // The soundtrack: hits from the game's decade, from the opening to the results. The game tells it
    // where it is (a question, a gap, a music break); a game that cannot have music simply plays without.
    const runId = first.state.runId
    soundtrackFor.current = runId
    setJukeboxLevel('intro') // the show opens loud, on its own, until the host starts talking
    if (first.state.mode !== 'straight') {
      api<Soundtrack>(`/api/runs/${runId}/soundtrack`, { retry: 2 }).then(
        (music) => {
          if (soundtrackFor.current !== runId || !music.tracks.length) return
          // A game picked back up does not start its music from the top again.
          const from = isResume ? Math.floor(Math.random() * music.tracks.length) : 0
          startJukebox([...music.tracks.slice(from), ...music.tracks.slice(0, from)], {
            volume: music.volume,
            mix: music.mix,
            cues: isResume ? { ...music.cues, opener: undefined } : music.cues,
          })
        },
        () => undefined,
      )
    }
  }

  const lastStart = useRef<{ mode: ModeId; decade: string | null } | null>(null) // for "Play again"
  const start = async (mode: ModeId, decade: string | null) => {
    // Sound is unlocked first, inside the tap: a phone allows it only there, before anything is awaited.
    unlockAudio()
    unlockMusic()
    play('click')
    if (await pageIsStale()) {
      location.reload() // the game was updated since this page loaded: start the new version instead
      return
    }
    lastStart.current = { mode, decade }
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
    // The ending, in order: the game-over sting in a moment of quiet, then the host's sign-off with the music
    // coming up under him, then - on his last word - the music up big for a few seconds (a show ends on its biggest
    // music, not its smallest), settling into the results playlist. A game that had a soundtrack never goes silent:
    // the song that is playing carries on under all of it, and the playlist picks up when that song ends.
    const party = result.afterParty
    partyFor.current = result.runId
    soundtrackFor.current = null
    const ours = () => Boolean(party) && partyFor.current === result.runId
    if (party) {
      setJukeboxLevel('think')
      window.setTimeout(() => ours() && setJukeboxLevel('party'), ENDING_QUIET_MS)
    } else stopJukebox(1200) // no music on this quiz's results screen
    const music = () => {
      if (party && ours()) startParty(party.tracks)
    }
    const host = hostRef.current
    if (result.status !== 'completed' || !host) {
      window.setTimeout(music, 2200)
      return
    }
    const celebrating = result.personalBest && result.score > 0
    window.setTimeout(() => {
      if (!host.cue('summary')) {
        music() // no host to sign off after all
        return
      }
      void host.whenQuiet().then(() => {
        if (!ours()) return
        setJukeboxLevel('title')
        window.setTimeout(music, FINALE_MS)
      })
    }, celebrating ? 500 : 1500)
  }

  /** Leave the results screen: its music and its host stop. */
  const leaveResults = () => {
    partyFor.current = null
    soundtrackFor.current = null
    stopJukebox()
    closeHost()
    applyDecade(null)
  }

  const backToLobby = () => {
    leaveResults()
    setScreen('lobby')
    void load()
    void pageIsStale().then((stale) => stale && location.reload()) // between games: catch up with an update
  }

  /** Straight into a new game with the same style, decade and host (the main menu if this one was picked back up). */
  const playAgain = () => {
    const last = lastStart.current
    if (!last) {
      backToLobby()
      return
    }
    leaveResults()
    void start(last.mode, last.decade).catch(() => backToLobby()) // could not start one: the main menu instead
  }

  /** From Office Jukebox warm-up straight into the quiz's main report game, keeping the player's decade and host. */
  const playReport = () => {
    if (!info) {
      backToLobby()
      return
    }
    const targetMode =
      info.quiz.defaultMode !== 'jukebox'
        ? info.quiz.defaultMode
        : (info.quiz.modes.find((m) => m.id !== 'jukebox')?.id ?? 'show')
    const decade = lastStart.current?.decade ?? summary?.decade ?? null
    leaveResults()
    void start(targetMode, decade).catch(() => backToLobby())
  }

  if (fatal) {
    return <FullScreenMessage title="Hold on" action={<a className="btn btn-primary" href="/">Start page</a>}>{fatal}</FullScreenMessage>
  }
  if (!info || screen === 'loading') {
    return <Stage className="grid place-items-center"><Spinner /></Stage>
  }

  const chosenHost = info.branding.host.choices.find((choice) => choice.id === hostId) ?? info.branding.host.choices[0]
  const hostName = chosenHost?.name ?? info.branding.host.name
  const hostTone = hostToneFor(chosenHost?.register)
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
      {scoresView ? (
        <HighScoresScreen
          slug={slug}
          quizTitle={info.quiz.title}
          branding={info.branding}
          initialMode={scoresView.mode}
          modes={info.quiz.modes}
          teams={info.quiz.teams}
          bestByMode={info.player?.best}
          backLabel={scoresView.from === 'summary' ? 'Results' : 'Lobby'}
          onBack={() => setScoresView(null)}
        />
      ) : (
        <>
          {screen === 'join' && <JoinForm info={info} challenge={challenge} onJoined={() => void load().then(() => setScreen('lobby'))} />}
          {screen === 'lobby' && info.player && (
            <Lobby
              info={info}
              player={info.player}
              challenge={challenge}
              hostId={hostId}
              onChooseHost={chooseHost}
              onStart={start}
              onResume={resume}
              onOpenScores={(selectedMode) => setScoresView({ from: 'lobby', mode: selectedMode })}
              onSwitchPlayer={() => { session.setPlayerToken(null); setInfo({ ...info, player: null }); setScreen('join') }}
            />
          )}
          {screen === 'play' && turn && (
            <Play key={turn.state.runId} firstTurn={turn} resumed={resumed} hostName={hostName} hostLine={hostLine} hostSpeaking={hostSpeaking} hostTone={hostTone} host={hostRef.current} talk={talk} onDone={finished} />
          )}
          {screen === 'summary' && summary && (
            <Summary
              slug={slug}
              quizTitle={info.quiz.title}
              branding={info.branding}
              playerName={info.player?.name}
              playerTeam={info.player?.team}
              challenge={challenge}
              summary={summary}
              hostName={hostName}
              hostLine={hostLine}
              hostSpeaking={hostSpeaking}
              hostTone={hostTone}
              talk={talk}
              showLeaderboard={info.quiz.leaderboard}
              reportUrl={info.quiz.reportUrl}
              reportFiles={info.quiz.reportFiles}
              onOpenScores={() => setScoresView({ from: 'summary', mode: summary.mode })}
              onPlayReport={info.quiz.modes.some((m) => m.id !== 'jukebox') ? playReport : undefined}
              onPlayAgain={playAgain}
              onMainMenu={backToLobby}
            />
          )}
        </>
      )}
    </Stage>
  )
}

function ChallengeBanner({ challenge, modes }: { challenge: Challenge | null; modes: { id: ModeId; name: string }[] }) {
  if (!challenge) return null
  const modeName = challenge.mode ? modes.find((m) => m.id === challenge.mode)?.name : null
  return (
    <div
      className="card card-accent rise flex items-center gap-3 p-4"
      role="status"
    >
      <IconTrophy size={24} className="flex-none" style={{ color: 'var(--accent)' }} />
      <div className="min-w-0 flex-1">
        <p className="pixel text-[10px]" style={{ color: 'var(--accent-text)' }}>
          Challenge from {challenge.by}
        </p>
        <p className="mt-1 text-sm">
          Posted <b>{challenge.score.toLocaleString()} pts</b>
          {challenge.badge ? <> ({challenge.badge})</> : null}
          {modeName ? <> in {modeName}</> : null}. Think you can beat it?
        </p>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- join

function JoinForm({ info, challenge, onJoined }: { info: QuizInfo; challenge: Challenge | null; onJoined: () => void }) {
  const navigate = useNavigate()
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
    unlockAudio()
    unlockMusic()
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
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-5 py-4 lg:max-w-lg">
      <header className="text-center">
        <div className="mb-4 flex justify-center"><Logo branding={branding} size={52} /></div>
        <h1 className="neon text-lg sm:text-2xl">{branding.gameTitle}</h1>
        <p className="mt-3 text-muted">{branding.tagline}</p>
      </header>

      <ChallengeBanner challenge={challenge} modes={quiz.modes} />

      <form className="card rise grid gap-4 p-5" onSubmit={submit}>
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="chip chip-accent">{quiz.status === 'draft' ? 'Play-test · not open yet' : 'Now playing'}</p>
            <button type="button" className="text-xs font-medium text-muted underline hover:text-ink" onClick={() => navigate('/')}>
              All reports
            </button>
          </div>
          <h2 className="mt-3 !normal-case text-[21px]" style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.3 }}>{quiz.title}</h2>
          {quiz.description && <p className="mt-1 text-[15px] text-muted">{quiz.description}</p>}
          {meeting && <p className="mt-2 text-[15px]" style={{ color: 'var(--accent-text)' }}>Meeting {meeting}</p>}
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
        <button className="btn btn-primary btn-lg" disabled={busy}>
          {busy ? 'Joining…' : <>Insert coin <IconPlay size={14} /></>}
        </button>
        <p className="text-center text-sm text-muted">No password. Your results are saved under your email so the organiser can see who is ready.</p>
      </form>
    </div>
  )
}

// --------------------------------------------------------------------------- lobby

type LobbyProps = {
  info: QuizInfo
  player: PlayerView
  challenge: Challenge | null
  hostId: string | null
  onChooseHost: (id: string) => void
  onStart: (mode: ModeId, decade: string | null) => Promise<void>
  onResume: (runId: string) => Promise<void>
  onOpenScores: (mode: ModeId) => void
  onSwitchPlayer: () => void
}

const REGISTER_LABEL: Record<string, string> = { deeper: 'Deeper voice', middle: 'Mid voice', brighter: 'Brighter voice' }

function Lobby({ info, player, challenge, hostId, onChooseHost, onStart, onResume, onOpenScores, onSwitchPlayer }: LobbyProps) {
  const { quiz, branding } = info
  const initialMode = (challenge?.mode && quiz.modes.some((m) => m.id === challenge.mode))
    ? challenge.mode
    : quiz.defaultMode
  const [mode, setMode] = useState<ModeId>(initialMode)
  const [decade, setDecade] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const chosen = quiz.modes.find((m) => m.id === mode) ?? quiz.modes[0]
  const wantsDecade = chosen?.music && quiz.decades.length > 0
  const meeting = untilText(quiz.meetingAt)
  const firstName = player.name.split(' ')[0]

  // Each host introduces themselves, so a player can choose one by ear. The sample is recorded once on the
  // server and kept, so a tap costs nothing; the first tap of a new host may take a few seconds.
  const sample = useRef<HTMLAudioElement | null>(null)
  const [hearing, setHearing] = useState<{ id: string; playing: boolean } | null>(null)
  useEffect(() => () => sample.current?.pause(), [])
  const hear = (id: string) => {
    const audio = sample.current ?? (sample.current = new Audio())
    audio.pause()
    if (hearing?.id === id) {
      setHearing(null)
      return
    }
    const done = () => setHearing((now) => (now?.id === id ? null : now))
    audio.onplaying = () => setHearing({ id, playing: true })
    audio.onended = done
    audio.onerror = () => {
      done()
      setError('That host cannot be heard right now. Try again in a minute.')
    }
    setError('')
    setHearing({ id, playing: false })
    audio.src = `/api/quiz/${encodeURIComponent(quiz.slug)}/hosts/${encodeURIComponent(id)}/sample`
    audio.play().catch(done)
  }

  useEffect(() => {
    applyDecade(wantsDecade ? decade : null)
  }, [decade, wantsDecade])

  const go = async (action: () => Promise<void>) => {
    sample.current?.pause()
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not start the game. Please try again.')
      setBusy(false)
    }
  }

  const navigate = useNavigate()
  const [liveCode, setLiveCode] = useState('')
  const [hostingLive, setHostingLive] = useState(false)
  const [liveOpen, setLiveOpen] = useState(false)
  const [customizeOpen, setCustomizeOpen] = useState(false)
  const [readinessOpen, setReadinessOpen] = useState(false)
  const [switcherOpen, setSwitcherOpen] = useState(false)
  const [openReports, setOpenReports] = useState<{ slug: string; title: string; description?: string | null }[]>([])

  useEffect(() => {
    api<{ quizzes: { slug: string; title: string; description?: string | null }[] }>('/api/config')
      .then((data) => setOpenReports(data.quizzes ?? []))
      .catch(() => undefined)
  }, [quiz.slug])

  const activeHost = branding.host.choices.find((c) => c.id === hostId) ?? branding.host.choices[0]
  const activeHostTone = activeHost ? hostToneFor(activeHost.register) : 'accent'
  const showCustomize = chosen?.id !== 'straight' && (branding.host.choices.length > 1 || quiz.decades.length > 0)
  const readiness = player.readiness
  const hasMultipleReports = openReports.length > 1

  const hostLiveRoom = async () => {
    if (hostingLive || busy) return
    setHostingLive(true)
    setError('')
    unlockAudio()
    unlockMusic()
    try {
      const created = await api<{ code: string; hostKey: string }>(`/api/live/quiz/${quiz.slug}/host`, {
        body: { decade: wantsDecade ? decade : null },
      })
      navigate(`/host/${created.code}#k=${encodeURIComponent(created.hostKey)}`)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not start a live room.')
      setHostingLive(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col gap-4 pb-8 lg:max-w-2xl">
      <header className="flex items-center gap-3">
        <Logo branding={branding} size={38} />
        <div className="min-w-0 flex-1">
          <p className="pixel truncate text-[10px] text-muted">{branding.companyName}</p>
          <p className="pixel neon truncate text-xs sm:text-sm">{branding.gameTitle}</p>
        </div>
        <SoundToggle />
      </header>

      <ChallengeBanner challenge={challenge} modes={quiz.modes} />

      {/* 1. Hero Launchpad: Report + Switcher + Primary Start CTA */}
      <section className="card rise p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="pixel text-[10px] text-muted">Now playing · {firstName}</span>
          {hasMultipleReports ? (
            <button
              type="button"
              className="btn btn-accent btn-sm"
              onClick={() => setSwitcherOpen((open) => !open)}
              aria-expanded={switcherOpen}
            >
              Switch report ({openReports.length}) {switcherOpen ? '▴' : '▾'}
            </button>
          ) : (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => navigate('/')}>
              All reports
            </button>
          )}
        </div>

        {switcherOpen && hasMultipleReports && (
          <div className="mt-3 flex flex-col gap-1.5 overflow-hidden rounded-lg border border-line bg-surface-2 p-2">
            {openReports.map((r) => {
              const current = r.slug === quiz.slug
              return (
                <button
                  key={r.slug}
                  type="button"
                  className="flex w-full min-w-0 items-center justify-between gap-3 overflow-hidden rounded-md border px-3 py-2.5 text-left text-sm transition hover:bg-surface"
                  style={{
                    borderColor: current ? 'var(--brand)' : 'transparent',
                    background: current ? 'color-mix(in oklab, var(--brand) 18%, var(--surface))' : 'transparent',
                  }}
                  onClick={() => {
                    setSwitcherOpen(false)
                    if (!current) navigate(`/q/${r.slug}`)
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold leading-snug">{r.title}</span>
                    {r.description && <span className="mt-0.5 block truncate text-xs text-muted">{r.description}</span>}
                  </span>
                  {current && (
                    <span className="pixel inline-flex flex-none items-center gap-1 text-[9px]" style={{ color: 'var(--good)' }}>
                      <IconCheck size={12} /> Active
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}

        <h1 className="mt-3 !normal-case text-[22px] font-bold sm:text-2xl" style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.25 }}>
          {quiz.title}
        </h1>
        {quiz.description && <p className="mt-1.5 text-sm leading-relaxed text-muted">{quiz.description}</p>}
        {meeting && <p className="mt-1.5 text-sm font-medium" style={{ color: 'var(--accent-text)' }}>Meeting {meeting}</p>}
        {!quiz.ready && <p className="mt-2 text-sm" style={{ color: 'var(--bad)' }}>This quiz is still being prepared. Check back soon.</p>}

        {(quiz.reportUrl || (quiz.reportFiles && quiz.reportFiles.length > 0)) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {quiz.reportUrl && (
              <a className="text-xs font-semibold underline hover:opacity-80" style={{ color: 'var(--accent-text)' }} href={quiz.reportUrl} target="_blank" rel="noopener noreferrer">
                Read the report first ↗
              </a>
            )}
            {(quiz.reportFiles ?? []).map((file) => (
              <a key={file.id} className="text-xs font-semibold underline hover:opacity-80" style={{ color: 'var(--accent-text)' }} href={file.url} target="_blank" rel="noopener noreferrer">
                {quiz.reportFiles!.length === 1 && !quiz.reportUrl ? 'Open report to read ↗' : `${file.filename} ↗`}
              </a>
            ))}
          </div>
        )}

        {readiness.total > 0 && (
          <div className="mt-4 rounded-lg border border-line bg-surface-2/70 px-3 py-2.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-semibold" style={{ color: readiness.ready ? 'var(--good)' : 'var(--ink)' }}>
                {readiness.ready ? 'Ready!' : 'Report progress'} · {readiness.cleared}/{readiness.total} sections ({readiness.percent}%)
              </span>
              <button
                type="button"
                className="text-xs text-muted underline hover:text-ink"
                onClick={() => setReadinessOpen((open) => !open)}
                aria-expanded={readinessOpen}
              >
                {readinessOpen ? 'Hide' : 'Details'}
              </button>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-black/35">
              <div
                className="h-full transition-all"
                style={{
                  width: `${Math.max(4, readiness.percent)}%`,
                  background: readiness.ready ? 'var(--good)' : 'var(--accent)',
                }}
              />
            </div>
            {readinessOpen && (
              <div className="mt-3 border-t border-line pt-3">
                <ReadinessMap readiness={readiness} />
              </div>
            )}
          </div>
        )}

        <ErrorNote>{error}</ErrorNote>

        {/* Hero Start Action */}
        <div className="mt-5 flex flex-col gap-2.5">
          {player.unfinishedRun && (
            <button className="btn btn-accent btn-lg w-full" disabled={busy} onClick={() => void go(() => onResume(player.unfinishedRun!.id))}>
              Continue your game <IconPlay size={14} />
            </button>
          )}
          <button
            className="btn btn-primary btn-lg w-full"
            disabled={busy || !quiz.ready}
            onClick={() => void go(() => onStart(mode, wantsDecade ? decade : null))}
          >
            {busy ? 'Loading…' : player.unfinishedRun ? 'Start a fresh game' : <>Press start <IconPlay size={14} /></>}
          </button>
          <p className="text-center text-xs text-muted">
            {chosen?.name ?? 'The Show'}
            {chosen?.id !== 'straight' && activeHost ? ` · Host: ${activeHost.name}` : ''}
            {wantsDecade ? ` · Music: ${decade ? DECADE_LABEL[decade] : 'Any decade'}` : ''}
          </p>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2.5 border-t border-line pt-4">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLiveOpen(true)}>
            Play live together
          </button>
          {quiz.leaderboard ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenScores(mode)}>
              <IconTrophy size={13} style={{ color: 'var(--accent)' }} /> High scores
            </button>
          ) : (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onSwitchPlayer}>
              Not {firstName}?
            </button>
          )}
        </div>
      </section>

      {/* 2. Compact Game Style Selector */}
      {quiz.modes.length > 1 && (
        <section className="card p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="pixel text-[10px] text-muted">Game style</p>
            {chosen && player.best[chosen.id] !== undefined && (
              <span className="pixel text-[9px]" style={{ color: 'var(--accent-text)' }}>
                Your best: {player.best[chosen.id]?.toLocaleString()}
              </span>
            )}
          </div>
          <div className="mt-2.5 flex flex-wrap gap-2" role="group" aria-label="Game style">
            {quiz.modes.map((option) => {
              const picked = option.id === mode
              return (
                <button
                  key={option.id}
                  type="button"
                  className={`btn btn-sm ${picked ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => { setMode(option.id); play('click') }}
                  aria-pressed={picked}
                >
                  {option.name}
                </button>
              )
            })}
          </div>
          {chosen && <p className="mt-2.5 text-sm text-muted">{chosen.tagline}</p>}
        </section>
      )}

      {/* 3. Collapsed Host & Music Customization Drawer */}
      {showCustomize && (
        <section className="card p-4">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-3 text-left"
            onClick={() => setCustomizeOpen((open) => !open)}
            aria-expanded={customizeOpen}
          >
            <div className="flex min-w-0 items-center gap-3">
              {activeHost && <HostCrt size="sm" tone={activeHostTone} state="idle" />}
              <div className="min-w-0">
                <p className="pixel text-[10px] text-muted">Host &amp; music</p>
                <p className="mt-0.5 truncate text-sm font-medium">
                  {activeHost ? activeHost.name : 'Default host'}
                  {wantsDecade ? ` · ${decade ? DECADE_LABEL[decade] : 'Any decade'}` : ''}
                  {player.favoriteArtist ? ` · ♥ ${player.favoriteArtist}` : ''}
                </p>
              </div>
            </div>
            <span className="btn btn-ghost btn-sm flex-none">{customizeOpen ? 'Done ▴' : 'Change ▾'}</span>
          </button>

          {customizeOpen && (
            <div className="mt-4 flex flex-col gap-4 border-t border-line pt-4">
              {branding.host.choices.length > 1 && (
                <div>
                  <p className="pixel mb-2 text-[10px] text-muted">Choose your host</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {branding.host.choices.map((choice, index) => {
                      const selected = hostId ? choice.id === hostId : index === 0
                      const isHearing = hearing?.id === choice.id
                      const tone = hostToneFor(choice.register)
                      return (
                        <div key={choice.id} className="flex items-stretch gap-1.5">
                          <button
                            type="button"
                            className="answer min-w-0 flex-1 items-center gap-2.5 py-2"
                            data-state={selected ? 'picked' : 'idle'}
                            style={{ ['--tone' as string]: 'var(--accent)' }}
                            onClick={() => { onChooseHost(choice.id); play('click') }}
                            aria-pressed={selected}
                          >
                            <HostCrt size="sm" tone={tone} state={isHearing && hearing.playing ? 'speaking' : isHearing ? 'thinking' : 'idle'} />
                            <span className="min-w-0 flex-1">
                              <span className="pixel block truncate text-[10px]">{choice.name}</span>
                              <span className="block truncate text-xs text-muted">{choice.label ?? REGISTER_LABEL[choice.register] ?? 'Host'}</span>
                            </span>
                          </button>
                          {branding.host.voiceEnabled && (
                            <button
                              type="button"
                              className="btn btn-ghost min-w-[42px] flex-none px-0"
                              onClick={() => hear(choice.id)}
                              aria-label={isHearing ? `Stop ${choice.name}` : `Hear ${choice.name}`}
                              title={isHearing ? 'Stop' : `Hear ${choice.name}`}
                            >
                              {isHearing ? (hearing.playing ? <IconStop size={13} /> : <span className="pixel text-[10px]">…</span>) : <IconPlay size={13} />}
                            </button>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {wantsDecade && (
                <div>
                  <p className="pixel mb-2 text-[10px] text-muted">Pick the music</p>
                  <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
                    <button
                      type="button"
                      className="answer min-h-[46px] flex-col justify-center gap-1 px-1 py-1.5"
                      data-state={decade === null ? 'picked' : 'idle'}
                      style={{ ['--tone' as string]: 'var(--accent)' }}
                      onClick={() => setDecade(null)}
                      aria-pressed={decade === null}
                      title="Surprise me"
                    >
                      <span className="pixel text-[9px]">Any</span>
                      <span className="h-1 w-6 rounded-full" style={{ background: 'linear-gradient(90deg, var(--brand), var(--accent))' }} aria-hidden />
                    </button>
                    {quiz.decades.map((d) => {
                      const [c1, c2] = DECADE_SWATCH[d] ?? ['var(--brand)', 'var(--accent)']
                      return (
                        <button
                          key={d}
                          type="button"
                          className="answer min-h-[46px] flex-col justify-center gap-1 px-1 py-1.5"
                          data-state={decade === d ? 'picked' : 'idle'}
                          style={{ ['--tone' as string]: c1 }}
                          onClick={() => { setDecade(d); play('click') }}
                          aria-pressed={decade === d}
                          title={DECADE_LABEL[d]}
                        >
                          <span className="pixel text-[9px]">{d}</span>
                          <span className="h-1 w-6 rounded-full" style={{ background: `linear-gradient(90deg, ${c1}, ${c2})` }} aria-hidden />
                        </button>
                      )
                    })}
                  </div>
                  <p className="mt-2 text-xs text-muted">{decade ? `${DECADE_LABEL[decade]} · ${DECADE_FLAVOUR[decade]}` : 'Surprise me — a fresh decade mix'}</p>
                </div>
              )}

              {quiz.decades.length > 0 && <FavouriteArtist initial={player.favoriteArtist ?? null} />}
            </div>
          )}
        </section>
      )}

      {/* Subtle account footer */}
      <footer className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs text-muted">
        <span>Signed in as <b className="text-ink">{player.name}</b></span>
        <button type="button" className="underline hover:text-ink" onClick={onSwitchPlayer}>Switch player</button>
        {branding.supportEmail && <a className="underline hover:text-ink" href={`mailto:${branding.supportEmail}`}>Need help?</a>}
      </footer>

      {/* Play Live Together Modal */}
      <Modal open={liveOpen} onClose={() => setLiveOpen(false)}>
        <div className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="pixel text-[10px]" style={{ color: 'var(--accent-text)' }}>Play live together</p>
              <h2 className="mt-1 !normal-case text-lg font-bold" style={{ fontFamily: 'var(--font-sans)' }}>
                Big screen + phones
              </h2>
            </div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLiveOpen(false)}>Close</button>
          </div>
          <p className="text-sm text-muted">
            Put the questions on a shared TV or projector and have everyone join with a room code on their phones.
          </p>
          <button
            type="button"
            className="btn btn-accent btn-lg w-full"
            disabled={hostingLive || busy || !quiz.ready}
            onClick={() => void hostLiveRoom()}
          >
            {hostingLive ? 'Opening room…' : <>Host big screen for this report <IconPlay size={13} /></>}
          </button>
          <div className="border-t border-line pt-4">
            <p className="label">Already have a room code?</p>
            <form
              className="mt-2 flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                const clean = liveCode.trim().toUpperCase()
                if (clean.length >= 4) navigate(`/live/${clean}`)
              }}
            >
              <input
                className="field pixel min-w-0 flex-1 text-center text-sm tracking-widest"
                value={liveCode}
                onChange={(e) => setLiveCode(e.target.value.toUpperCase())}
                maxLength={6}
                autoCapitalize="characters"
                autoComplete="off"
                placeholder="ABCDE"
                aria-label="Live room join code"
              />
              <button type="submit" className="btn btn-primary" disabled={liveCode.trim().length < 4}>
                Join room
              </button>
            </form>
          </div>
        </div>
      </Modal>
    </div>
  )
}

/**
 * "Who do you like?" - a player's favourite artist. Now and then a song from them, or from artists like them,
 * plays behind their game and on their results screen (never as a name-that-tune question). Optional.
 */
function FavouriteArtist({ initial }: { initial: string | null }) {
  const [saved, setSaved] = useState(initial)
  const [text, setText] = useState(initial ?? '')
  const [similar, setSimilar] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')

  const save = async (value: string) => {
    setBusy(true)
    setProblem('')
    try {
      const reply = await api<{ artist: string | null; similar: string[] }>('/api/me/favourite-artist', {
        method: 'PUT',
        body: { artist: value.trim() || null },
      })
      setSaved(reply.artist)
      setText(reply.artist ?? '')
      setSimilar(reply.similar ?? [])
    } catch (caught) {
      setProblem(caught instanceof ApiError ? caught.message : 'That did not save. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const unchanged = text.trim() === (saved ?? '')
  return (
    <section>
      <label className="pixel mb-2 block text-[9px] text-muted" htmlFor="favourite-artist">
        Who do you like? <span className="normal-case">(optional)</span>
      </label>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (!unchanged) void save(text)
        }}
      >
        <input
          id="favourite-artist"
          className="field min-w-0 flex-1"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="An artist, e.g. Chicago"
          maxLength={80}
          autoComplete="off"
        />
        <button className="btn btn-ghost flex-none" type="submit" disabled={busy || unchanged}>
          {busy ? '…' : 'Save'}
        </button>
      </form>
      <p className="mt-2 text-[15px] text-muted" aria-live="polite">
        {saved ? (
          <>
            Now and then: {saved}
            {similar.length > 0 && `, and artists like ${similar.slice(0, 3).join(', ')}`}.{' '}
            <button className="underline" type="button" onClick={() => void save('')} disabled={busy}>
              Clear
            </button>
          </>
        ) : (
          'Now and then, a song from them or from artists like them.'
        )}
      </p>
      {problem && (
        <p className="mt-1 text-[15px]" style={{ color: 'var(--bad)' }}>
          {problem}
        </p>
      )}
    </section>
  )
}
