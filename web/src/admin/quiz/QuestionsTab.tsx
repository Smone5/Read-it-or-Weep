import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { call, errorText } from '../api'
import { Bar, ConfirmDialog, Dialog, EmptyState, ErrorBlock, Field, LoadingBlock, Note, Segmented } from '../components'
import { useNotify } from '../context'
import { isWorking, plural } from '../format'
import { useLoad } from '../hooks'
import { paths } from '../paths'
import type { BulkAction, DeepPartial, Difficulty, GameRules, Question, QuestionBank, QuestionPatch, QuestionSources } from '../types'
import { useDocumentWatch, useQuiz } from './context'
import QuestionCard, { type QuestionActions } from './QuestionCard'
import QuestionEditor from './QuestionEditor'
import { isFiltered, matches, NO_FILTERS, sortQuestions, stableImageUrls, TYPE_LABEL, withPatch, type Filters, type QuestionSort, type QuestionType } from './questions'

const PAGE = 30
const BULK_LIMIT = 500 // the server accepts at most this many ids per request
const NONE: Question[] = []

const BULK_DONE: Record<Exclude<BulkAction, 'delete'>, string> = {
  approve: 'approved',
  reject: 'rejected',
  pin: 'pinned',
  unpin: 'unpinned',
  set_difficulty: 'updated',
  set_section: 'moved',
}

const SOURCE_OPTIONS: { value: QuestionSources; label: string }[] = [
  { value: 'all', label: 'All live questions' },
  { value: 'manual', label: 'Only hand-written' },
  { value: 'ai', label: 'Only AI-written' },
]

const APPROVAL_OPTIONS = [
  { value: 'auto', label: 'Go live automatically' },
  { value: 'hold', label: 'Hold for my approval' },
] as const

/** Links from elsewhere (Overview tiles, Report sections) can open the bank already filtered. */
function filtersFromLink(params: URLSearchParams): Filters {
  const status = params.get('status')
  const section = params.get('section')
  return {
    ...NO_FILTERS,
    status: status === 'approved' || status === 'pending' || status === 'rejected' ? status : 'all',
    section: section || 'all',
    flaggedOnly: params.get('flagged') === '1',
    hasVideoOnly: params.get('video') === '1',
  }
}

