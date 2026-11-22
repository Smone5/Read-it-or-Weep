import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { call, download, errorText } from '../api'
import { Badge, Bar, ConfirmDialog, EmptyState, ErrorBlock, LoadingBlock, Note, Panel, Segmented, Stat } from '../components'
import { useNotify } from '../context'
import { decadeLabel, formatDate, plural, timeAgo } from '../format'
import { useLoad } from '../hooks'
import { paths } from '../paths'
import type { Leaderboard, ModeId, Participant, Results } from '../types'
import { useQuiz } from './context'

export default function ResultsTab() {
  const { detail, refresh: refreshQuiz } = useQuiz()
  const notify = useNotify()
  const { quiz, rules } = detail
  const { data, error, loading, reload, refresh } = useLoad<Results>(`/quizzes/${quiz.id}/results`)
  const [refreshing, setRefreshing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [askingReset, setAskingReset] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)

  const resetAll = async () => {
    setResetBusy(true)
    setResetError(null)
    try {
      await call(`/quizzes/${quiz.id}/reset-results`, { method: 'POST' })
      setAskingReset(false)
      notify('All play-test runs and scores have been cleared.')
      await Promise.all([refresh().catch(() => {}), refreshQuiz().catch(() => {})])
    } catch (reason) {
      setResetError(errorText(reason))
    } finally {
      setResetBusy(false)
    }
  }

  const refreshNow = async () => {
    setRefreshing(true)
    try {
      await refresh()
      notify('Results are up to date.')
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setRefreshing(false)
    }
  }

  const exportCsv = async () => {
    setExporting(true)
    try {
      await download(`/quizzes/${quiz.id}/results.csv`, `${quiz.slug}-results.csv`)
    } catch (reason) {
      notify(errorText(reason, 'Could not download the file. Please try again.'), 'bad')
    } finally {
      setExporting(false)
    }
  }

  if (!data) {
    if (error) return <ErrorBlock message={error} onRetry={reload} />
    return <LoadingBlock label={loading ? 'Loading the results' : 'Loading'} />
  }

  const people = data.participants
  const answering = people.filter((person) => person.answered > 0)
  const averageAccuracy = answering.length ? Math.round(answering.reduce((sum, person) => sum + person.accuracy, 0) / answering.length) : null
  const games = people.reduce((sum, person) => sum + person.games, 0)
  const hasAnyResults = people.length > 0 || detail.overview.games > 0 || (detail.overview.live_sessions ?? 0) > 0

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Results</h2>
          <p className="text-sm text-muted">Who has played, who is ready for the meeting, and which parts of the report did not land.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-ghost" onClick={refreshNow} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          {hasAnyResults && (
            <button type="button" className="btn btn-ghost" onClick={() => { setResetError(null); setAskingReset(true) }}>
              {quiz.status === 'draft' ? 'Clear test results' : 'Reset results'}
            </button>
          )}
          <button type="button" className="btn btn-primary" onClick={exportCsv} disabled={exporting || people.length === 0}>
            {exporting ? 'Preparing…' : 'Export CSV'}
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={askingReset}
        title={quiz.status === 'draft' ? 'Clear test results?' : 'Reset all results for this quiz?'}
        confirmLabel={quiz.status === 'draft' ? 'Clear test results' : 'Reset all results'}
        busyLabel="Clearing…"
        busy={resetBusy}
        error={resetError}
        onConfirm={() => void resetAll()}
        onCancel={() => !resetBusy && setAskingReset(false)}
      >
        <p>
          This removes all solo games, live room scores, high scores, and ratings for <strong>{quiz.title}</strong>.
        </p>
        <p className="mt-2 text-muted">Your uploaded report, sections, and all questions stay untouched.</p>
      </ConfirmDialog>

      {people.length === 0 ? (
        <EmptyState
          title="Nobody has played yet"
          action={
            <Link className="btn btn-primary" to={paths.quiz(quiz.id)}>
              Get the play link
            </Link>
          }
        >
          {quiz.status === 'open'
            ? 'Share the play link and results appear here as people play.'
            : 'The quiz is not open, so only admins can play it. Open it on the Overview tab, then share the link.'}
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Participants" value={people.length} hint={`${plural(games, 'game')} finished`} />
            <Stat
              label="Ready for the meeting"
              value={`${data.ready} of ${people.length}`}
              hint={`Cleared at least ${data.threshold}% of the sections`}
            />
            <Stat
              label="Average accuracy"
              value={averageAccuracy === null ? '—' : `${averageAccuracy}%`}
              hint="Report questions answered correctly, per person"
            />
            <Stat
              label="Feedback"
              value={data.feedback.average === null ? '—' : `${data.feedback.average.toFixed(1)} / 5`}
              hint={data.feedback.count ? plural(data.feedback.count, 'rating') : 'No ratings yet'}
            />
          </div>

          <ParticipantsPanel people={people} threshold={data.threshold} />
          <InsightsPanel insights={data.insights} quizId={quiz.id} />
          <LeaderboardsPanel boards={data.leaderboards} hiddenFromPlayers={!rules.leaderboard} />
          <FeedbackPanel feedback={data.feedback} />
        </>
      )}
    </div>
  )
}

