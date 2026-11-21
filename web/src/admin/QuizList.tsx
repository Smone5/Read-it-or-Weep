import { useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ErrorNote } from '../components/ui'
import { call, errorText } from './api'
import { Badge, Dialog, DialogActions, EmptyState, ErrorBlock, Field, LoadingBlock, PageHeader } from './components'
import { useNotify, usePageTitle } from './context'
import { formatDateTime, fromLocalInput, meetingHint, QUIZ_STATUS } from './format'
import { useLoad } from './hooks'
import { paths } from './paths'
import type { Quiz, QuizListItem } from './types'

type StatusFilter = 'all' | 'open' | 'draft' | 'closed'
type SortKey = 'meeting' | 'newest'

export default function QuizList() {
  usePageTitle('Quizzes')
  const navigate = useNavigate()
  const notify = useNotify()
  const { data, error, loading, reload, refresh } = useLoad<{ quizzes: QuizListItem[] }>('/quizzes')
  const [creating, setCreating] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SortKey>('meeting')
  const [togglingId, setTogglingId] = useState<string | null>(null)

  const newQuizButton = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      New quiz
    </button>
  )

  const counts = useMemo(() => {
    const list = data?.quizzes ?? []
    return {
      all: list.length,
      open: list.filter((q) => q.status === 'open').length,
      draft: list.filter((q) => q.status === 'draft').length,
      closed: list.filter((q) => q.status === 'closed').length,
    }
  }, [data])

  const visible = useMemo(() => {
    const list = data?.quizzes ?? []
    const needle = search.trim().toLowerCase()
    const filtered = list.filter((q) => {
      if (statusFilter !== 'all' && q.status !== statusFilter) return false
      if (!needle) return true
      return q.title.toLowerCase().includes(needle) || q.slug.toLowerCase().includes(needle)
    })
    return [...filtered].sort((a, b) => {
      if (sortBy === 'meeting') {
        const am = a.meetingAt ? new Date(a.meetingAt).getTime() : Number.MAX_SAFE_INTEGER
        const bm = b.meetingAt ? new Date(b.meetingAt).getTime() : Number.MAX_SAFE_INTEGER
        if (am !== bm) return am - bm
      }
      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
    })
  }, [data, statusFilter, search, sortBy])

  const toggleStatus = async (quiz: QuizListItem) => {
    if (togglingId) return
    const next = quiz.status === 'open' ? 'closed' : 'open'
    setTogglingId(quiz.id)
    try {
      await call(`/quizzes/${quiz.id}`, { method: 'PATCH', body: { status: next } })
      notify(next === 'closed' ? `Closed "${quiz.title}".` : `Opened "${quiz.title}" for players.`)
      await refresh().catch(() => {})
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setTogglingId(null)
    }
  }

  return (
    <>
      <PageHeader
        title="Quizzes"
        description="One quiz per report or meeting packet. Close finished quizzes to keep the player front door clean."
        actions={newQuizButton}
      />

      {!data && loading && <LoadingBlock label="Loading your quizzes" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}
      {data && data.quizzes.length === 0 && (
        <EmptyState title="No quizzes yet" action={newQuizButton}>
          Create a quiz, upload the report people should read, and the questions are written for you.
        </EmptyState>
      )}
      {data && data.quizzes.length > 0 && (
        <>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Filter quizzes by status">
              {(['all', 'open', 'draft', 'closed'] as StatusFilter[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === key}
                  className={`btn btn-sm ${statusFilter === key ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setStatusFilter(key)}
                >
                  {key === 'all' ? 'All' : key === 'open' ? 'Open' : key === 'draft' ? 'Draft' : 'Closed'} ({counts[key]})
                </button>
              ))}
            </div>
            <div className="flex flex-1 flex-wrap items-center justify-end gap-2 sm:flex-initial">
              <input
                type="search"
                className="field min-w-[180px] flex-1 sm:w-56 sm:flex-initial"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search quizzes…"
                aria-label="Search quizzes"
              />
              <select
                className="field w-auto"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortKey)}
                aria-label="Sort quizzes"
              >
                <option value="meeting">Sort: Upcoming meeting</option>
                <option value="newest">Sort: Newest first</option>
              </select>
            </div>
          </div>

          {visible.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted">No quizzes match that filter.</p>
          ) : (
            <ul className="grid gap-4 md:grid-cols-2">
              {visible.map((quiz) => (
                <li key={quiz.id}>
                  <QuizCard
                    quiz={quiz}
                    busy={togglingId === quiz.id}
                    onToggleStatus={() => void toggleStatus(quiz)}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {creating && <NewQuizDialog onClose={() => setCreating(false)} onCreated={(quiz) => navigate(paths.quiz(quiz.id))} />}
    </>
  )
}

function QuizCard({ quiz, busy, onToggleStatus }: { quiz: QuizListItem; busy: boolean; onToggleStatus: () => void }) {
  const status = QUIZ_STATUS[quiz.status]
  const { overview } = quiz
  const hint = meetingHint(quiz.meetingAt)
  const counts: { label: string; value: number; warn?: boolean }[] = [
    { label: 'Live questions', value: overview.approved },
    { label: 'Waiting for review', value: overview.pending, warn: overview.pending > 0 },
    { label: 'Players', value: overview.players },
    { label: 'Games played', value: overview.games },
  ]
  return (
    <div className="card flex h-full flex-col p-4">
      <Link to={paths.quiz(quiz.id)} className="flex-1">
        <div className="flex items-start justify-between gap-3">
          <h2 className="min-w-0 text-base font-bold">{quiz.title}</h2>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted">
          {quiz.meetingAt ? (
            <>
              Meeting: {formatDateTime(quiz.meetingAt)}
              {hint && ` (${hint})`}
            </>
          ) : (
            'No meeting date set'
          )}
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          {counts.map((count) => (
            <div key={count.label}>
              <dt className="text-xs text-muted">{count.label}</dt>
              <dd className="text-lg font-bold tabular-nums" style={count.warn ? { color: 'var(--warn)' } : undefined}>
                {count.value}
              </dd>
            </div>
          ))}
        </dl>
        {(overview.documents === 0 || overview.flagged > 0) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {overview.documents === 0 && <Badge tone="warn">No report uploaded yet</Badge>}
            {overview.flagged > 0 && (
              <Badge tone="bad">
                {overview.flagged} flagged by players
              </Badge>
            )}
          </div>
        )}
      </Link>
      <div className="mt-3 flex items-center justify-between gap-2 border-t pt-2.5" style={{ borderColor: 'var(--line)' }}>
        <span className="truncate font-mono text-xs text-muted">/q/{quiz.slug}</span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={busy || (quiz.status !== 'open' && overview.approved < 3)}
          title={quiz.status !== 'open' && overview.approved < 3 ? 'Needs at least 3 live questions before opening' : undefined}
          onClick={onToggleStatus}
        >
          {busy ? 'Saving…' : quiz.status === 'open' ? 'Close' : 'Open'}
        </button>
      </div>
    </div>
  )
}

function NewQuizDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (quiz: Quiz) => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [meeting, setMeeting] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (title.trim().length < 2) {
      setError('Give the quiz a title of at least two characters.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await call<{ quiz: Quiz }>('/quizzes', {
        method: 'POST',
        body: { title: title.trim(), description: description.trim() || null, meetingAt: fromLocalInput(meeting) },
      })
      onCreated(result.quiz)
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={busy ? () => {} : onClose} title="New quiz">
      <form className="grid gap-4" onSubmit={submit}>
        <Field label="Title" hint="Players see this. For example: Q3 Business Review.">
          {(props) => <input className="field" {...props} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required />}
        </Field>
        <Field label="Description (optional)" hint="A line or two shown to players before they start.">
          {(props) => <textarea className="field" {...props} value={description} onChange={(event) => setDescription(event.target.value)} maxLength={600} rows={3} />}
        </Field>
        <Field label="Meeting date and time (optional)" hint="Shown to players as a countdown.">
          {(props) => <input className="field" type="datetime-local" {...props} value={meeting} onChange={(event) => setMeeting(event.target.value)} />}
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Creating…' : 'Create quiz'}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
