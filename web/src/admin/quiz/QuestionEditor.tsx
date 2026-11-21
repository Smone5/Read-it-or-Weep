import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { ChoiceGroup, Dialog, DialogActions, Field, Note, Segmented, Switch } from '../components'
import type { Difficulty, PictureMode, Question, QuestionKind, QuestionPatch, QuestionWrite, QuizImage, Suggestion } from '../types'
import { useQuiz } from './context'
import { LETTERS } from './questions'

// Limits the server enforces (QuestionWrite and SuggestRequest in admin.py). Editing a question
// with text outside these limits makes the server fail without a readable message, so they are
// checked here first.
const TEXT_MIN = 8
const TEXT_MAX = 300
const ANSWER_MAX = 110
const TITLE_MAX = 60
const EXPLANATION_MAX = 500

type Form = {
  kind: QuestionKind
  text: string
  /** Always four entries. */
  options: string[]
  correctIndex: number
  isTrue: boolean | null
  sectionId: string
  imageId: string
  difficulty: Difficulty
  pinned: boolean
  /** New questions only: go live straight away, or wait for review. */
  live: boolean
  title: string
  explanation: string
  videoUrl: string
  videoTitle: string
}

function initialForm(question: Question | null): Form {
  if (!question) {
    return {
      kind: 'mcq',
      text: '',
      options: ['', '', '', ''],
      correctIndex: 0,
      isTrue: null,
      sectionId: '',
      imageId: '',
      difficulty: 'medium',
      pinned: false,
      live: true,
      title: '',
      explanation: '',
      videoUrl: '',
      videoTitle: '',
    }
  }
  const options = [...(question.options ?? [])]
  while (options.length < 4) options.push('')
  return {
    kind: question.kind,
    text: question.text,
    options: options.slice(0, 4),
    correctIndex: question.correctIndex ?? 0,
    isTrue: question.isTrue,
    sectionId: question.sectionId ?? '',
    imageId: question.imageId ?? '',
    difficulty: question.difficulty,
    pinned: question.pinned,
    live: question.status === 'approved',
    title: question.title ?? '',
    explanation: question.explanation ?? '',
    videoUrl: question.videoUrl ?? '',
    videoTitle: question.videoTitle ?? '',
  }
}

function problemWith(form: Form): string | null {
  const text = form.text.trim()
  const what = form.kind === 'mcq' ? 'question' : 'statement'
  if (text.length < TEXT_MIN) return `Write the ${what} (at least ${TEXT_MIN} characters).`
  if (text.length > TEXT_MAX) return `Keep the ${what} to ${TEXT_MAX} characters or fewer.`
  if (form.kind === 'mcq') {
    const options = form.options.map((option) => option.trim())
    if (options.some((option) => !option)) return 'Fill in all four answers.'
    if (new Set(options.map((option) => option.toLowerCase())).size !== 4) return 'The four answers must all be different.'
  } else if (form.isTrue === null) {
    return 'Say whether the statement is a fact or a fake.'
  }
  return null
}

/**
 * Only what changed. This matters: sending the wording or the answers at all marks an
 * AI question as "edited", so an untouched question must not send them.
 */
function patchFrom(question: Question, form: Form): QuestionPatch {
  const patch: QuestionPatch = {}
  if (form.text.trim() !== question.text) patch.text = form.text.trim()
  if (question.kind === 'mcq') {
    const options = form.options.map((option) => option.trim())
    if (JSON.stringify(options) !== JSON.stringify(question.options ?? [])) patch.options = options
    if (form.correctIndex !== question.correctIndex) patch.correctIndex = form.correctIndex
  } else if (form.isTrue !== null && form.isTrue !== question.isTrue) {
    patch.isTrue = form.isTrue
  }
  if (form.title.trim() !== (question.title ?? '')) patch.title = form.title.trim()
  if (form.explanation.trim() !== (question.explanation ?? '')) patch.explanation = form.explanation.trim()
  if (form.videoUrl.trim() !== (question.videoUrl ?? '')) patch.videoUrl = form.videoUrl.trim()
  if (form.videoTitle.trim() !== (question.videoTitle ?? '')) patch.videoTitle = form.videoTitle.trim()
  if (form.difficulty !== question.difficulty) patch.difficulty = form.difficulty
  if ((form.sectionId || null) !== question.sectionId) patch.sectionId = form.sectionId || null
  return patch
}