// ---- participants ---------------------------------------------------------------------

type SortKey = 'name' | 'team' | 'readiness' | 'sectionsCleared' | 'accuracy' | 'games' | 'bestScore' | 'peeksUsed' | 'lastPlayedAt'
type Sort = { key: SortKey; descending: boolean }

const COLUMNS: { key: SortKey; label: string; numeric?: boolean; title?: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'team', label: 'Team' },
  { key: 'readiness', label: 'Readiness' },
  { key: 'sectionsCleared', label: 'Sections cleared', numeric: true, title: 'Sections cleared (≥75% across their last 4 answers, including at least 1 multiple-choice question; Speed Run excluded)' },
  { key: 'accuracy', label: 'Accuracy', numeric: true, title: 'Report questions answered correctly' },
  { key: 'games', label: 'Games', numeric: true },
  { key: 'bestScore', label: 'Best score', numeric: true },
  { key: 'peeksUsed', label: 'Peeks used', numeric: true, title: 'How often they used the Peek lifeline to look at the report during a game' },
  { key: 'lastPlayedAt', label: 'Last played' },
]

const WHO = [
  { value: 'all', label: 'Everyone' },
  { value: 'ready', label: 'Ready' },
  { value: 'not-ready', label: 'Not ready yet' },
] as const

function compare(a: Participant, b: Participant, key: SortKey): number {
  const left = a[key]
  const right = b[key]
  if (left === null && right === null) return 0
  if (left === null) return 1 // people with nothing to show always go last
  if (right === null) return -1
  if (typeof left === 'number' && typeof right === 'number') return left - right
  return String(left).localeCompare(String(right), undefined, { sensitivity: 'base' })
}

const PARTICIPANTS_PAGE = 50

