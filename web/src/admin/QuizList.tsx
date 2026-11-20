import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ErrorNote } from '../components/ui'
import { call, errorText } from './api'
import { Badge, Dialog, DialogActions, EmptyState, ErrorBlock, Field, LoadingBlock, PageHeader } from './components'
import { usePageTitle } from './context'
import { formatDateTime, fromLocalInput, meetingHint, QUIZ_STATUS } from './format'
import { useLoad } from './hooks'
import { paths } from './paths'
import type { Quiz, QuizListItem } from './types'

export default function QuizList() {
  usePageTitle('Quizzes')
  const navigate = useNavigate()
  const { data, error, loading, reload } = useLoad<{ quizzes: QuizListItem[] }>('/quizzes')
  const [creating, setCreating] = useState(false)

  const newQuizButton = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      New quiz
    </button>
  )

  return (
    <>
      <PageHeader
        title="Quizzes"
        description="One quiz per report. Open a quiz to check its questions, share the link, see who is ready and run a live game."
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
        <ul className="grid gap-4 md:grid-cols-2">
          {data.quizzes.map((quiz) => (
            <li key={quiz.id}>
              <QuizCard quiz={quiz} />
            </li>
          ))}
        </ul>
      )}

      {creating && <NewQuizDialog onClose={() => setCreating(false)} onCreated={(quiz) => navigate(paths.quiz(quiz.id))} />}
    </>
  )
}

function QuizCard({ quiz }: { quiz: QuizListItem }) {
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
    <Link to={paths.quiz(quiz.id)} className="card h-full p-4">
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
