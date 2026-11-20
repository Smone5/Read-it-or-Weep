import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { Badge, ConfirmDialog, CopyButton, Dialog, ErrorBlock, Field, LoadingBlock, Note, Panel } from '../components'
import { useNotify } from '../context'
import { decadeLabel, formatDateTime, isThisSite, plural, type Tone } from '../format'
import { useLoad } from '../hooks'
import { paths } from '../paths'
import type { LiveCreated, LiveSession, LiveStanding } from '../types'
import { useQuiz } from './context'

const SESSION_STATUS: Record<LiveSession['status'], { label: string; tone: Tone }> = {
  lobby: { label: 'Waiting to start', tone: 'info' },
  question: { label: 'In progress', tone: 'warn' },
  reveal: { label: 'In progress', tone: 'warn' },
  scoreboard: { label: 'In progress', tone: 'warn' },
  finished: { label: 'Finished', tone: 'neutral' },
}

export default function LiveTab() {
  const { detail, liveGame, setLiveGame } = useQuiz()
  const notify = useNotify()
  const { quiz, rules } = detail
  const sessions = useLoad<{ sessions: LiveSession[] }>(`/quizzes/${quiz.id}/live`)
  const [decade, setDecade] = useState('') // '' lets the server pick one
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [replacing, setReplacing] = useState(false)
  const [viewing, setViewing] = useState<LiveSession | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const musicInLive = rules.music_enabled && rules.live.music_rounds > 0

  const start = async () => {
    if (starting) return
    setStarting(true)
    setError(null)
    try {
      const game = await call<LiveCreated>(`/quizzes/${quiz.id}/live`, { method: 'POST', body: musicInLive && decade ? { decade } : {} })
      setLiveGame(game)
      setReplacing(false)
      notify(`Live game ${game.code} is ready.`)
      sessions.refresh().catch(() => {})
    } catch (reason) {
      // For example: fewer than three live questions. The server's message says what to do.
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
      {liveGame && <ReadyGame game={liveGame} />}

      <Panel
        title={liveGame ? 'Start another live game' : 'Start a live game'}
        description="For the meeting itself: the questions go up on a big screen and everyone answers on their own phone, fastest finger first."
      >
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>
            {rules.live.questions} questions, {rules.live.timer_sec} seconds each
            {musicInLive ? `, with ${plural(rules.live.music_rounds, 'music break')}` : ', no music'}.
          </li>
          <li>
            {rules.live.head_start > 0
              ? `People who are already “ready” from the pre-read start with ${rules.live.head_start} points.`
              : 'Nobody gets a head start for doing the pre-read.'}
          </li>
          <li>Pinned questions are always included, then the ones people found hardest in the pre-read.</li>
          <li>
            Players join with a code, not the play link, so it works whatever the quiz's status is.{' '}
            <Link className="ad-link" to={paths.quiz(quiz.id, 'play-styles')}>
              Change these settings
            </Link>
          </li>
        </ul>

        <div className="mt-4 flex flex-wrap items-end gap-3">
          {musicInLive && (
            <Field label="Music from" className="w-full sm:w-56">
              {(props) => (
                <select className="field" {...props} value={decade} onChange={(event) => setDecade(event.target.value)} disabled={starting}>
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
            disabled={starting}
            onClick={() => {
              setError(null)
              if (liveGame) setReplacing(true)
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
        description="The last 20 games run for this quiz."
        actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={refreshSessions} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      >
        {!sessions.data && sessions.loading && <LoadingBlock label="Loading earlier games" />}
        {!sessions.data && sessions.error && <ErrorBlock message={sessions.error} onRetry={sessions.reload} />}
        {sessions.data && sessions.data.sessions.length === 0 && <p className="py-2 text-sm text-muted">No live games have been run for this quiz yet.</p>}
        {sessions.data && sessions.data.sessions.length > 0 && (
          <>
            <div className="ad-table-wrap">
              <table className="ad-table" style={{ minWidth: 560 }}>
                <thead>
                  <tr>
                    <th scope="col">Started</th>
                    <th scope="col">Join code</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      Players
                    </th>
                    <th scope="col" className="num">
                      Questions
                    </th>
                    <th scope="col">
                      <span className="sr-only">Results</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.data.sessions.map((session) => {
                    const status = SESSION_STATUS[session.status] ?? { label: session.status, tone: 'neutral' as Tone }
                    return (
                      <tr key={session.id}>
                        <td className="whitespace-nowrap">{formatDateTime(session.created_at)}</td>
                        <td className="font-mono font-semibold tracking-wider">{session.code}</td>
                        <td>
                          <Badge tone={status.tone}>{status.label}</Badge>
                        </td>
                        <td className="num">{session.players}</td>
                        <td className="num">{session.questions}</td>
                        <td className="num">
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setViewing(session)} disabled={session.players === 0}>
                            {session.players === 0 ? 'Nobody joined' : 'See results'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">
              The big-screen link of an earlier game cannot be shown again. If you have lost it for a game that has not finished, start a new one.
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
          Game <strong>{liveGame?.code}</strong> stays valid, but its big-screen link will be replaced on this page by the new game's link and
          cannot be shown again.
        </p>
        <p>If you still need it, copy the big-screen link first.</p>
      </ConfirmDialog>

      <Dialog open={viewing !== null} onClose={() => setViewing(null)} title={viewing ? `Results of game ${viewing.code}` : 'Results'} wide>
        {viewing && <SessionResults session={viewing} />}
      </Dialog>
    </div>
  )
}

/** The game that was just created: its join code, and the one-time link that runs it. */
function ReadyGame({ game }: { game: LiveCreated }) {
  const elsewhere = !isThisSite(game.joinUrl)
  return (
    <section className="card p-4 sm:p-5" aria-label={`Live game ${game.code}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-bold">Your live game is ready</h2>
          <p className="mt-0.5 text-sm text-muted">{plural(game.questions, 'question')} lined up. Players can join as soon as the big screen is open.</p>
        </div>
        <a className="btn btn-primary btn-lg" href={game.hostUrl} target="_blank" rel="noopener">
          Open the big screen
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
          <strong className="block">The big-screen link is shown only once</strong>
          <p className="mt-1">
            It contains the key that controls the game, so anyone who has it can run the game: open it only on the computer connected to the big
            screen, and do not share it with players. It stays here until you leave this quiz or reload the page. After that it cannot be shown
            again and you would have to start a new game.
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
      <div className="ad-table-wrap">
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
