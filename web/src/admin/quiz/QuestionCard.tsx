import { Badge, Note } from '../components'
import { DIFFICULTY_LABEL, plural, QUESTION_STATUS } from '../format'
import type { Question } from '../types'
import { LETTERS, originLabel, questionType, SKILL_LABEL, TYPE_LABEL } from './questions'

export type QuestionActions = {
  onSelect: (question: Question, selected: boolean, shiftKey: boolean) => void
  onStatus: (question: Question, status: 'approved' | 'rejected') => void
  onDismissFlags: (question: Question) => void
  onPin: (question: Question, pinned: boolean) => void
  onEdit: (question: Question) => void
  onDuplicate: (question: Question) => void
  onDelete: (question: Question) => void
  onPreview: (question: Question) => void
  onPlayerPreview: (question: Question) => void
}

/** One question in the bank: what players see, the right answer, how it is doing, and what you can do with it. */
export default function QuestionCard({
  question,
  selected,
  focused = false,
  expandExplanation = false,
  busy,
  actions,
}: {
  question: Question
  selected: boolean
  focused?: boolean
  expandExplanation?: boolean
  busy: boolean
  actions: QuestionActions
}) {
  const status = QUESTION_STATUS[question.status]
  const type = questionType(question)
  const struggling = question.accuracy !== null && question.served >= 3 && question.accuracy < 30
  const lostPicture = question.sourceType === 'image' && !question.imageId
  const metaSummary = [TYPE_LABEL[type], DIFFICULTY_LABEL[question.difficulty], SKILL_LABEL[question.skill], originLabel(question)].join(' · ')

  return (
    <article
      className="card ad-question p-3 sm:p-4"
      data-selected={selected}
      data-status={question.status}
      style={focused ? { boxShadow: '0 0 0 2px var(--brand)' } : undefined}
    >
      <div className="flex items-start gap-2 sm:gap-3">
        <label className="-ml-2 -mt-1.5 grid h-11 w-11 flex-none cursor-pointer place-items-center">
          <input
            type="checkbox"
            checked={selected}
            onClick={(event) => {
              if (event.shiftKey) {
                event.preventDefault()
                actions.onSelect(question, !selected, true)
              }
            }}
            onChange={(event) => actions.onSelect(question, event.target.checked, false)}
            aria-label={`Select the question: ${question.text}`}
          />
        </label>

        <div className="ad-question-body min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {question.status !== 'approved' && <Badge tone={status.tone}>{status.label}</Badge>}
            {question.pinned && (
              <Badge tone="brand" title="Pinned questions are asked before the others">
                Pinned
              </Badge>
            )}
            {question.flags > 0 && <Badge tone="bad">Flagged ({question.flags})</Badge>}
            {question.videoUrl && (
              <Badge tone="info" title={question.videoTitle || question.videoUrl}>
                Video{question.videoTitle ? `: ${question.videoTitle.slice(0, 26)}${question.videoTitle.length > 26 ? '…' : ''}` : ''}
              </Badge>
            )}
            <span className="text-xs text-muted">{metaSummary}</span>
          </div>

          <div className="mt-2 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {question.title && <p className="text-xs font-bold uppercase tracking-wide text-muted">{question.title}</p>}
              <h3 className="text-[15px] font-semibold leading-snug [overflow-wrap:anywhere]">{question.text}</h3>
            </div>
            {(question.imageUrl || question.revealImageUrl) && (
              <button
                type="button"
                className="ad-thumb relative h-16 w-24 flex-none"
                onClick={() => actions.onPreview(question)}
                aria-label={question.imageUrl ? 'Show the picture for this question larger' : 'Show the answer reveal visual larger'}
                title={question.imageUrl ? 'Question picture' : 'Answer reveal visual'}
              >
                <img src={question.imageUrl ?? question.revealImageUrl ?? ''} alt="" loading="lazy" />
                <span className="pointer-events-none absolute bottom-1 right-1 rounded bg-black/75 px-1.5 py-0.5 text-[10px] font-bold leading-none text-white">
                  {question.imageUrl ? 'Question' : 'Reveal'}
                </span>
              </button>
            )}
          </div>

          {question.kind === 'mcq' && question.options && (
            <ol className="mt-2 grid gap-1.5 sm:grid-cols-2">
              {question.options.map((option, index) => {
                const correct = index === question.correctIndex
                return (
                  <li key={index} className="ad-option" data-correct={correct}>
                    <span className="ad-option-key" aria-hidden>
                      {LETTERS[index] ?? index + 1}
                    </span>
                    <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{option}</span>
                    {correct && <span className="flex-none text-xs font-bold text-good">Correct</span>}
                  </li>
                )
              })}
            </ol>
          )}

          {question.kind === 'fact_fake' && (
            <ul className="mt-2 grid max-w-sm grid-cols-2 gap-1.5">
              {[true, false].map((value) => {
                const correct = question.isTrue === value
                return (
                  <li key={String(value)} className="ad-option" data-correct={correct}>
                    <span className="flex-1 font-bold">{value ? 'FACT' : 'FAKE'}</span>
                    {correct && <span className="flex-none text-xs font-bold text-good">Correct</span>}
                  </li>
                )
              })}
            </ul>
          )}

          {(question.explanation || question.sourceQuote) && (
            <details className="mt-2" open={expandExplanation || undefined}>
              <summary className="cursor-pointer py-1 text-sm text-muted">Why this is the answer</summary>
              <div className="mt-1 grid gap-2 rounded-lg bg-surface-2 p-3 text-sm">
                {question.explanation && <p>{question.explanation}</p>}
                {question.sourceQuote && (
                  <p className="text-muted">
                    <span className="font-semibold">{question.sourceType === 'image' ? 'Where to look: ' : 'From the report: '}</span>
                    {question.sourceType === 'image' ? question.sourceQuote : `“${question.sourceQuote}”`}
                  </p>
                )}
              </div>
            </details>
          )}

          <p className="mt-2 text-sm text-muted">
            {question.section ?? 'No section'}
            {' · '}
            {question.served === 0 ? (
              'Not asked yet'
            ) : (
              <>
                Asked {plural(question.served, 'time')},{' '}
                <span style={struggling ? { color: 'var(--warn)', fontWeight: 600 } : undefined}>{question.accuracy ?? 0}% answered correctly</span>
              </>
            )}
          </p>
        </div>
      </div>

      {(question.flags > 0 || (question.pulledReason && question.status !== 'approved') || lostPicture) && (
        <div className="mt-3 grid gap-2">
          {question.pulledReason && question.status !== 'approved' && (
            <Note tone="warn">
              <strong>Taken out of play automatically:</strong> {question.pulledReason}. Fix it or approve it to put it back, or reject it.
            </Note>
          )}
          {question.flags > 0 && (
            <Note tone="bad">
              <strong>{plural(question.flags, 'player')} flagged this question.</strong>
              {question.flagReasons.length > 0 ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {question.flagReasons.map((reason, index) => (
                    <li key={index} className="[overflow-wrap:anywhere]">
                      {reason}
                    </li>
                  ))}
                </ul>
              ) : (
                ' Nobody said why.'
              )}
            </Note>
          )}
          {lostPicture && (
            <Note tone="warn">
              <strong>This picture question has lost its picture</strong> (the report was processed again). Edit it to choose a picture again,
              switch it to text-only, or delete it before approving.
            </Note>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <button type="button" className="btn btn-danger btn-sm mr-auto" disabled={busy} onClick={() => actions.onDelete(question)}>
          Delete
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onPlayerPreview(question)}>
          Preview
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onDuplicate(question)}>
          Duplicate
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onEdit(question)}>
          Edit
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={busy}
          title="Pinned questions are asked before the others"
          onClick={() => actions.onPin(question, !question.pinned)}
        >
          {question.pinned ? 'Unpin' : 'Pin'}
        </button>
        {question.status === 'approved' && question.flags > 0 && (
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => actions.onDismissFlags(question)}>
            Dismiss flag{question.flags === 1 ? '' : 's'}
          </button>
        )}
        {question.status !== 'rejected' && (
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onStatus(question, 'rejected')}>
            Reject
          </button>
        )}
        {question.status !== 'approved' && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy || lostPicture}
            title={lostPicture ? 'Choose a picture or convert to text-only in Edit before approving' : undefined}
            onClick={() => actions.onStatus(question, 'approved')}
          >
            Approve
          </button>
        )}
      </div>
    </article>
  )
}
