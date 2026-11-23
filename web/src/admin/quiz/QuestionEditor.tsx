import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { ChoiceGroup, Dialog, DialogActions, Field, Note, Segmented, Switch } from '../components'
import { plural } from '../format'
import type { Difficulty, PictureMode, Question, QuestionKind, QuestionPatch, QuestionStatus, QuestionWrite, QuizImage, Suggestion } from '../types'
import { useQuiz } from './context'
import { LETTERS } from './questions'
import { YouTubePicker } from './YouTubePicker'

const TEXT_MIN = 8
const TEXT_MAX = 300
const ANSWER_MAX = 110
const TITLE_MAX = 60
const EXPLANATION_MAX = 500

type VisualMode = 'none' | 'question' | 'reveal'

type Form = {
  kind: QuestionKind
  text: string
  options: string[]
  correctIndex: number
  isTrue: boolean | null
  sectionId: string
  imageId: string
  revealImageId: string
  difficulty: Difficulty
  pinned: boolean
  status: QuestionStatus
  title: string
  explanation: string
  videoUrl: string
  videoTitle: string
  clearFlags: boolean
}

function initialForm(question: Question | null, duplicateOf?: Question | null): Form {
  const source = question ?? duplicateOf ?? null
  if (!source) {
    return {
      kind: 'mcq',
      text: '',
      options: ['', '', '', ''],
      correctIndex: 0,
      isTrue: null,
      sectionId: '',
      imageId: '',
      revealImageId: '',
      difficulty: 'medium',
      pinned: false,
      status: 'approved',
      title: '',
      explanation: '',
      videoUrl: '',
      videoTitle: '',
      clearFlags: false,
    }
  }
  const options = [...(source.options ?? [])]
  while (options.length < 4) options.push('')
  return {
    kind: source.kind,
    text: source.text,
    options: options.slice(0, 4),
    correctIndex: source.correctIndex ?? 0,
    isTrue: source.isTrue,
    sectionId: source.sectionId ?? '',
    imageId: source.imageId ?? '',
    revealImageId: source.revealImageId ?? '',
    difficulty: source.difficulty,
    pinned: question ? source.pinned : false,
    status: question ? source.status : 'approved',
    title: source.title ?? '',
    explanation: source.explanation ?? '',
    videoUrl: source.videoUrl ?? '',
    videoTitle: source.videoTitle ?? '',
    clearFlags: Boolean(question && question.flags > 0),
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
  if (form.pinned !== question.pinned) patch.pinned = form.pinned
  if (form.status !== question.status) patch.status = form.status
  if ((form.sectionId || null) !== question.sectionId) patch.sectionId = form.sectionId || null
  if (question.kind === 'mcq') {
    const lostPictureConvertedToText = question.sourceType === 'image' && !question.imageId && !form.imageId
    if ((form.imageId || null) !== question.imageId || (lostPictureConvertedToText && form.status === 'approved')) {
      patch.imageId = form.imageId || null
    }
    const nextReveal = form.imageId ? null : form.revealImageId || null
    if (nextReveal !== (question.revealImageId ?? null)) patch.revealImageId = nextReveal
  }
  if (question.flags > 0 && form.clearFlags && patch.status !== 'approved') {
    patch.clearFlags = true
  }
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

const VISUAL_MODES: { value: VisualMode; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'question', label: 'Ask about this picture' },
  { value: 'reveal', label: 'Show on answer reveal' },
]

const STATUS_EDIT: { value: QuestionStatus; label: string }[] = [
  { value: 'approved', label: 'Live' },
  { value: 'pending', label: 'Pending' },
  { value: 'rejected', label: 'Rejected' },
]

const STATUS_ADD: { value: QuestionStatus; label: string }[] = [
  { value: 'approved', label: 'Live straight away' },
  { value: 'pending', label: 'Waiting for review' },
]

const VERDICTS = [
  { value: 'fact', label: 'Fact', description: 'The statement is true according to the report.' },
  { value: 'fake', label: 'Fake', description: 'One detail has been changed, so it is false.' },
] as const

/** Add a question by hand, edit an existing one, or duplicate one into a fresh manual question. */
export default function QuestionEditor({
  question,
  duplicateOf = null,
  images,
  onClose,
  onSaved,
}: {
  question: Question | null
  duplicateOf?: Question | null
  images: QuizImage[]
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const { detail, meta } = useQuiz()
  const quizId = detail.quiz.id
  const [start] = useState(() => initialForm(question, duplicateOf))
  const [form, setForm] = useState<Form>(start)
  const [visualMode, setVisualMode] = useState<VisualMode>(() =>
    start.imageId ? 'question' : start.revealImageId ? 'reveal' : 'none',
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [suggested, setSuggested] = useState<Suggestion | null>(null)
  const [suggestedFor, setSuggestedFor] = useState<string | null>(null)
  const [suggestError, setSuggestError] = useState<string | null>(null)
  const [confirmingClose, setConfirmingClose] = useState(false)
  const keepEditing = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!confirmingClose) return
    keepEditing.current?.scrollIntoView({ block: 'nearest' })
    keepEditing.current?.focus()
  }, [confirmingClose])
  const radioName = useId()

  const adding = question === null
  const busy = saving || suggesting
  const dirty = JSON.stringify(form) !== JSON.stringify(start)
  const selectedVisualId = visualMode === 'question' ? form.imageId : visualMode === 'reveal' ? form.revealImageId : ''
  const selectedVisual = images.find((image) => image.id === selectedVisualId) ?? null
  const correctAnswer = form.options[form.correctIndex]?.trim() ?? ''
  const canSuggest = form.text.trim().length >= TEXT_MIN && correctAnswer.length > 0
  const asked = `${form.text.trim()}\n${correctAnswer}`
  const verdict = suggested && suggestedFor === asked ? suggested : null

  const { sectionImages, otherImages } = useMemo(() => {
    if (!form.sectionId) return { sectionImages: [] as QuizImage[], otherImages: images }
    const matching = images.filter((img) => img.sectionId === form.sectionId)
    const rest = images.filter((img) => img.sectionId !== form.sectionId)
    return { sectionImages: matching, otherImages: rest }
  }, [images, form.sectionId])

  const set = (patch: Partial<Form>) => {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  const setOption = (index: number, value: string) => set({ options: form.options.map((option, at) => (at === index ? value : option)) })

  const changeVisualMode = (nextMode: VisualMode) => {
    setVisualMode(nextMode)
    const activeId = form.imageId || form.revealImageId || sectionImages[0]?.id || ''
    if (nextMode === 'none') {
      set({ imageId: '', revealImageId: '' })
    } else if (nextMode === 'question') {
      set({ imageId: activeId, revealImageId: '' })
    } else {
      set({ imageId: '', revealImageId: activeId })
    }
  }

  const pickVisual = (id: string) => {
    if (visualMode === 'question') set({ imageId: id, revealImageId: '' })
    else if (visualMode === 'reveal') set({ imageId: '', revealImageId: id })
  }

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
        onSaved(changed ? (patch.clearFlags ? 'Question updated and player flags cleared.' : 'Question updated.') : 'Nothing was changed.')
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
          imageId: mcq ? form.imageId || null : null,
          revealImageId: mcq && !form.imageId ? form.revealImageId || null : null,
          pinned: form.pinned,
          status: form.status,
        }
        await call<{ id: string }>(`/quizzes/${quizId}/questions`, { method: 'POST', body })
        onSaved(form.status === 'approved' ? 'Question added. It is live.' : 'Question added. It is waiting for review.')
      }
    } catch (reason) {
      setError(errorText(reason))
      setSaving(false)
    }
  }

  return (
    <Dialog open onClose={requestClose} title={adding ? (duplicateOf ? 'Duplicate question' : 'Add a question') : 'Edit question'} wide>
      <form className="grid gap-5" onSubmit={submit}>
        {question && question.flags > 0 && (
          <Note tone="bad">
            <strong className="block">{plural(question.flags, 'player')} flagged this question</strong>
            {question.flagReasons.length > 0 ? (
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {question.flagReasons.map((reason, idx) => (
                  <li key={idx} className="[overflow-wrap:anywhere]">
                    {reason}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1">No specific reason was written.</p>
            )}
            <div className="mt-2 border-t border-line/60 pt-2">
              <Switch
                checked={form.clearFlags}
                onChange={(clearFlags) => set({ clearFlags })}
                label={`Clear player flag${question.flags === 1 ? '' : 's'} on save`}
                disabled={busy}
              />
            </div>
          </Note>
        )}

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
                    <label className="-ml-2 grid h-11 w-11 flex-none cursor-pointer place-items-center" title="Mark as the correct answer">
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
          <Field label="Section of the report" hint="A player clears a section by hitting ≥75% across their last 4 answers in it (including at least 1 multiple-choice question).">
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

        {form.kind === 'mcq' && images.length > 0 && (
          <fieldset className="rounded-lg border border-line bg-surface-2/40 p-3.5">
            <legend className="label">Visual from the report (optional)</legend>
            <Segmented label="Visual placement" options={VISUAL_MODES} value={visualMode} onChange={changeVisualMode} disabled={busy} />
            {visualMode !== 'none' && (
              <div className="mt-3 grid gap-3">
                {sectionImages.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-semibold text-muted">From this section ({sectionImages.length})</p>
                    <div className="flex flex-wrap gap-2">
                      {sectionImages.map((image) => (
                        <button
                          key={image.id}
                          type="button"
                          className="ad-thumb h-[68px] w-24"
                          aria-pressed={selectedVisualId === image.id}
                          aria-label={image.label}
                          title={image.label}
                          onClick={() => pickVisual(image.id)}
                        >
                          <img src={image.url} alt="" loading="lazy" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {otherImages.length > 0 && (
                  <div>
                    {sectionImages.length > 0 && (
                      <p className="mb-1.5 text-xs font-semibold text-muted">Other report images ({otherImages.length})</p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {otherImages.map((image) => (
                        <button
                          key={image.id}
                          type="button"
                          className="ad-thumb h-[68px] w-24"
                          aria-pressed={selectedVisualId === image.id}
                          aria-label={image.label}
                          title={image.label}
                          onClick={() => pickVisual(image.id)}
                        >
                          <img src={image.url} alt="" loading="lazy" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {selectedVisual ? (
                  <p className="text-xs text-muted">
                    <span className="font-semibold text-ink">{selectedVisual.label}</span> —{' '}
                    {visualMode === 'question'
                      ? PICTURE_NOTE[detail.rules.picture_mode]
                      : 'Revealed alongside the explanation after players answer.'}
                  </p>
                ) : (
                  <p className="text-xs text-muted">Click a thumbnail above to attach it.</p>
                )}
              </div>
            )}
          </fieldset>
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

        <YouTubePicker
          value={form.videoUrl}
          title={form.videoTitle}
          disabled={busy}
          onChange={({ videoUrl, videoTitle }) => set({ videoUrl, videoTitle })}
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <span className="label">Status</span>
            <Segmented
              label="Question status"
              options={adding ? STATUS_ADD : STATUS_EDIT}
              value={form.status}
              onChange={(status) => set({ status })}
              disabled={busy}
            />
            <p className="mt-1 text-xs text-muted">
              {form.status === 'approved'
                ? 'Live questions can be asked in games (approving also clears player flags).'
                : form.status === 'pending'
                  ? 'Held in review — players will not see it until approved.'
                  : 'Rejected — kept for reference but never asked.'}
            </p>
          </div>
          <Switch
            checked={form.pinned}
            onChange={(pinned) => set({ pinned })}
            label="Pin as a must-ask"
            description="Pinned questions are asked before the others."
            disabled={busy}
          />
        </div>

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