function ParticipantsPanel({ people, threshold }: { people: Participant[]; threshold: number }) {
  const [who, setWho] = useState<(typeof WHO)[number]['value']>('all')
  const [search, setSearch] = useState('')
  const [shown, setShown] = useState(PARTICIPANTS_PAGE)
  const [sort, setSort] = useState<Sort | null>(null) // null keeps the server's order: most sections cleared first

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const filtered = people.filter((person) => {
      if (who !== 'all' && (who === 'ready') !== person.ready) return false
      if (!needle) return true
      return (
        person.name.toLowerCase().includes(needle) ||
        person.email.toLowerCase().includes(needle) ||
        (person.team ?? '').toLowerCase().includes(needle)
      )
    })
    if (!sort) return filtered
    const sorted = [...filtered].sort((a, b) => compare(a, b, sort.key))
    // Missing values stay at the bottom whichever way the column is sorted.
    return sort.descending ? sorted.reverse().sort((a, b) => Number(a[sort.key] === null) - Number(b[sort.key] === null)) : sorted
  }, [people, who, search, sort])

  const visibleRows = rows.slice(0, shown)

  const toggleSort = (key: SortKey, numeric: boolean) =>
    setSort((current) => (current?.key === key ? { key, descending: !current.descending } : { key, descending: numeric || key === 'readiness' || key === 'lastPlayedAt' }))

  const notReady = people.filter((person) => !person.ready).length

  return (
    <Panel
      title="Participants"
      description={
        notReady === 0
          ? 'Everyone who has played is ready.'
          : `${plural(notReady, 'person', 'people')} still ${notReady === 1 ? 'needs' : 'need'} to clear ${threshold}% of the sections to count as ready.`
      }
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            className="field w-48 sm:w-56"
            placeholder="Search name, email, team…"
            aria-label="Search participants"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              setShown(PARTICIPANTS_PAGE)
            }}
          />
          <Segmented
            label="Show"
            options={WHO}
            value={who}
            onChange={(next) => {
              setWho(next)
              setShown(PARTICIPANTS_PAGE)
            }}
          />
        </div>
      }
    >
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          {search.trim() ? 'No participants match that search.' : who === 'ready' ? 'Nobody is ready yet.' : 'Everyone is ready.'}
        </p>
      ) : (
        <>
          <div className="ad-table-wrap">
            <table className="ad-table" style={{ minWidth: 980 }}>
              <thead>
                <tr>
                  {COLUMNS.map((column) => (
                    <th
                      key={column.key}
                      scope="col"
                      className={column.numeric ? 'num' : undefined}
                      aria-sort={sort?.key === column.key ? (sort.descending ? 'descending' : 'ascending') : undefined}
                    >
                      <button type="button" className="ad-sort" onClick={() => toggleSort(column.key, Boolean(column.numeric))} title={column.title ?? `Sort by ${column.label.toLowerCase()}`}>
                        {column.label}
                        <span aria-hidden>{sort?.key === column.key ? (sort.descending ? '▼' : '▲') : '↕'}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((person) => (
                  <tr key={person.id}>
                    <th scope="row">
                      <div className="font-semibold">{person.name}</div>
                      <div className="font-normal text-muted [overflow-wrap:anywhere]">{person.email}</div>
                    </th>
                    <td>{person.team ?? <span className="text-muted">—</span>}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="w-20 flex-none">
                          <Bar value={person.readiness} label={`Readiness of ${person.name}`} tone={person.ready ? 'var(--good)' : undefined} />
                        </div>
                        <span className="w-10 tabular-nums">{person.readiness}%</span>
                        {person.ready && <Badge tone="good">Ready</Badge>}
                      </div>
                    </td>
                    <td className="num">
                      {person.sectionsCleared} of {person.sectionsTotal}
                    </td>
                    <td className="num">{person.answered ? `${person.accuracy}%` : '—'}</td>
                    <td className="num">{person.games}</td>
                    <td className="num">{person.bestScore === null ? '—' : person.bestScore.toLocaleString()}</td>
                    <td className="num">{person.peeksUsed}</td>
                    <td className="whitespace-nowrap">{timeAgo(person.lastPlayedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > shown && (
            <div className="mt-3 flex items-center justify-between gap-2 text-xs text-muted">
              <span>
                Showing {visibleRows.length} of {rows.length} participants
              </span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShown((n) => n + PARTICIPANTS_PAGE)}>
                Show {Math.min(PARTICIPANTS_PAGE, rows.length - shown)} more
              </button>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}

// ---- what did not land ----------------------------------------------------------------

/** Red, amber or green for a share of correct answers: one shade for a bar, a darker one that is readable as text. */
function accuracyTone(accuracy: number): { bar: string; text: string } {
  if (accuracy < 50) return { bar: 'var(--bad)', text: 'var(--bad)' }
  if (accuracy < 70) return { bar: 'var(--accent)', text: 'var(--warn)' }
  return { bar: 'var(--good)', text: 'var(--good)' }
}

function InsightsPanel({ insights, quizId }: { insights: Results['insights']; quizId: string }) {
  const weakest = new Set(insights.weakestSections.map((section) => section.id))
  const answered = insights.sections.some((section) => section.accuracy !== null)

  return (
    <Panel title="What did not land" description="How well each part of the report was answered. Low numbers are worth going over in the meeting.">
      {!answered ? (
        <p className="py-4 text-sm text-muted">Not enough answers yet to say.</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <h3 className="text-sm font-bold">By section</h3>
            <ul className="mt-2 grid gap-3">
              {insights.sections.map((section) => (
                <li key={section.id}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                    <span className="min-w-0 font-semibold [overflow-wrap:anywhere]">
                      {section.heading}{' '}
                      {weakest.has(section.id) && section.accuracy !== null && section.accuracy < 80 && <Badge tone="warn">Weakest</Badge>}
                    </span>
                    <span className="flex-none tabular-nums text-muted">
                      {section.accuracy === null
                        ? 'No answers yet'
                        : `${section.accuracy}% correct · ${plural(section.answered, 'answer')} from ${plural(section.players, 'person', 'people')}`}
                    </span>
                  </div>
                  <div className="mt-1">
                    <Bar
                      value={section.accuracy ?? 0}
                      label={`${section.heading}: share answered correctly`}
                      tone={section.accuracy === null ? undefined : accuracyTone(section.accuracy).bar}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-bold">Hardest questions</h3>
            {insights.hardestQuestions.length === 0 ? (
              <p className="mt-2 text-sm text-muted">Questions appear here once they have been asked at least three times.</p>
            ) : (
              <ol className="mt-2 grid gap-2">
                {insights.hardestQuestions.map((question) => (
                  <li key={question.id} className="rounded-lg border border-line p-2.5 text-sm">
                    <div className="flex items-start justify-between gap-2">
                      <p className="min-w-0 flex-1 font-semibold [overflow-wrap:anywhere]">{question.question_text}</p>
                      <Link
                        className="ad-link flex-none text-xs font-semibold"
                        to={`${paths.quiz(quizId, 'questions')}?edit=${encodeURIComponent(question.id)}`}
                      >
                        Edit
                      </Link>
                    </div>
                    <p className="mt-0.5 text-muted">
                      <span style={{ color: accuracyTone(question.accuracy).text, fontWeight: 600 }}>{question.accuracy}% correct</span>
                      {' · '}asked {plural(question.times_served, 'time')}
                      {question.heading ? ` · ${question.heading}` : ''}
                      {question.kind === 'fact_fake' ? ' · Fact or Fake' : ''}
                      {question.flag_count > 0 ? ` · flagged by ${plural(question.flag_count, 'player')}` : ''}
                    </p>
                  </li>
                ))}
              </ol>
            )}
            <p className="mt-3 text-xs text-muted">
              A question almost nobody gets right may be badly worded rather than hard.{' '}
              <Link className="ad-link" to={paths.quiz(quizId, 'questions')}>
                Check it in the question bank
              </Link>
            </p>
          </div>
        </div>
      )}
    </Panel>
  )
}

// ---- leaderboards ---------------------------------------------------------------------

function LeaderboardsPanel({ boards, hiddenFromPlayers }: { boards: Results['leaderboards']; hiddenFromPlayers: boolean }) {
  const list = Object.values(boards).filter((board): board is Leaderboard => Boolean(board))
  const [mode, setMode] = useState<ModeId | null>(null)
  const board = list.find((candidate) => candidate.mode === mode) ?? list.find((candidate) => candidate.entries.length > 0) ?? list[0]

  if (!board) return null

  return (
    <Panel
      title="Leaderboards"
      description="Each person's best game in each style of play. Players see first names and an initial only."
      actions={
        list.length > 1 ? (
          <Segmented label="Style of play" options={list.map((candidate) => ({ value: candidate.mode, label: candidate.modeName }))} value={board.mode} onChange={setMode} />
        ) : undefined
      }
    >
      {hiddenFromPlayers && (
        <div className="mb-3">
          <Note tone="info">The leaderboard is switched off for players on the Play styles tab. Only admins see this.</Note>
        </div>
      )}
      {board.entries.length === 0 ? (
        <p className="py-4 text-sm text-muted">Nobody has finished a {board.modeName} game yet.</p>
      ) : (
        <>
          <div className="ad-table-wrap">
            <table className="ad-table" style={{ minWidth: 520 }}>
              <thead>
                <tr>
                  <th scope="col" className="num">
                    Place
                  </th>
                  <th scope="col">Name</th>
                  <th scope="col">Team</th>
                  <th scope="col" className="num">
                    Score
                  </th>
                  <th scope="col">Title earned</th>
                  <th scope="col">{board.mode === 'straight' ? 'Passed' : 'Music'}</th>
                </tr>
              </thead>
              <tbody>
                {board.entries.map((entry) => (
                  <tr key={`${entry.rank}-${entry.name}`}>
                    <td className="num">{entry.rank}</td>
                    <td className="font-semibold">{entry.name}</td>
                    <td>{entry.team ?? <span className="text-muted">—</span>}</td>
                    <td className="num">{entry.score.toLocaleString()}</td>
                    <td>{entry.badge ?? <span className="text-muted">—</span>}</td>
                    <td>
                      {board.mode === 'straight' ? (
                        entry.passed === null ? (
                          <span className="text-muted">—</span>
                        ) : (
                          <Badge tone={entry.passed ? 'good' : 'neutral'}>{entry.passed ? 'Passed' : 'Not yet'}</Badge>
                        )
                      ) : entry.decade ? (
                        decadeLabel(entry.decade)
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {board.players > board.entries.length && (
            <p className="mt-2 text-xs text-muted">
              Showing the top {board.entries.length} of {board.players} players. The CSV export lists everyone.
            </p>
          )}
          {board.teams.length > 0 && (
            <p className="mt-3 text-sm">
              <span className="font-semibold">Team averages: </span>
              {board.teams.map((team) => `${team.team} ${team.average.toLocaleString()} (${plural(team.players, 'player')})`).join(' · ')}
            </p>
          )}
        </>
      )}
    </Panel>
  )
}

// ---- feedback -------------------------------------------------------------------------

function Stars({ count }: { count: number | null }) {
  if (count === null) return <span className="text-muted">No rating</span>
  return (
    <span role="img" aria-label={`${count} out of 5 stars`} style={{ color: 'var(--warn)' }}>
      <span aria-hidden>
        {'★'.repeat(count)}
        <span style={{ opacity: 0.3 }}>{'★'.repeat(Math.max(0, 5 - count))}</span>
      </span>
    </span>
  )
}

function FeedbackPanel({ feedback }: { feedback: Results['feedback'] }) {
  return (
    <Panel
      title="What players said"
      description={
        feedback.average === null
          ? 'Players can rate the game when they finish.'
          : `Rated ${feedback.average.toFixed(1)} out of 5 on average, from ${plural(feedback.count, 'rating')}.`
      }
    >
      {feedback.comments.length === 0 ? (
        <p className="py-2 text-sm text-muted">No comments yet.</p>
      ) : (
        <ul className="grid gap-2">
          {feedback.comments.map((comment, index) => (
            <li key={`${comment.created_at}-${index}`} className="rounded-lg border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Stars count={comment.stars} />
                <span className="text-xs text-muted">{formatDate(comment.created_at)}</span>
              </div>
              <p className="mt-1 whitespace-pre-line [overflow-wrap:anywhere]">{comment.comment}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
