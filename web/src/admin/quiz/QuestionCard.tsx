import { Badge, Note } from '../components'
import { DIFFICULTY_LABEL, plural, QUESTION_STATUS } from '../format'
import type { Question } from '../types'
import { LETTERS, originLabel, questionType, SKILL_LABEL, TYPE_LABEL } from './questions'

export type QuestionActions = {
  onSelect: (question: Question, selected: boolean) => void
  onStatus: (question: Question, status: 'approved' | 'rejected') => void
  onPin: (question: Question, pinned: boolean) => void
  onEdit: (question: Question) => void
  onDelete: (question: Question) => void
  onPreview: (question: Question) => void
}

/** One question in the bank: what players see, the right answer, how it is doing, and what you can do with it. */
export default function QuestionCard({
  question,
  selected,
  busy,
  actions,
}: {
  question: Question
  selected: boolean
  busy: boolean
  actions: QuestionActions
}) {
  const status = QUESTION_STATUS[question.status]
  const type = questionType(question)
  const struggling = question.accuracy !== null && question.served >= 3 && question.accuracy < 30
  const lostPicture = question.sourceType === 'image' && !question.imageId

  return (
    <article className="card ad-question p-3 sm:p-4" data-selected={selected} data-status={question.status}>
      <div className="flex items-start gap-2 sm:gap-3">
        <label className="-ml-2 -mt-1.5 grid h-10 w-10 flex-none cursor-pointer place-items-center">
          <input
            type="checkbox"
            checked={selected}
            onChange={(event) => actions.onSelect(question, event.target.checked)}
            aria-label={`Select the question: ${question.text}`}
          />
        </label>

        <div className="ad-question-body min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={status.tone}>{status.label}</Badge>
            <Badge tone={question.origin === 'manual' ? 'brand' : 'neutral'}>{originLabel(question)}</Badge>
            {question.pinned && (
              <Badge tone="brand" title="Pinned questions are asked before the others">
                Pinned
              </Badge>
            )}
            <Badge>{TYPE_LABEL[type]}</Badge>
            <Badge>{DIFFICULTY_LABEL[question.difficulty]}</Badge>
            <Badge title={question.skill === 'recall' ? 'Tests remembering a detail' : 'Tests understanding what it means'}>
              {SKILL_LABEL[question.skill]}
            </Badge>
            {question.videoUrl && (
              <Badge tone="info" title={question.videoTitle || question.videoUrl}>
                Video{question.videoTitle ? `: ${question.videoTitle.slice(0, 28)}${question.videoTitle.length > 28 ? '…' : ''}` : ''}
              </Badge>
            )}
            {!question.imageUrl && question.revealImageUrl && (
              <Badge tone="neutral" title="Shows a supporting chart or diagram on answer reveal">
                Reveal visual
              </Badge>
            )}
            {question.flags > 0 && <Badge tone="bad">Flagged by {plural(question.flags, 'player')}</Badge>}
          </div>

          <div className="mt-2 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {question.title && <p className="text-xs font-bold uppercase tracking-wide text-muted">{question.title}</p>}
              <p className="text-[15px] font-semibold leading-snug [overflow-wrap:anywhere]">{question.text}</p>
            </div>
            {(question.imageUrl || question.revealImageUrl) && (
              <button
                type="button"
                className="ad-thumb h-16 w-24 flex-none"
                onClick={() => actions.onPreview(question)}
                aria-label={question.imageUrl ? 'Show the picture for this question larger' : 'Show the answer reveal visual larger'}
                title={question.imageUrl ? 'Question picture' : 'Answer reveal visual'}
              >
                <img src={question.imageUrl ?? question.revealImageUrl ?? ''} alt="" loading="lazy" />
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
            <details className="mt-2">
              <summary className="cursor-pointer py-1 text-sm text-muted">Why this is the answer</summary>
              <div className="mt-1 grid gap-2 rounded-lg bg-surface-2 p-3 text-sm">
                {question.explanation && <p>{question.explanation}</p>}
                {question.sourceQuote && (
                  <p className="text-muted">
                    {/* For a question written about a picture, the "quote" says where in the picture to look. */}
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
              reword it so it makes sense on its own, or delete it.
            </Note>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap justify-end gap-2 border-t border-line pt-3">
        {question.status !== 'approved' && (
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => actions.onStatus(question, 'approved')}>
            Approve
          </button>
        )}
        {question.status !== 'rejected' && (
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onStatus(question, 'rejected')}>
            Reject
          </button>
        )}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={busy}
          title="Pinned questions are asked before the others"
          onClick={() => actions.onPin(question, !question.pinned)}
        >
          {question.pinned ? 'Unpin' : 'Pin'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => actions.onEdit(question)}>
          Edit
        </button>
        <button type="button" className="btn btn-danger btn-sm" disabled={busy} onClick={() => actions.onDelete(question)}>
          Delete
        </button>
      </div>
    </article>
  )
}