const PICTURE_NOTE: Record<PictureMode, string> = {
  auto: 'Picture mode is on Auto, which asks a hand-written picture question from memory: players see the picture blurred until they have answered. If they need to look at it to answer, switch picture mode to “Always show” on the Play styles tab.',
  show: 'Players see the picture while they answer.',
  memory: 'Players see the picture blurred until they have answered.',
}

const DIFFICULTIES = [
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
] as const

const KINDS = [
  { value: 'mcq', label: 'Multiple choice' },
  { value: 'fact_fake', label: 'Fact or Fake' },
] as const

const VERDICTS = [
  { value: 'fact', label: 'Fact', description: 'The statement is true according to the report.' },
  { value: 'fake', label: 'Fake', description: 'One detail has been changed, so it is false.' },
] as const

/** Add a question by hand, or edit an existing one. Pass `question = null` to add. */
export default function QuestionEditor({
  question,
  images,
  onClose,
  onSaved,
}: {
  question: Question | null
  images: QuizImage[]
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const { detail, meta } = useQuiz()
  const quizId = detail.quiz.id
  const [start] = useState(() => initialForm(question))
  const [form, setForm] = useState<Form>(start)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [suggested, setSuggested] = useState<Suggestion | null>(null)
  /** The question and correct answer the last suggestion was made for. */
  const [suggestedFor, setSuggestedFor] = useState<string | null>(null)
  const [suggestError, setSuggestError] = useState<string | null>(null)
  const [confirmingClose, setConfirmingClose] = useState(false)
  const keepEditing = useRef<HTMLButtonElement>(null)

  // The "close without saving?" question appears at the foot of a long form; bring it to the person.
  useEffect(() => {
    if (!confirmingClose) return
    keepEditing.current?.scrollIntoView({ block: 'nearest' })
    keepEditing.current?.focus()
  }, [confirmingClose])
  const radioName = useId()

  const adding = question === null
  const busy = saving || suggesting
  const dirty = JSON.stringify(form) !== JSON.stringify(start)
  const picture = images.find((image) => image.id === form.imageId) ?? null
  const correctAnswer = form.options[form.correctIndex]?.trim() ?? ''
  const canSuggest = form.text.trim().length >= TEXT_MIN && correctAnswer.length > 0
  // The AI's verdict is about one particular question and answer; once either is reworded it no longer applies.
  const asked = `${form.text.trim()}\n${correctAnswer}`
  const verdict = suggested && suggestedFor === asked ? suggested : null

  const set = (patch: Partial<Form>) => {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  const setOption = (index: number, value: string) => set({ options: form.options.map((option, at) => (at === index ? value : option)) })

  // Closing by accident (a stray click outside, the Escape key) should not throw away typing.
  const requestClose = () => {
    if (busy) return
    if (dirty) setConfirmingClose(true)
    else onClose()
  }

  const suggest = async () => {
    if (!canSuggest || busy) return
    setSuggesting(true)
    setSuggestError(null)
    const askedNow = asked
    try {
      const result = await call<Suggestion>(`/quizzes/${quizId}/questions/suggest`, {
        method: 'POST',
        body: { text: form.text.trim(), correctAnswer, sectionId: form.sectionId || null },
      })
      const title = result.title.slice(0, TITLE_MAX)
      const explanation = result.explanation.slice(0, EXPLANATION_MAX)
      const wrong = result.wrongAnswers.map((answer) => answer.slice(0, ANSWER_MAX)).filter(Boolean)
      setForm((current) => {
        const slots = [0, 1, 2, 3].filter((index) => index !== current.correctIndex)
        const options = [...current.options]
        slots.forEach((slot, at) => {
          if (wrong[at]) options[slot] = wrong[at]
        })
        // A title or explanation the person typed themselves is theirs to keep; one from an
        // earlier suggestion is replaced by the new one.
        const mine = (value: string, earlier: string | undefined) => value.trim() !== '' && value !== earlier
        return {
          ...current,
          options,
          title: mine(current.title, suggested?.title.slice(0, TITLE_MAX)) ? current.title : title,
          explanation: mine(current.explanation, suggested?.explanation.slice(0, EXPLANATION_MAX)) ? current.explanation : explanation,
        }
      })
      setSuggested(result)
      setSuggestedFor(askedNow)
    } catch (reason) {
      setSuggestError(errorText(reason))
    } finally {
      setSuggesting(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    const problem = problemWith(form)
    if (problem) {
      setError(problem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (question) {
        const patch = patchFrom(question, form)
        const changed = Object.keys(patch).length > 0
        if (changed) await call(`/questions/${question.id}`, { method: 'PATCH', body: patch })
        onSaved(changed ? 'Question updated.' : 'Nothing was changed.')
      } else {
        const mcq = form.kind === 'mcq'
        const body: QuestionWrite = {
          kind: form.kind,
          title: form.title.trim() || null,
          text: form.text.trim(),
          options: mcq ? form.options.map((option) => option.trim()) : null,
          correctIndex: mcq ? form.correctIndex : null,
          isTrue: mcq ? null : form.isTrue,
          explanation: form.explanation.trim() || null,
          videoUrl: form.videoUrl.trim() || null,
          videoTitle: form.videoTitle.trim() || null,
          difficulty: form.difficulty,
          sectionId: form.sectionId || null,
          imageId: mcq ? form.imageId || null : null, // the game never shows a picture with a Fact-or-Fake statement
          pinned: form.pinned,
          status: form.live ? 'approved' : 'pending',
        }
        await call<{ id: string }>(`/quizzes/${quizId}/questions`, { method: 'POST', body })
        onSaved(form.live ? 'Question added. It is live.' : 'Question added. It is waiting for review.')
      }
    } catch (reason) {
      setError(errorText(reason))
      setSaving(false)
    }
  }

  return (
    <Dialog open onClose={requestClose} title={adding ? 'Add a question' : 'Edit question'} wide>
      <form className="grid gap-5" onSubmit={submit}>
        {adding ? (
          <div>
            <span className="label">Kind of question</span>
            <Segmented label="Kind of question" options={KINDS} value={form.kind} onChange={(kind) => set({ kind })} disabled={busy} />
            <p className="mt-1 text-xs text-muted">
              {form.kind === 'mcq'
                ? 'One question, four answers, one of them right.'
                : 'A one-sentence statement for the rapid-fire round. Players call it Fact or Fake.'}
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted">
            {form.kind === 'mcq' ? 'Multiple choice' : 'Fact or Fake'}
            {question?.origin === 'generated' && ' · written by the AI. Changing the wording or the answers marks it as edited.'}
          </p>
        )}

        <Field
          label={form.kind === 'mcq' ? 'Question' : 'Statement'}
          hint={`${form.text.trim().length} of ${TEXT_MAX} characters.${form.kind === 'fact_fake' ? ' Keep it to one sentence that reads in about three seconds.' : ''}`}
        >
          {(props) => (
            <textarea
              className="field"
              {...props}
              value={form.text}
              onChange={(event) => set({ text: event.target.value })}
              maxLength={TEXT_MAX}
              rows={3}
              required
              data-autofocus
            />
          )}
        </Field>

        {form.kind === 'mcq' ? (
          <fieldset>
            <legend className="label">Answers: type all four and mark the correct one</legend>
            <div className="grid gap-2">
              {form.options.map((option, index) => {
                const correct = form.correctIndex === index
                return (
                  <div key={index} className="flex items-center gap-2">
                    <label className="-ml-2 grid h-10 w-10 flex-none cursor-pointer place-items-center" title="Mark as the correct answer">
                      <input
                        type="radio"
                        name={radioName}
                        checked={correct}
                        onChange={() => set({ correctIndex: index })}
                        aria-label={`Answer ${LETTERS[index]} is the correct one`}
                      />
                    </label>
                    <div className="min-w-0 flex-1">
                      <input
                        className="field"
                        value={option}
                        onChange={(event) => setOption(index, event.target.value)}
                        maxLength={ANSWER_MAX}
                        aria-label={`Answer ${LETTERS[index]}${correct ? ', the correct answer' : ''}`}
                        placeholder={correct ? 'The correct answer' : 'A wrong answer'}
                        style={
                          correct
                            ? { borderColor: 'var(--good)', background: 'color-mix(in oklab, var(--good) 7%, var(--surface))' }
                            : undefined
                        }
                      />
                    </div>
                    <span className="hidden w-14 flex-none text-xs font-bold text-good sm:block">{correct ? 'Correct' : ''}</span>
                  </div>
                )
              })}
            </div>
            <p className="mt-1 text-xs text-muted">The order does not matter: answers are shuffled every time the question is asked.</p>

            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
              <button type="button" className="btn btn-ghost btn-sm" onClick={suggest} disabled={!canSuggest || busy || !meta.aiConfigured}>
                {suggesting ? 'Thinking…' : 'Suggest wrong answers'}
              </button>
              <span className="text-xs text-muted">
                {!meta.aiConfigured
                  ? 'Not available: the AI service is not set up on this server.'
                  : canSuggest
                    ? 'The AI fills in three believable wrong answers, a title and an explanation, and checks the question against the report.'
                    : 'Write the question and the correct answer first.'}
              </span>
            </div>
            {suggestError && (
              <div className="mt-2">
                <ErrorNote>{suggestError}</ErrorNote>
              </div>
            )}
            {verdict && !suggestError && (
              <div className="mt-2 grid gap-2" role="status">
                {verdict.concern && (
                  <Note tone="warn" role="alert">
                    <strong className="block">Check this question before saving</strong>
                    {verdict.concern}
                  </Note>
                )}
                <Note tone={verdict.concern ? 'neutral' : 'good'}>
                  {verdict.wrongAnswers.length >= 3
                    ? 'Three wrong answers were filled in'
                    : `Only ${verdict.wrongAnswers.length} wrong answer${verdict.wrongAnswers.length === 1 ? ' was' : 's were'} suggested`}
                  , along with a title and an explanation where those were empty. Read them through before you save.
                </Note>
              </div>
            )}
          </fieldset>
        ) : (
          <div>
            <span className="label">Is the statement true?</span>
            <ChoiceGroup
              label="Is the statement true?"
              className="grid gap-2 sm:grid-cols-2"
              options={VERDICTS}
              value={form.isTrue === null ? '' : form.isTrue ? 'fact' : 'fake'}
              onChange={(verdict) => set({ isTrue: verdict === 'fact' })}
              disabled={busy}
            />
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Section of the report" hint="A player clears a section by getting one of its questions right, which is what makes them “ready”.">
            {(props) => (
              <select className="field" {...props} value={form.sectionId} onChange={(event) => set({ sectionId: event.target.value })}>
                <option value="">No particular section</option>
                {detail.sections.map((section) => (
                  <option key={section.id} value={section.id}>
                    {section.heading}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div>
            <span className="label">Difficulty</span>
            <Segmented label="Difficulty" options={DIFFICULTIES} value={form.difficulty} onChange={(difficulty) => set({ difficulty })} disabled={busy} />
            <p className="mt-1 text-xs text-muted">Games start with the easier questions.</p>
          </div>
        </div>

        {adding && form.kind === 'mcq' && images.length > 0 && (
          <fieldset>
            <legend className="label">Picture from the report (optional)</legend>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="ad-thumb h-[72px] w-24" aria-pressed={form.imageId === ''} onClick={() => set({ imageId: '' })}>
                <span className="grid h-full place-items-center text-xs text-muted">No picture</span>
              </button>
              {images.map((image) => (
                <button
                  key={image.id}
                  type="button"
                  className="ad-thumb h-[72px] w-24"
                  aria-pressed={form.imageId === image.id}
                  aria-label={image.label}
                  title={image.label}
                  onClick={() => set({ imageId: image.id })}
                >
                  <img src={image.url} alt="" loading="lazy" />
                </button>
              ))}
            </div>
            {picture && (
              <p className="mt-2 text-xs text-muted">
                <span className="font-semibold text-ink">{picture.label}</span> {PICTURE_NOTE[detail.rules.picture_mode]}
              </p>
            )}
          </fieldset>
        )}

        {!adding && question?.imageUrl && (
          <div className="flex items-center gap-3">
            <img src={question.imageUrl} alt="" className="h-16 w-24 flex-none rounded-lg border border-line object-cover" />
            <p className="text-xs text-muted">This question is asked with this picture. The picture cannot be changed here.</p>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Title (optional)" hint="A short, playful heading shown above the question. It must not give the answer away.">
            {(props) => <input className="field" {...props} value={form.title} onChange={(event) => set({ title: event.target.value })} maxLength={TITLE_MAX} />}
          </Field>
          <Field label="Explanation (optional)" hint="One sentence stating the fact, shown once the question has been answered.">
            {(props) => (
              <textarea
                className="field"
                {...props}
                value={form.explanation}
                onChange={(event) => set({ explanation: event.target.value })}
                maxLength={EXPLANATION_MAX}
                rows={2}
              />
            )}
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="YouTube clip link (optional)"
            hint="Shown on the answer reveal card. Add #external if the uploader disables embeds."
          >
            {(props) => (
              <input
                className="field"
                type="url"
                placeholder="https://www.youtube.com/watch?v=..."
                {...props}
                value={form.videoUrl}
                onChange={(event) => set({ videoUrl: event.target.value })}
                maxLength={300}
              />
            )}
          </Field>
          <Field label="Clip title (optional)" hint="Short label on the replay card, e.g. 'Elway Helicopter Dive (SB XXXII)'.">
            {(props) => (
              <input
                className="field"
                {...props}
                value={form.videoTitle}
                onChange={(event) => set({ videoTitle: event.target.value })}
                maxLength={120}
              />
            )}
          </Field>
        </div>

        {adding && (
          <div className="grid gap-x-6 sm:grid-cols-2">
            <Switch
              checked={form.pinned}
              onChange={(pinned) => set({ pinned })}
              label="Pin as a must-ask"
              description="Pinned questions are asked before the others."
              disabled={busy}
            />
            <Switch
              checked={form.live}
              onChange={(live) => set({ live })}
              label="Put it live straight away"
              description="Turn off to save it as waiting for review."
              disabled={busy}
            />
          </div>
        )}

        <ErrorNote>{error}</ErrorNote>

        {confirmingClose ? (
          <Note tone="warn" role="alert">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="font-semibold">Close without saving what you have typed?</span>
              <span className="flex flex-wrap gap-2">
                <button ref={keepEditing} type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmingClose(false)}>
                  Keep editing
                </button>
                <button type="button" className="btn btn-danger btn-sm" onClick={onClose}>
                  Discard
                </button>
              </span>
            </div>
          </Note>
        ) : (
          <DialogActions>
            <button type="button" className="btn btn-ghost" onClick={requestClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {saving ? 'Saving…' : adding ? 'Add question' : 'Save changes'}
            </button>
          </DialogActions>
        )}
      </form>
    </Dialog>
  )
}