export default function QuestionsTab() {
  const { detail, meta, refresh, save } = useQuiz()
  const notify = useNotify()
  const { quiz, rules, sections, documents } = detail
  const bank = useLoad<QuestionBank>(`/quizzes/${quiz.id}/questions`)
  const [params, setParams] = useSearchParams()
  const [filters, setFilters] = useState<Filters>(() => filtersFromLink(params))
  const [sort, setSort] = useState<QuestionSort>('default')
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set())
  const [shown, setShown] = useState(PAGE)
  const [editing, setEditing] = useState<Question | 'new' | null>(null)
  const openedEditRef = useRef<string | null>(null)
  const [previewing, setPreviewing] = useState<Question | null>(null)
  const [deleting, setDeleting] = useState<Question | 'selected' | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [settingBusy, setSettingBusy] = useState(false)
  const [askingGenerate, setAskingGenerate] = useState(false)
  const [generateBusy, setGenerateBusy] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  // Set while a "write more" job we started is under way. `seenWorking` turns true once the server
  // reports it running; `before` is what the documents' progress looked like when we asked.
  const [generation, setGeneration] = useState<{ seenWorking: boolean; before: string } | null>(null)
  const [stableUrl] = useState(stableImageUrls)

  const watch = useDocumentWatch(generation !== null && !generation.seenWorking)
  const wasWorking = useRef(watch.working)
  // Timers and effects below fire later than the render that created them; this is always current.
  const latestDocuments = useRef(documents)
  useEffect(() => {
    latestDocuments.current = documents
  })

  const data = useMemo(() => {
    if (!bank.data) return null
    return {
      questions: bank.data.questions.map((question) => {
        const nextImage = question.imageId && question.imageUrl ? stableUrl(question.imageId, question.imageUrl) : question.imageUrl
        const nextReveal =
          question.revealImageId && question.revealImageUrl
            ? stableUrl(question.revealImageId, question.revealImageUrl)
            : question.revealImageUrl
        if (nextImage === question.imageUrl && nextReveal === question.revealImageUrl) return question
        return { ...question, imageUrl: nextImage, revealImageUrl: nextReveal }
      }),
      images: bank.data.images.map((image) => ({ ...image, url: stableUrl(image.id, image.url) })),
    }
  }, [bank.data, stableUrl])

  const all = data?.questions ?? NONE
  const editParam = params.get('edit')
  useEffect(() => {
    if (!editParam || !data || openedEditRef.current === editParam) return
    const target = data.questions.find((q) => q.id === editParam)
    if (target) {
      openedEditRef.current = editParam
      setEditing(target)
      const next = new URLSearchParams(params)
      next.delete('edit')
      setParams(next, { replace: true })
    }
  }, [editParam, data, params, setParams])
  const filtered = useMemo(() => sortQuestions(all.filter((question) => matches(question, filters)), sort), [all, filters, sort])
  const visible = filtered.slice(0, shown)
  const chosen = filtered.filter((question) => selected.has(question.id))
  const allChosen = filtered.length > 0 && chosen.length === filtered.length

  const counts = useMemo(() => {
    const perSection = new Map<string, number>()
    let approved = 0
    let pending = 0
    let rejected = 0
    let flagged = 0
    let withVideo = 0
    let pinned = 0
    let withRevealVisual = 0
    let unfiled = 0
    for (const question of all) {
      if (question.status === 'approved') approved += 1
      else if (question.status === 'pending') pending += 1
      else rejected += 1
      if (question.flags > 0) flagged += 1
      if (question.videoUrl) withVideo += 1
      if (question.pinned) pinned += 1
      if (question.revealImageId) withRevealVisual += 1
      if (question.sectionId) perSection.set(question.sectionId, (perSection.get(question.sectionId) ?? 0) + 1)
      else unfiled += 1
    }
    return { approved, pending, rejected, flagged, withVideo, pinned, withRevealVisual, unfiled, perSection }
  }, [all])

  // How many questions a game can actually draw on, given the "players are asked" setting.
  const playable = all.filter(
    (question) =>
      question.status === 'approved' &&
      question.kind === 'mcq' &&
      (rules.question_sources === 'all' || (rules.question_sources === 'manual' ? question.origin === 'manual' : question.origin === 'generated')),
  ).length

  const reloadAll = () => Promise.all([bank.refresh(), refresh()]).then(() => undefined)

  const progressOf = (docs: typeof documents) => JSON.stringify(docs.map((doc) => [doc.id, doc.progress]))

  /** Called when the server stops working on this quiz's documents: new questions may have arrived. */
  const settle = (sawItRun: boolean) => {
    reloadAll().catch(() => {})
    if (!generation) return
    setGeneration(null)
    const docs = latestDocuments.current
    // A quick job can start and finish between two checks. It still leaves its result on the documents.
    if (!sawItRun && progressOf(docs) === generation.before) {
      notify('The list is up to date.')
      return
    }
    const failure = docs.map((doc) => doc.progress.label ?? '').find((label) => label.startsWith('Could not write more questions'))
    if (failure) {
      notify(`${failure}. Try again in a moment.`, 'bad')
      return
    }
    const added = docs.reduce((sum, doc) => sum + (doc.progress.added ?? 0), 0)
    if (added === 0) notify('No new questions this time: nothing new passed the quality checks.')
    else notify(`${plural(added, 'new question')} written${rules.auto_approve ? '' : '. They are waiting for your review'}.`)
  }

  useEffect(() => {
    const finished = wasWorking.current && !watch.working
    wasWorking.current = watch.working
    if (watch.working && generation && !generation.seenWorking) setGeneration({ ...generation, seenWorking: true })
    if (finished) settle(true)
    // Only a change in whether the server is working matters here; `settle` reads the latest values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch.working])

  // A small job can finish (or fail to start) before we ever see it running. Do not wait for ever.
  useEffect(() => {
    if (!generation || generation.seenWorking) return
    const timer = window.setTimeout(() => settle(false), 20_000)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation])

  // A job that has gone quiet for five minutes has probably died with the server. Stop waiting
  // for it, so "Write more with AI" can be pressed again.
  useEffect(() => {
    if (watch.stalled) setGeneration(null)
  }, [watch.stalled])

  const changeFilters = (patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }))
    setSelected(new Set()) // a selection is always something you can see
    setShown(PAGE)
  }

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((current) => {
      const next = new Set(current)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })

  const patchQuestion = async (question: Question, patch: QuestionPatch, done: string) => {
    markBusy(question.id, true)
    try {
      await call(`/questions/${question.id}`, { method: 'PATCH', body: patch })
      bank.mutate((current) => ({
        ...current,
        questions: current.questions.map((other) => (other.id === question.id ? withPatch(other, patch) : other)),
      }))
      notify(done)
      refresh().catch(() => {}) // the counts in the page header
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      markBusy(question.id, false)
    }
  }

  const runBulk = async (action: BulkAction, ids: string[], extra?: { difficulty?: Difficulty; sectionId?: string | null }) => {
    let changed = 0
    for (let start = 0; start < ids.length; start += BULK_LIMIT) {
      const result = await call<{ changed: number }>(`/quizzes/${quiz.id}/questions/bulk`, {
        method: 'POST',
        body: { ids: ids.slice(start, start + BULK_LIMIT), action, ...(extra ?? {}) },
      })
      changed += result.changed
    }
    return changed
  }

  const bulk = async (action: Exclude<BulkAction, 'delete'>, extra?: { difficulty?: Difficulty; sectionId?: string | null }) => {
    if (chosen.length === 0 || bulkBusy) return
    setBulkBusy(true)
    try {
      const changed = await runBulk(action, chosen.map((question) => question.id), extra)
      setSelected(new Set())
      await reloadAll().catch(() => {})
      notify(`${plural(changed, 'question')} ${BULK_DONE[action]}.`)
    } catch (reason) {
      notify(errorText(reason), 'bad')
      reloadAll().catch(() => {})
    } finally {
      setBulkBusy(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      if (deleting === 'selected') {
        const changed = await runBulk('delete', chosen.map((question) => question.id))
        notify(`${plural(changed, 'question')} deleted.`)
        setSelected(new Set())
      } else {
        await call(`/questions/${deleting.id}`, { method: 'DELETE' })
        notify('Question deleted.')
        setSelected((current) => new Set([...current].filter((id) => id !== deleting.id)))
      }
      setDeleting(null)
      await reloadAll().catch(() => {})
    } catch (reason) {
      setDeleteError(errorText(reason))
    } finally {
      setDeleteBusy(false)
    }
  }

  const refreshNow = async () => {
    setRefreshing(true)
    try {
      await reloadAll()
      notify('The list is up to date.')
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setRefreshing(false)
    }
  }

  const saveSetting = async (settings: DeepPartial<GameRules>, done: string) => {
    setSettingBusy(true)
    try {
      await save({ settings })
      notify(done)
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setSettingBusy(false)
    }
  }

  const startGeneration = async () => {
    setGenerateBusy(true)
    setGenerateError(null)
    try {
      const before = progressOf(documents)
      await call(`/quizzes/${quiz.id}/questions/generate`, { method: 'POST' })
      setAskingGenerate(false)
      setGeneration({ seenWorking: false, before })
    } catch (reason) {
      setGenerateError(errorText(reason))
    } finally {
      setGenerateBusy(false)
    }
  }

  const actions: QuestionActions = {
    onSelect: (question, on) =>
      setSelected((current) => {
        const next = new Set(current)
        if (on) next.add(question.id)
        else next.delete(question.id)
        return next
      }),
    onStatus: (question, status) =>
      void patchQuestion(question, { status }, status === 'approved' ? 'Approved. It is live.' : 'Rejected. It will not be asked.'),
    onPin: (question, pinned) => void patchQuestion(question, { pinned }, pinned ? 'Pinned. It will be asked before the others.' : 'Unpinned.'),
    onEdit: setEditing,
    onDelete: (question) => {
      setDeleteError(null)
      setDeleting(question)
    },
    onPreview: setPreviewing,
  }

  const hasReadyReport = documents.some((doc) => doc.status === 'ready')
  const reading = documents.some((doc) => doc.status !== 'ready' && isWorking(doc))
  const activeDoc = documents.find(isWorking)
  const busyWithAi = (watch.working && !watch.stalled) || generation !== null
  const cannotGenerate = !meta.aiConfigured
    ? 'The AI service is not set up on this server.'
    : !hasReadyReport
      ? 'Upload a report first.'
      : busyWithAi
        ? 'Questions are being written right now.'
        : null

  const addButton = (
    <button type="button" className="btn btn-primary" onClick={() => setEditing('new')}>
      Add a question
    </button>
  )

  const typeOptions = Object.entries(TYPE_LABEL) as [QuestionType, string][]

  return (
    // A flex column rather than a grid, so the bar of bulk actions (position: sticky) can follow the screen.
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Question bank</h2>
          <p className="text-sm text-muted">
            {data ? `${counts.approved} live · ${counts.pending} waiting for review · ${counts.rejected} rejected` : 'Every question players can be asked.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {addButton}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setGenerateError(null)
              setAskingGenerate(true)
            }}
            disabled={cannotGenerate !== null}
            title={cannotGenerate ?? 'The AI writes questions about facts the bank does not cover yet'}
          >
            Write more with AI
          </button>
          <button type="button" className="btn btn-ghost" onClick={refreshNow} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {busyWithAi && (
        <Note tone="info" role="status">
          <p>
            <strong>{reading ? 'The report is still being read.' : 'Writing more questions.'}</strong> This takes a minute or two. New questions
            appear here by themselves when it has finished, and you can carry on working meanwhile.
          </p>
          {activeDoc && (activeDoc.progress.total ?? 0) > 1 && (
            <div className="mt-2">
              <Bar label={activeDoc.progress.label ?? 'Progress'} value={activeDoc.progress.done ?? 0} max={activeDoc.progress.total ?? 1} />
            </div>
          )}
        </Note>
      )}

      {watch.stalled && (
        <Note tone="warn" role="status">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>Nothing has changed for ten minutes, so the writing has probably stopped. You can check again, or start it again with “Write more with AI”.</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={watch.resume}>
              Check again
            </button>
          </div>
        </Note>
      )}

      <section className="card grid gap-x-6 gap-y-4 p-3 sm:p-4 md:grid-cols-2" aria-label="How questions are used">
        <div>
          <span className="label">When the AI writes questions, they</span>
          <Segmented
            label="When the AI writes questions"
            options={APPROVAL_OPTIONS}
            value={rules.auto_approve ? 'auto' : 'hold'}
            disabled={settingBusy}
            onChange={(choice) => {
              const auto = choice === 'auto'
              if (auto === rules.auto_approve) return
              void saveSetting(
                { auto_approve: auto },
                auto ? 'New AI questions will go live automatically.' : 'New AI questions will wait for your approval.',
              )
            }}
          />
          <p className="mt-1 text-xs text-muted">Applies to questions written from now on, not to the ones already here.</p>
        </div>
        <div>
          <span className="label">Players are asked</span>
          <Segmented
            label="Players are asked"
            options={SOURCE_OPTIONS}
            value={rules.question_sources}
            disabled={settingBusy}
            onChange={(sources) => {
              if (sources === rules.question_sources) return
              void saveSetting({ question_sources: sources }, 'Saved.')
            }}
          />
          <p className="mt-1 text-xs text-muted">Only live questions are ever asked. Pinned questions come first.</p>
        </div>
        {data && all.length > 0 && playable < 3 && (
          <div className="md:col-span-2">
            <Note tone="warn" role="status">
              <strong>Players cannot start a game yet.</strong> A game needs at least three live multiple-choice questions
              {rules.question_sources === 'all' ? '' : rules.question_sources === 'manual' ? ' written by hand' : ' written by the AI'}, and there{' '}
              {playable === 1 ? 'is' : 'are'} {playable}. Approve or add more{rules.question_sources === 'all' ? '' : ', or let players be asked all live questions'}.
            </Note>
          </div>
        )}
      </section>

      {!data && bank.loading && <LoadingBlock label="Loading the questions" />}
      {!data && bank.error && <ErrorBlock message={bank.error} onRetry={bank.reload} />}

      {data && all.length === 0 && (
        <EmptyState
          title="No questions yet"
          action={
            <>
              {addButton}
              <Link className="btn btn-ghost" to={paths.quiz(quiz.id, 'report')}>
                Go to Report
              </Link>
            </>
          }
        >
          {reading
            ? 'The report is being read. Questions will appear here in a minute or two.'
            : 'Upload a report and the questions are written for you, or write your own.'}
        </EmptyState>
      )}

      {data && all.length > 0 && (
        <>
          <section className="card grid gap-3 p-3 sm:p-4" aria-label="Filter the questions">
            <Segmented
              label="Status"
              value={filters.status}
              onChange={(status) => changeFilters({ status })}
              options={[
                { value: 'all', label: <>All {all.length}</> },
                { value: 'approved', label: <>Live {counts.approved}</> },
                { value: 'pending', label: <>Waiting for review {counts.pending}</> },
                { value: 'rejected', label: <>Rejected {counts.rejected}</> },
              ]}
            />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              <Field label="Search" className="sm:col-span-2 lg:col-span-2">
                {(props) => (
                  <input
                    className="field"
                    type="search"
                    {...props}
                    value={filters.search}
                    onChange={(event) => changeFilters({ search: event.target.value })}
                    placeholder="Words in the question or its answers"
                  />
                )}
              </Field>
              <Field label="Written by">
                {(props) => (
                  <select className="field" {...props} value={filters.author} onChange={(event) => changeFilters({ author: event.target.value as Filters['author'] })}>
                    <option value="all">Anyone</option>
                    <option value="ai">The AI</option>
                    <option value="manual">By hand</option>
                  </select>
                )}
              </Field>
              <Field label="Type">
                {(props) => (
                  <select className="field" {...props} value={filters.type} onChange={(event) => changeFilters({ type: event.target.value as Filters['type'] })}>
                    <option value="all">All types</option>
                    {typeOptions.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label="Difficulty">
                {(props) => (
                  <select className="field" {...props} value={filters.difficulty} onChange={(event) => changeFilters({ difficulty: event.target.value as Filters['difficulty'] })}>
                    <option value="all">All levels</option>
                    <option value="easy">Easy</option>
                    <option value="medium">Medium</option>
                    <option value="hard">Hard</option>
                  </select>
                )}
              </Field>
              <Field label="Sort">
                {(props) => (
                  <select className="field" {...props} value={sort} onChange={(event) => { setSort(event.target.value as QuestionSort); setShown(PAGE) }}>
                    <option value="default">Default order</option>
                    <option value="hardest">Hardest first</option>
                    <option value="easiest">Easiest first</option>
                    <option value="most_asked">Most asked</option>
                    <option value="most_flagged">Most flagged</option>
                  </select>
                )}
              </Field>
              <Field label="Section" className="sm:col-span-2 lg:col-span-3">
                {(props) => (
                  <select className="field" {...props} value={filters.section} onChange={(event) => changeFilters({ section: event.target.value })}>
                    <option value="all">All sections</option>
                    {sections.map((section) => (
                      <option key={section.id} value={section.id}>
                        {section.heading} ({counts.perSection.get(section.id) ?? 0})
                      </option>
                    ))}
                    {counts.unfiled > 0 && <option value="none">No section ({counts.unfiled})</option>}
                  </select>
                )}
              </Field>
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <label className="ad-check text-sm">
                <input type="checkbox" checked={filters.flaggedOnly} onChange={(event) => changeFilters({ flaggedOnly: event.target.checked })} />
                <span>
                  Flagged only <span className="text-muted">({counts.flagged})</span>
                </span>
              </label>
              <label className="ad-check text-sm">
                <input type="checkbox" checked={filters.pinnedOnly} onChange={(event) => changeFilters({ pinnedOnly: event.target.checked })} />
                <span>
                  Pinned only <span className="text-muted">({counts.pinned})</span>
                </span>
              </label>
              <label className="ad-check text-sm">
                <input type="checkbox" checked={filters.hasVideoOnly} onChange={(event) => changeFilters({ hasVideoOnly: event.target.checked })} />
                <span>
                  Has YouTube clip <span className="text-muted">({counts.withVideo})</span>
                </span>
              </label>
              <label className="ad-check text-sm">
                <input type="checkbox" checked={filters.hasRevealVisualOnly} onChange={(event) => changeFilters({ hasRevealVisualOnly: event.target.checked })} />
                <span>
                  Has reveal visual <span className="text-muted">({counts.withRevealVisual})</span>
                </span>
              </label>
            </div>
          </section>

          <div className="flex flex-wrap items-center justify-between gap-x-4">
            <label className="ad-check text-sm">
              <input
                type="checkbox"
                checked={allChosen}
                disabled={filtered.length === 0}
                onChange={(event) => setSelected(event.target.checked ? new Set(filtered.map((question) => question.id)) : new Set())}
              />
              <span>
                Select all {filtered.length}
                {isFiltered(filters) ? ' that match' : ''}
              </span>
            </label>
            <p className="py-2 text-sm text-muted" role="status">
              Showing {filtered.length} of {plural(all.length, 'question')}
              {isFiltered(filters) && (
                <>
                  {' · '}
                  <button type="button" className="ad-link" onClick={() => changeFilters(NO_FILTERS)}>
                    Clear filters
                  </button>
                </>
              )}
            </p>
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              title="No questions match"
              action={
                <button type="button" className="btn btn-ghost" onClick={() => changeFilters(NO_FILTERS)}>
                  Clear filters
                </button>
              }
            >
              Try a different status or fewer filters.
            </EmptyState>
          ) : (
            <ul className="grid gap-3">
              {visible.map((question) => (
                <li key={question.id}>
                  <QuestionCard question={question} selected={selected.has(question.id)} busy={busyIds.has(question.id) || bulkBusy} actions={actions} />
                </li>
              ))}
            </ul>
          )}

          {filtered.length > shown && (
            <div className="flex justify-center">
              <button type="button" className="btn btn-ghost" onClick={() => setShown((count) => count + PAGE)}>
                Show {Math.min(PAGE, filtered.length - shown)} more ({filtered.length - shown} not shown)
              </button>
            </div>
          )}
        </>
      )}

      {chosen.length > 0 && (
        <div className="ad-sticky-bar" role="region" aria-label="Actions for the selected questions">
          <strong className="mr-auto text-sm">{chosen.length} selected</strong>
          <button type="button" className="btn btn-primary btn-sm" disabled={bulkBusy} onClick={() => void bulk('approve')}>
            Approve
          </button>
          <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('reject')}>
            Reject
          </button>
          <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('pin')}>
            Pin
          </button>
          <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('unpin')}>
            Unpin
          </button>
          <select
            className="field !h-8 !w-auto !py-0 text-xs"
            aria-label="Set difficulty for selected questions"
            value=""
            disabled={bulkBusy}
            onChange={(event) => {
              const value = event.target.value as Difficulty | ''
              if (value) void bulk('set_difficulty', { difficulty: value })
            }}
          >
            <option value="">Set difficulty…</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
          {sections.length > 0 && (
            <select
              className="field !h-8 !w-auto max-w-[190px] !py-0 text-xs"
              aria-label="Move selected questions to section"
              value=""
              disabled={bulkBusy}
              onChange={(event) => {
                const value = event.target.value
                if (!value) return
                void bulk('set_section', { sectionId: value === '__none__' ? null : value })
              }}
            >
              <option value="">Move to section…</option>
              <option value="__none__">No section</option>
              {sections.map((section) => (
                <option key={section.id} value={section.id}>
                  {section.heading}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="btn btn-danger btn-sm"
            disabled={bulkBusy}
            onClick={() => {
              setDeleteError(null)
              setDeleting('selected')
            }}
          >
            Delete
          </button>
          <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => setSelected(new Set())}>
            Clear selection
          </button>
        </div>
      )}

      {editing && (
        <QuestionEditor
          question={editing === 'new' ? null : editing}
          images={data?.images ?? []}
          onClose={() => setEditing(null)}
          onSaved={(message) => {
            setEditing(null)
            notify(message)
            reloadAll().catch(() => {})
          }}
        />
      )}

      <ConfirmDialog
        open={deleting !== null}
        title={deleting === 'selected' ? `Delete ${plural(chosen.length, 'question')}?` : 'Delete this question?'}
        confirmLabel={deleting === 'selected' ? `Delete ${plural(chosen.length, 'question')}` : 'Delete question'}
        busyLabel="Deleting…"
        busy={deleteBusy}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      >
        {deleting && deleting !== 'selected' && <p className="font-semibold">“{deleting.text}”</p>}
        <p>
          {deleting === 'selected' ? 'They are' : 'It is'} removed for good, along with how often {deleting === 'selected' ? 'they were' : 'it was'}{' '}
          answered correctly. To stop a question being asked but keep it, reject it instead.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={askingGenerate}
        title="Write more questions with AI?"
        confirmLabel="Write more questions"
        busyLabel="Starting…"
        danger={false}
        busy={generateBusy}
        error={generateError}
        onConfirm={startGeneration}
        onCancel={() => setAskingGenerate(false)}
      >
        <p>The AI reads the report again and writes questions about facts the bank does not cover yet. Existing questions are left alone.</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>It takes a minute or two and runs in the background.</li>
          <li>It uses the AI service, which has a small cost.</li>
          <li>{rules.auto_approve ? 'The new questions go live straight away.' : 'The new questions wait here for your approval.'}</li>
        </ul>
      </ConfirmDialog>

      <Dialog
        open={previewing !== null}
        onClose={() => setPreviewing(null)}
        title={previewing ? (previewing.imageUrl ? (previewing.section ?? 'Question picture') : `Answer reveal visual · ${previewing.section ?? 'Report'}`) : 'Picture'}
        wide
      >
        {(previewing?.imageUrl || previewing?.revealImageUrl) && (
          <>
            <img
              src={previewing.imageUrl ?? previewing.revealImageUrl ?? ''}
              alt={previewing.imageUrl ? `The picture shown with the question: ${previewing.text}` : `The exhibit shown after answering: ${previewing.text}`}
              className="mx-auto max-h-[70dvh] w-auto max-w-full rounded-lg"
            />
            <p className="mt-3 text-sm font-semibold">{previewing.text}</p>
          </>
        )}
      </Dialog>
    </div>
  )
}
