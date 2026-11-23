import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { Badge, ConfirmDialog, CopyButton, Dialog, ErrorBlock, Field, LoadingBlock, Note, Panel } from '../components'
import { useNotify } from '../context'
import { decadeLabel, formatDateTime, isThisSite, plural, type Tone } from '../format'
import { useInterval, useLoad } from '../hooks'
import { paths } from '../paths'
import type { LiveCreated, LiveSession, LiveStanding, QuestionSources, QuizOverview } from '../types'
import { useQuiz } from './context'

const SESSION_STATUS: Record<LiveSession['status'], { label: string; tone: Tone }> = {
  lobby: { label: 'Waiting to start', tone: 'info' },
  question: { label: 'In progress', tone: 'warn' },
  reveal: { label: 'In progress', tone: 'warn' },
  scoreboard: { label: 'In progress', tone: 'warn' },
  finished: { label: 'Finished', tone: 'neutral' },
}

function playableMcqCount(overview: QuizOverview, source?: QuestionSources): number {
  if (source === 'manual' && overview.approved_manual_mcq !== undefined) return overview.approved_manual_mcq
  if (source === 'ai' && overview.approved_ai_mcq !== undefined) return overview.approved_ai_mcq
  return overview.approved_mcq ?? overview.approved
}

export default function LiveTab() {
  const { detail, refresh: refreshQuiz, liveGame, setLiveGame } = useQuiz()
  const notify = useNotify()
  const { quiz, rules, overview } = detail
  const sessions = useLoad<{ sessions: LiveSession[] }>(`/quizzes/${quiz.id}/live`)
  const [decade, setDecade] = useState('') // '' lets the server pick one
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [replacing, setReplacing] = useState(false)
  const [viewing, setViewing] = useState<LiveSession | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [reclaimingId, setReclaimingId] = useState<string | null>(null)
  const [ending, setEnding] = useState<LiveSession | null>(null)
  const [endBusy, setEndBusy] = useState(false)
  const [endError, setEndError] = useState<string | null>(null)
  const [reopenedCode, setReopenedCode] = useState<string | null>(null)
  const readyRef = useRef<HTMLDivElement>(null)

  const list = sessions.data?.sessions ?? []
  const activeSessions = list.filter((s) => s.status !== 'finished')
  const hasActive = activeSessions.length > 0 || liveGame !== null
  const playableMcq = playableMcqCount(overview, rules.question_sources)
  const canStart = playableMcq >= 3

  // Auto-poll active live rooms every 5s (paused while tab is hidden).
  useInterval(
    () => {
      if (document.hidden) return
      sessions.refresh().catch(() => {})
    },
    hasActive ? 5000 : null,
  )

  // Clear the active ReadyGame banner automatically once the server marks that room finished.
  useEffect(() => {
    if (!liveGame || !sessions.data) return
    const match = sessions.data.sessions.find((s) => s.code === liveGame.code)
    if (match && match.status === 'finished') {
      setLiveGame(null)
      setReopenedCode(null)
      refreshQuiz().catch(() => {})
    }
  }, [liveGame, sessions.data, setLiveGame, refreshQuiz])

  const reclaim = async (session: LiveSession) => {
    if (reclaimingId) return
    setReclaimingId(session.id)
    try {
      const recovered = await call<LiveCreated>(`/live/${session.id}/reclaim`, { method: 'POST', body: {} })
      setLiveGame({ ...recovered, questions: session.questions })
      setReopenedCode(recovered.code)
      notify(`Fresh big-screen link ready for ${recovered.code}.`)
      window.setTimeout(() => {
        readyRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 50)
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setReclaimingId(null)
    }
  }

  const endGame = async () => {
    if (!ending || endBusy) return
    setEndBusy(true)
    setEndError(null)
    try {
      await call(`/live/${ending.id}/finish`, { method: 'POST', body: {} })
      if (liveGame?.code === ending.code) {
        setLiveGame(null)
        setReopenedCode(null)
      }
      notify(`Ended live game ${ending.code}.`)
      setEnding(null)
      await Promise.all([sessions.refresh().catch(() => {}), refreshQuiz().catch(() => {})])
    } catch (reason) {
      setEndError(errorText(reason))
    } finally {
      setEndBusy(false)
    }
  }

  const musicInLive = rules.music_enabled && rules.live.music_rounds > 0
  const wantsMusicDecade = rules.music_enabled && (rules.live.music_rounds > 0 || rules.after_party)

  const start = async () => {
    if (starting) return
    setStarting(true)
    setError(null)
    try {
      const game = await call<LiveCreated>(`/quizzes/${quiz.id}/live`, { method: 'POST', body: wantsMusicDecade && decade ? { decade } : {} })
      setLiveGame(game)
      setReopenedCode(null)
      setReplacing(false)
      notify(`Live game ${game.code} is ready.`)
      await Promise.all([sessions.refresh().catch(() => {}), refreshQuiz().catch(() => {})])
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setStarting(false)
    }
  }

  const refreshSessions = async () => {
    setRefreshing(true)
    try {
      await sessions.refresh()
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="grid gap-5">
      {liveGame && (
        <div ref={readyRef}>
          <ReadyGame game={liveGame} resumed={reopenedCode === liveGame.code} />
        </div>
      )}

      {!liveGame && activeSessions.length > 0 && (
        <Note tone="info" role="status">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <strong>
                Active live room{activeSessions.length > 1 ? `s (${activeSessions.length})` : `: ${activeSessions[0].code}`}
              </strong>
              <p className="mt-0.5 text-sm text-muted">
                {activeSessions.length === 1
                  ? `${SESSION_STATUS[activeSessions[0].status]?.label ?? activeSessions[0].status} · ${plural(activeSessions[0].players, 'player')} joined.`
                  : 'Reopen the big screen on any active room below, or end abandoned test rooms.'}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={reclaimingId === activeSessions[0].id}
                onClick={() => void reclaim(activeSessions[0])}
              >
                {reclaimingId === activeSessions[0].id ? 'Reopening…' : `Reopen ${activeSessions[0].code}`}
              </button>
              <button
                type="button"
                className="btn btn-danger btn-sm"
                onClick={() => {
                  setEndError(null)
                  setEnding(activeSessions[0])
                }}
              >
                End game
              </button>
            </div>
          </div>
        </Note>
      )}

      <Panel
        title={liveGame ? 'Start another live game' : 'Start a live game'}
        description="For the meeting itself: the questions go up on a big screen and everyone answers on their own phone, fastest finger first."
      >
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>
            {rules.live.questions} questions, {rules.live.timer_sec} seconds each
            {musicInLive
              ? `, with ${plural(rules.live.music_rounds, 'music break')}${rules.after_party ? ' and after-party music' : ''}`
              : wantsMusicDecade
                ? ', with after-party music at the end'
                : ', no music'}.
          </li>
          <li>
            {rules.live.head_start > 0
              ? `People who are already “ready” from the pre-read start with ${rules.live.head_start} points.`
              : 'Nobody gets a head start for doing the pre-read.'}
          </li>
          <li>Pinned questions are always included, followed by a balanced easy → medium → hard mix across sections (favoring topics people missed in the pre-read).</li>
          <li>
            Players join with a code, not the play link, so it works whatever the quiz's status is.{' '}
            <Link className="ad-link" to={paths.quiz(quiz.id, 'play-styles')}>
              Change these settings
            </Link>
          </li>
        </ul>

        {!canStart && (
          <div className="mt-4">
            <Note tone="warn">
              This quiz currently has <strong>{plural(playableMcq, 'eligible live multiple-choice question')}</strong> — at least 3 are needed to run a
              live game.{' '}
              <Link className="ad-link" to={paths.quiz(quiz.id, 'questions')}>
                Go to Questions
              </Link>
            </Note>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-end gap-3">
          {wantsMusicDecade && (
            <Field label="Music from" className="w-full sm:w-56">
              {(props) => (
                <select className="field" {...props} value={decade} onChange={(event) => setDecade(event.target.value)} disabled={starting || !canStart}>
                  <option value="">Surprise us</option>
                  {rules.decades.map((option) => (
                    <option key={option} value={option}>
                      The {decadeLabel(option)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={starting || !canStart}
            onClick={() => {
              setError(null)
              if (liveGame || activeSessions.length > 0) setReplacing(true)
              else void start()
            }}
          >
            {starting ? 'Setting up…' : liveGame ? 'Start another game' : 'Start a live game'}
          </button>
        </div>
        {error && !replacing && (
          <div className="mt-3">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
      </Panel>

      <Panel
        title="Earlier live games"
        description={hasActive ? 'The last 20 games run for this quiz. Updates automatically every 5 seconds while a room is active.' : 'The last 20 games run for this quiz.'}
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={refreshSessions} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      >
        {!sessions.data && sessions.loading && <LoadingBlock label="Loading earlier games" />}
        {!sessions.data && sessions.error && <ErrorBlock message={sessions.error} onRetry={sessions.reload} />}
        {sessions.data && list.length === 0 && <p className="py-2 text-sm text-muted">No live games have been run for this quiz yet.</p>}
        {sessions.data && list.length > 0 && (
          <>
            <div className="ad-table-wrap ad-table-sticky" tabIndex={0} role="region" aria-label="Earlier live games">
              <table className="ad-table" style={{ minWidth: 680 }}>
                <thead>
                  <tr>
                    <th scope="col">Join code</th>
                    <th scope="col">Started</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      Players
                    </th>
                    <th scope="col" className="num">
                      Questions
                    </th>
                    <th scope="col" className="num">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((session) => {
                    const status = SESSION_STATUS[session.status] ?? { label: session.status, tone: 'neutral' as Tone }
                    return (
                      <tr key={session.id}>
                        <th scope="row" className="font-mono font-semibold tracking-wider">
                          {session.code}
                        </th>
                        <td className="whitespace-nowrap">{formatDateTime(session.created_at)}</td>
                        <td>
                          <Badge tone={status.tone}>{status.label}</Badge>
                        </td>
                        <td className="num">{session.players}</td>
                        <td className="num">{session.questions}</td>
                        <td className="num">
                          <div className="inline-flex flex-wrap items-center justify-end gap-2">
                            {session.status !== 'finished' && (
                              <>
                                <button
                                  type="button"
                                  className="btn btn-accent btn-sm"
                                  disabled={reclaimingId === session.id}
                                  onClick={() => void reclaim(session)}
                                >
                                  {reclaimingId === session.id ? 'Reopening…' : 'Reopen big screen'}
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-danger btn-sm"
                                  onClick={() => {
                                    setEndError(null)
                                    setEnding(session)
                                  }}
                                >
                                  End game
                                </button>
                              </>
                            )}
                            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setViewing(session)} disabled={session.players === 0}>
                              {session.players === 0 ? 'Nobody joined' : 'See results'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">
              Lost the big-screen tab for a game that is still running? Use “Reopen big screen” to generate a fresh host link (this immediately disconnects any older host tab).
            </p>
          </>
        )}
      </Panel>

      <ConfirmDialog
        open={replacing}
        title="Start another live game?"
        confirmLabel="Start a new game"
        busyLabel="Setting up…"
        danger={false}
        busy={starting}
        error={error}
        onConfirm={start}
        onCancel={() => {
          setReplacing(false)
          setError(null)
        }}
      >
        <p>
          {liveGame ? (
            <>
              Game <strong>{liveGame.code}</strong> stays active, and you can reopen its big screen or end it anytime from Earlier live games below.
            </>
          ) : (
            <>An earlier live room is still active. Starting another creates a separate room with its own join code.</>
          )}
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={ending !== null}
        title={ending ? `End live game ${ending.code}?` : 'End live game?'}
        confirmLabel="End game"
        busyLabel="Ending…"
        danger
        busy={endBusy}
        error={endError}
        onConfirm={() => void endGame()}
        onCancel={() => !endBusy && setEnding(null)}
      >
        <p>
          Marks live game <strong>{ending?.code}</strong> as finished immediately. Any joined players will see the final standings, and nobody new can join.
        </p>
      </ConfirmDialog>

      <Dialog open={viewing !== null} onClose={() => setViewing(null)} title={viewing ? `Results of game ${viewing.code}` : 'Results'} wide>
        {viewing && <SessionResults session={viewing} />}
      </Dialog>
    </div>
  )
}

/** The game that was just created or reclaimed: its join code, and the one-time link that runs it. */
function ReadyGame({ game, resumed }: { game: LiveCreated; resumed: boolean }) {
  const elsewhere = !isThisSite(game.joinUrl)
  return (
    <section className="card p-4 sm:p-5" aria-label={`Live game ${game.code}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-bold">{resumed ? `Resuming live game ${game.code}` : 'Your live game is ready'}</h2>
          <p className="mt-0.5 text-sm text-muted">
            {resumed
              ? 'A fresh host control link has been issued. Opening it takes over the big screen right where the game left off.'
              : `${plural(game.questions, 'question')} lined up. Players can join as soon as the big screen is open.`}
          </p>
        </div>
        <a className="btn btn-primary btn-lg" href={game.hostUrl} target="_blank" rel="noopener">
          {resumed ? 'Reopen the big screen' : 'Open the big screen'}
        </a>
      </div>

      <div className="mt-4 grid gap-5 md:grid-cols-[auto_minmax(0,1fr)] md:items-center">
        <div className="rounded-xl bg-surface-2 px-6 py-4 text-center">
          <div className="text-xs font-semibold text-muted">Join code</div>
          <div className="font-mono text-5xl font-bold tracking-[0.18em]">{game.code}</div>
        </div>
        <div className="grid gap-3">
          <Field label="Join link for players" hint="The big screen shows the code too, so you only need this for an invitation.">
            {(props) => (
              <div className="flex flex-wrap gap-2">
                <div className="min-w-0 flex-1 basis-56">
                  <input className="field" {...props} readOnly value={game.joinUrl} onFocus={(event) => event.target.select()} />
                </div>
                <CopyButton text={game.joinUrl} label="Copy link" />
              </div>
            )}
          </Field>
        </div>
      </div>

      <div className="mt-4">
        <Note tone="warn" role="status">
          <strong className="block">Keep the big-screen link private</strong>
          <p className="mt-1">
            It contains the key that controls the game, so anyone who has it can run the room: open it only on the computer connected to the big
            screen, and do not share it with players. If you close or lose the tab while the game is still running, use “Reopen big screen” below to
            generate a fresh host link.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <div className="min-w-0 flex-1 basis-56">
              <input
                className="field"
                readOnly
                value={game.hostUrl}
                aria-label="Big-screen link, including the control key"
                onFocus={(event) => event.target.select()}
              />
            </div>
            <CopyButton text={game.hostUrl} label="Copy big-screen link" />
          </div>
        </Note>
      </div>

      {elsewhere && (
        <div className="mt-3">
          <Note tone="warn">
            These links point to a different address from the one you are using now ({window.location.origin}). If they do not open, ask whoever set
            the system up to correct the site address (the WEB_BASE_URL setting).
          </Note>
        </div>
      )}
    </section>
  )
}

function SessionResults({ session }: { session: LiveSession }) {
  const { data, error, loading, reload } = useLoad<{ standings: LiveStanding[] }>(`/live/${session.id}/results`)

  if (!data) {
    if (error) return <ErrorBlock message={error} onRetry={reload} />
    return <LoadingBlock label={loading ? 'Loading the results' : 'Loading'} />
  }
  if (data.standings.length === 0) return <p className="py-4 text-sm text-muted">Nobody joined this game.</p>

  return (
    <>
      <p className="mb-3 text-sm text-muted">
        {formatDateTime(session.created_at)} · {plural(session.questions, 'question')} · {SESSION_STATUS[session.status]?.label ?? session.status}
        {session.status !== 'finished' && ' (scores so far)'}
      </p>
      <div className="ad-table-wrap" tabIndex={0} role="region" aria-label={`Standings for game ${session.code}`}>
        <table className="ad-table" style={{ minWidth: 560 }}>
          <thead>
            <tr>
              <th scope="col" className="num">
                Place
              </th>
              <th scope="col">Player</th>
              <th scope="col">Team</th>
              <th scope="col" className="num">
                Score
              </th>
              <th scope="col" className="num">
                Correct
              </th>
              <th scope="col" className="num" title="Points given at the start for being ready from the pre-read">
                Head start
              </th>
            </tr>
          </thead>
          <tbody>
            {data.standings.map((standing, index) => (
              <tr key={`${standing.email}-${index}`}>
                <td className="num">{index + 1}</td>
                <th scope="row">
                  <div className="font-semibold">{standing.name}</div>
                  <div className="text-muted [overflow-wrap:anywhere]">{standing.email}</div>
                </th>
                <td>{standing.team ?? <span className="text-muted">—</span>}</td>
                <td className="num">{standing.score.toLocaleString()}</td>
                <td className="num">{standing.correct_count}</td>
                <td className="num">{standing.head_start > 0 ? standing.head_start : <span className="text-muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
