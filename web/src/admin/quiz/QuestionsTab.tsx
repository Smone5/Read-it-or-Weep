import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { call, errorText } from '../api'
import { Badge, Bar, ConfirmDialog, Dialog, DialogActions, EmptyState, ErrorBlock, Field, LoadingBlock, Note, Segmented } from '../components'
import { useNotify } from '../context'
import { isWorking, plural } from '../format'
import { useLoad } from '../hooks'
import { paths } from '../paths'
import type { BulkAction, DeepPartial, Difficulty, GameRules, Question, QuestionBank, QuestionPatch, QuestionSources } from '../types'
import { useDocumentWatch, useQuiz } from './context'
import QuestionCard, { type QuestionActions } from './QuestionCard'
import QuestionEditor from './QuestionEditor'
import {
  countSecondaryFilters,
  isFiltered,
  LETTERS,
  matches,
  NO_FILTERS,
  sortQuestions,
  stableImageUrls,
  TYPE_LABEL,
  withPatch,
  type AuthorFilter,
  type Filters,
  type QuestionSort,
  type QuestionType,
} from './questions'

const PAGE = 30
const BULK_LIMIT = 500
const NONE: Question[] = []

const BULK_DONE: Record<Exclude<BulkAction, 'delete'>, string> = {
  approve: 'approved',
  reject: 'rejected',
  pending: 'moved to pending review',
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

function filtersFromLink(params: URLSearchParams): Filters {
  const status = params.get('status')
  const author = params.get('author')
  const type = params.get('type')
  const difficulty = params.get('difficulty')
  const section = params.get('section')
  return {
    ...NO_FILTERS,
    status: status === 'approved' || status === 'pending' || status === 'rejected' ? status : 'all',
    author:
      author === 'ai' || author === 'ai_unedited' || author === 'ai_edited' || author === 'manual' ? author : 'all',
    type: type === 'mcq' || type === 'fact_fake' || type === 'picture' ? type : 'all',
    difficulty: difficulty === 'easy' || difficulty === 'medium' || difficulty === 'hard' ? difficulty : 'all',
    section: section || 'all',
    search: params.get('q') ?? '',
    flaggedOnly: params.get('flagged') === '1',
    pinnedOnly: params.get('pinned') === '1',
    hasVideoOnly: params.get('video') === '1',
    hasRevealVisualOnly: params.get('reveal') === '1',
  }
}

function sortFromLink(params: URLSearchParams): QuestionSort {
  const s = params.get('sort')
  return s === 'hardest' || s === 'easiest' || s === 'most_asked' || s === 'most_flagged' ? s : 'default'
}

function toSearchParams(filters: Filters, sort: QuestionSort): URLSearchParams {
  const p = new URLSearchParams()
  if (filters.status !== 'all') p.set('status', filters.status)
  if (filters.author !== 'all') p.set('author', filters.author)
  if (filters.type !== 'all') p.set('type', filters.type)
  if (filters.difficulty !== 'all') p.set('difficulty', filters.difficulty)
  if (filters.section !== 'all') p.set('section', filters.section)
  if (filters.search.trim()) p.set('q', filters.search.trim())
  if (filters.flaggedOnly) p.set('flagged', '1')
  if (filters.pinnedOnly) p.set('pinned', '1')
  if (filters.hasVideoOnly) p.set('video', '1')
  if (filters.hasRevealVisualOnly) p.set('reveal', '1')
  if (sort !== 'default') p.set('sort', sort)
  return p
}

export default function QuestionsTab() {
  const { detail, meta, refresh, save } = useQuiz()
  const notify = useNotify()
  const { quiz, rules, sections, documents } = detail
  const bank = useLoad<QuestionBank>(`/quizzes/${quiz.id}/questions`)
  const [params, setParams] = useSearchParams()
  const [filters, setFilters] = useState<Filters>(() => filtersFromLink(params))
  const [sort, setSort] = useState<QuestionSort>(() => sortFromLink(params))
  const [filtersOpen, setFiltersOpen] = useState(() => countSecondaryFilters(filtersFromLink(params)) > 0)
  const [expandExplanations, setExpandExplanations] = useState(false)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const lastClickedId = useRef<string | null>(null)
  const selectAllRef = useRef<HTMLInputElement>(null)
  const [focusedIndex, setFocusedIndex] = useState<number>(-1)
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set())
  const [shown, setShown] = useState(PAGE)
  const [editing, setEditing] = useState<Question | 'new' | null>(null)
  const [duplicating, setDuplicating] = useState<Question | null>(null)
  const openedEditRef = useRef<string | null>(null)
  const [previewing, setPreviewing] = useState<Question | null>(null)
  const [playerPreview, setPlayerPreview] = useState<Question | null>(null)
  const [deleting, setDeleting] = useState<Question | 'selected' | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMoreOpen, setBulkMoreOpen] = useState(false)
  const [bulkDifficulty, setBulkDifficulty] = useState<Difficulty | ''>('')
  const [bulkSection, setBulkSection] = useState<string>('')
  const [refreshing, setRefreshing] = useState(false)
  const [settingBusy, setSettingBusy] = useState(false)
  const [askingGenerate, setAskingGenerate] = useState(false)
  const [generateBusy, setGenerateBusy] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [generation, setGeneration] = useState<{ seenWorking: boolean; before: string } | null>(null)
  const [stableUrl] = useState(stableImageUrls)

  const watch = useDocumentWatch(generation !== null && !generation.seenWorking)
  const wasWorking = useRef(watch.working)
  const latestDocuments = useRef(documents)
  useEffect(() => {
    latestDocuments.current = documents
  })

  // Keep toast lifted slightly above sticky bulk bar when items are selected.
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.admin')
    if (!root) return
    root.style.setProperty('--ad-toast-offset', selected.size > 0 ? '68px' : '0px')
    return () => {
      root.style.setProperty('--ad-toast-offset', '0px')
    }
  }, [selected.size])

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

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = chosen.length > 0 && !allChosen
    }
  }, [chosen.length, allChosen])

  const counts = useMemo(() => {
    const perSection = new Map<string, number>()
    const liveMcqBySection = new Map<string, number>()
    let approved = 0
    let pending = 0
    let rejected = 0
    let flagged = 0
    let withVideo = 0
    let pinned = 0
    let withRevealVisual = 0
    let unfiled = 0
    let easyMcq = 0
    let mediumMcq = 0
    let hardMcq = 0
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
      if (question.status === 'approved' && question.kind === 'mcq') {
        if (question.difficulty === 'easy') easyMcq += 1
        else if (question.difficulty === 'medium') mediumMcq += 1
        else hardMcq += 1
        if (question.sectionId) liveMcqBySection.set(question.sectionId, (liveMcqBySection.get(question.sectionId) ?? 0) + 1)
      }
    }
    const thinSections = sections.filter((s) => (liveMcqBySection.get(s.id) ?? 0) < 2)
    return {
      approved,
      pending,
      rejected,
      flagged,
      withVideo,
      pinned,
      withRevealVisual,
      unfiled,
      perSection,
      liveMcqBySection,
      easyMcq,
      mediumMcq,
      hardMcq,
      thinSections,
    }
  }, [all, sections])

  const playable = all.filter(
    (question) =>
      question.status === 'approved' &&
      question.kind === 'mcq' &&
      (rules.question_sources === 'all' || (rules.question_sources === 'manual' ? question.origin === 'manual' : question.origin === 'generated')),
  ).length

  const reloadAll = () => Promise.all([bank.refresh(), refresh()]).then(() => undefined)
  const progressOf = (docs: typeof documents) => JSON.stringify(docs.map((doc) => [doc.id, doc.progress]))

  const settle = (sawItRun: boolean) => {
    reloadAll().catch(() => {})
    if (!generation) return
    setGeneration(null)
    const docs = latestDocuments.current
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch.working])

  useEffect(() => {
    if (!generation || generation.seenWorking) return
    const timer = window.setTimeout(() => settle(false), 20_000)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation])

  useEffect(() => {
    if (watch.stalled) setGeneration(null)
  }, [watch.stalled])

  const syncUrl = (nextFilters: Filters, nextSort: QuestionSort) => {
    setParams(toSearchParams(nextFilters, nextSort), { replace: true })
  }

  const changeFilters = (patch: Partial<Filters>) => {
    setFilters((current) => {
      const next = { ...current, ...patch }
      syncUrl(next, sort)
      return next
    })
    setSelected(new Set())
    lastClickedId.current = null
    setFocusedIndex(-1)
    setShown(PAGE)
  }

  const changeSort = (nextSort: QuestionSort) => {
    setSort(nextSort)
    syncUrl(filters, nextSort)
    setShown(PAGE)
  }

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((current) => {
      const next = new Set(current)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })

  const patchQuestion = async (
    question: Question,
    patch: QuestionPatch,
    done: string,
    undo?: { patch: QuestionPatch; message: string },
  ) => {
    markBusy(question.id, true)
    try {
      await call(`/questions/${question.id}`, { method: 'PATCH', body: patch })
      bank.mutate((current) => ({
        ...current,
        questions: current.questions.map((other) => (other.id === question.id ? withPatch(other, patch) : other)),
      }))
      notify(
        done,
        'good',
        undo
          ? {
              actionLabel: 'Undo',
              onAction: () => void patchQuestion(question, undo.patch, undo.message),
            }
          : undefined,
      )
      refresh().catch(() => {})
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      markBusy(question.id, false)
    }
  }

  const runBulk = async (action: BulkAction, ids: string[], extra?: { difficulty?: Difficulty; sectionId?: string | null }) => {
    let changed = 0
    let skipped = 0
    for (let start = 0; start < ids.length; start += BULK_LIMIT) {
      const result = await call<{ changed: number; skipped?: number }>(`/quizzes/${quiz.id}/questions/bulk`, {
        method: 'POST',
        body: { ids: ids.slice(start, start + BULK_LIMIT), action, ...(extra ?? {}) },
      })
      changed += result.changed
      skipped += result.skipped ?? 0
    }
    return { changed, skipped }
  }

  const bulk = async (action: Exclude<BulkAction, 'delete'>, extra?: { difficulty?: Difficulty; sectionId?: string | null }) => {
    if (chosen.length === 0 || bulkBusy) return
    setBulkBusy(true)
    try {
      const { changed, skipped } = await runBulk(action, chosen.map((question) => question.id), extra)
      setSelected(new Set())
      setBulkDifficulty('')
      setBulkSection('')
      setBulkMoreOpen(false)
      await reloadAll().catch(() => {})
      if (skipped > 0) {
        notify(
          `${plural(changed, 'question')} approved. ${plural(skipped, 'lost-picture question')} skipped — add a picture or switch to text-only first.`,
          changed === 0 ? 'bad' : 'good',
        )
      } else {
        notify(`${plural(changed, 'question')} ${BULK_DONE[action]}.`)
      }
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
        const { changed } = await runBulk('delete', chosen.map((question) => question.id))
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
    onSelect: (question, on, shiftKey) => {
      if (shiftKey && lastClickedId.current) {
        const startIdx = visible.findIndex((q) => q.id === lastClickedId.current)
        const endIdx = visible.findIndex((q) => q.id === question.id)
        if (startIdx >= 0 && endIdx >= 0) {
          const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx]
          setSelected((current) => {
            const next = new Set(current)
            for (let i = lo; i <= hi; i += 1) {
              if (on) next.add(visible[i].id)
              else next.delete(visible[i].id)
            }
            return next
          })
          lastClickedId.current = question.id
          return
        }
      }
      lastClickedId.current = question.id
      setSelected((current) => {
        const next = new Set(current)
        if (on) next.add(question.id)
        else next.delete(question.id)
        return next
      })
    },
    onStatus: (question, status) => {
      const prev = question.status
      void patchQuestion(
        question,
        { status },
        status === 'approved' ? 'Approved. It is live.' : 'Rejected. It will not be asked.',
        status === 'rejected' ? { patch: { status: prev }, message: 'Restored question.' } : undefined,
      )
    },
    onDismissFlags: (question) =>
      void patchQuestion(question, { clearFlags: true }, `Cleared player flag${question.flags === 1 ? '' : 's'}.`),
    onPin: (question, pinned) => void patchQuestion(question, { pinned }, pinned ? 'Pinned. It will be asked before the others.' : 'Unpinned.'),
    onEdit: (question) => {
      setDuplicating(null)
      setEditing(question)
    },
    onDuplicate: (question) => {
      setDuplicating(question)
      setEditing('new')
    },
    onDelete: (question) => {
      setDeleteError(null)
      setDeleting(question)
    },
    onPreview: setPreviewing,
    onPlayerPreview: setPlayerPreview,
  }

  // Keyboard triage shortcuts (J/K move focus, A approve, R reject, P pin, E edit) when not in an input or modal.
  const modalOpen = Boolean(editing || deleting || askingGenerate || previewing || playerPreview)
  useEffect(() => {
    if (modalOpen || visible.length === 0) return
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return
      const key = event.key.toLowerCase()
      if (key === 'j') {
        event.preventDefault()
        setFocusedIndex((i) => Math.min(visible.length - 1, i + 1))
      } else if (key === 'k') {
        event.preventDefault()
        setFocusedIndex((i) => Math.max(0, i <= 0 ? 0 : i - 1))
      } else if (focusedIndex >= 0 && focusedIndex < visible.length) {
        const q = visible[focusedIndex]
        if (busyIds.has(q.id) || bulkBusy) return
        if (key === 'a' && q.status !== 'approved' && !(q.sourceType === 'image' && !q.imageId)) {
          event.preventDefault()
          actions.onStatus(q, 'approved')
        } else if (key === 'r' && q.status !== 'rejected') {
          event.preventDefault()
          actions.onStatus(q, 'rejected')
        } else if (key === 'p') {
          event.preventDefault()
          actions.onPin(q, !q.pinned)
        } else if (key === 'e') {
          event.preventDefault()
          actions.onEdit(q)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

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
    <button
      type="button"
      className="btn btn-primary"
      onClick={() => {
        setDuplicating(null)
        setEditing('new')
      }}
    >
      Add a question
    </button>
  )

  const typeOptions = Object.entries(TYPE_LABEL) as [QuestionType, string][]
  const secondaryFilterCount = countSecondaryFilters(filters)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Question bank</h2>
          <p className="text-sm text-muted">
            {data
              ? `${counts.approved} live · ${counts.pending} pending · ${counts.rejected} rejected${counts.flagged ? ` · ${counts.flagged} flagged` : ''}`
              : 'Every question players can be asked.'}
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

      {data && all.length > 0 && playable < 3 && (
        <Note tone="warn" role="status">
          <strong>Players cannot start a game yet.</strong> A game needs at least three live multiple-choice questions
          {rules.question_sources === 'all' ? '' : rules.question_sources === 'manual' ? ' written by hand' : ' written by the AI'}, and there{' '}
          {playable === 1 ? 'is' : 'are'} {playable}. Approve or add more{rules.question_sources === 'all' ? '' : ', or let players be asked all live questions'}.
        </Note>
      )}

      <details className="card p-3 sm:px-4">
        <summary className="cursor-pointer py-0.5 text-sm font-semibold">
          Bank settings{' '}
          <span className="ml-1 font-normal text-muted">
            ({rules.auto_approve ? 'AI auto-approves' : 'AI held for review'} ·{' '}
            {SOURCE_OPTIONS.find((o) => o.value === rules.question_sources)?.label ?? rules.question_sources})
          </span>
        </summary>
        <div className="mt-3 grid gap-x-6 gap-y-4 border-t border-line pt-3 md:grid-cols-2">
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
        </div>
      </details>

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
          <details className="card p-3 sm:px-4">
            <summary className="cursor-pointer py-0.5 text-sm font-semibold">
              Coverage &amp; difficulty mix{' '}
              <span className="ml-1 font-normal text-muted">
                (Live MCQs: {counts.easyMcq} easy · {counts.mediumMcq} medium · {counts.hardMcq} hard
                {counts.thinSections.length > 0 ? ` · ${plural(counts.thinSections.length, 'thin section')}` : ''})
              </span>
            </summary>
            <div className="mt-3 grid gap-3 border-t border-line pt-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="good">Easy: {counts.easyMcq}</Badge>
                <Badge tone="info">Medium: {counts.mediumMcq}</Badge>
                <Badge tone="brand">Hard: {counts.hardMcq}</Badge>
              </div>
              {counts.thinSections.length > 0 ? (
                <div>
                  <p className="text-xs font-semibold text-muted">Sections with fewer than 2 live multiple-choice questions:</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {counts.thinSections.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          changeFilters({ section: s.id, status: 'all' })
                          setFiltersOpen(true)
                        }}
                      >
                        {s.heading} ({counts.liveMcqBySection.get(s.id) ?? 0} live)
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                sections.length > 0 && <p className="text-xs text-muted">Every report section has at least 2 live multiple-choice questions.</p>
              )}
            </div>
          </details>

          <section className="card grid gap-3 p-3 sm:p-4" aria-label="Filter the questions">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Segmented
                label="Status"
                value={filters.status}
                onChange={(status) => changeFilters({ status })}
                options={[
                  { value: 'all', label: <>All {all.length}</> },
                  { value: 'approved', label: <>Live {counts.approved}</> },
                  { value: 'pending', label: <>Pending {counts.pending}</> },
                  { value: 'rejected', label: <>Rejected {counts.rejected}</> },
                ]}
              />
              <div className="flex flex-1 flex-wrap items-center justify-end gap-2 sm:flex-initial">
                <input
                  className="field min-w-[180px] flex-1 sm:w-56 sm:flex-initial"
                  type="search"
                  aria-label="Search questions"
                  value={filters.search}
                  onChange={(event) => changeFilters({ search: event.target.value })}
                  placeholder="Search questions or answers…"
                />
                <select
                  className="field w-auto"
                  aria-label="Sort questions"
                  value={sort}
                  onChange={(event) => changeSort(event.target.value as QuestionSort)}
                >
                  <option value="default">Sort: Default</option>
                  <option value="hardest">Hardest first</option>
                  <option value="easiest">Easiest first</option>
                  <option value="most_asked">Most asked</option>
                  <option value="most_flagged">Most flagged</option>
                </select>
                <button
                  type="button"
                  className={`btn btn-sm ${secondaryFilterCount > 0 ? 'btn-primary' : 'btn-ghost'}`}
                  aria-expanded={filtersOpen}
                  onClick={() => setFiltersOpen((o) => !o)}
                >
                  Filters{secondaryFilterCount > 0 ? ` (${secondaryFilterCount})` : ''}
                </button>
              </div>
            </div>

            {filtersOpen && (
              <div className="grid gap-3 border-t border-line pt-3">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <Field label="Written by">
                    {(props) => (
                      <select
                        className="field"
                        {...props}
                        value={filters.author}
                        onChange={(event) => changeFilters({ author: event.target.value as AuthorFilter })}
                      >
                        <option value="all">Anyone</option>
                        <option value="ai">All AI</option>
                        <option value="ai_unedited">AI · unedited</option>
                        <option value="ai_edited">AI · edited</option>
                        <option value="manual">By hand</option>
                      </select>
                    )}
                  </Field>
                  <Field label="Type">
                    {(props) => (
                      <select
                        className="field"
                        {...props}
                        value={filters.type}
                        onChange={(event) => changeFilters({ type: event.target.value as Filters['type'] })}
                      >
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
                      <select
                        className="field"
                        {...props}
                        value={filters.difficulty}
                        onChange={(event) => changeFilters({ difficulty: event.target.value as Filters['difficulty'] })}
                      >
                        <option value="all">All levels</option>
                        <option value="easy">Easy</option>
                        <option value="medium">Medium</option>
                        <option value="hard">Hard</option>
                      </select>
                    )}
                  </Field>
                  <Field label="Section">
                    {(props) => (
                      <select
                        className="field"
                        {...props}
                        value={filters.section}
                        onChange={(event) => changeFilters({ section: event.target.value })}
                      >
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
                <div className="flex flex-wrap gap-x-6 gap-y-1">
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
              </div>
            )}
          </section>

          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <div className="flex flex-wrap items-center gap-x-4">
              <label className="ad-check text-sm">
                <input
                  ref={selectAllRef}
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
              <button
                type="button"
                className="ad-link text-sm"
                onClick={() => setExpandExplanations((v) => !v)}
              >
                {expandExplanations ? 'Collapse explanations' : 'Expand all explanations'}
              </button>
            </div>
            <p className="py-1 text-sm text-muted" role="status">
              Showing {filtered.length} of {plural(all.length, 'question')}
              {isFiltered(filters) && (
                <>
                  {' · '}
                  <button type="button" className="ad-link" onClick={() => changeFilters(NO_FILTERS)}>
                    Clear filters
                  </button>
                </>
              )}
              {(filters.status === 'pending' || filters.flaggedOnly) && (
                <span className="ml-2 hidden text-xs md:inline">· Shortcuts: J/K move · A approve · R reject · P pin · E edit</span>
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
              {visible.map((question, index) => (
                <li key={question.id}>
                  <QuestionCard
                    question={question}
                    selected={selected.has(question.id)}
                    focused={index === focusedIndex}
                    expandExplanation={expandExplanations}
                    busy={busyIds.has(question.id) || bulkBusy}
                    actions={actions}
                  />
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
        <div className="ad-sticky-bar flex-col items-stretch" role="region" aria-label="Actions for the selected questions">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="mr-auto text-sm">{chosen.length} selected</strong>
            <button type="button" className="btn btn-primary btn-sm" disabled={bulkBusy} onClick={() => void bulk('approve')}>
              Approve
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('reject')}>
              Reject
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-expanded={bulkMoreOpen}
              disabled={bulkBusy}
              onClick={() => setBulkMoreOpen((v) => !v)}
            >
              {bulkMoreOpen ? 'Fewer actions' : 'More…'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => setSelected(new Set())}>
              Clear
            </button>
          </div>

          {bulkMoreOpen && (
            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2">
              <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('pending')}>
                Set pending
              </button>
              <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('pin')}>
                Pin
              </button>
              <button type="button" className="btn btn-ghost btn-sm" disabled={bulkBusy} onClick={() => void bulk('unpin')}>
                Unpin
              </button>

              <div className="flex items-center gap-1.5">
                <select
                  className="field w-auto text-xs"
                  aria-label="Choose difficulty for selected questions"
                  value={bulkDifficulty}
                  disabled={bulkBusy}
                  onChange={(event) => setBulkDifficulty(event.target.value as Difficulty | '')}
                >
                  <option value="">Difficulty…</option>
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                </select>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={bulkBusy || !bulkDifficulty}
                  onClick={() => bulkDifficulty && void bulk('set_difficulty', { difficulty: bulkDifficulty })}
                >
                  Apply
                </button>
              </div>

              {sections.length > 0 && (
                <div className="flex items-center gap-1.5">
                  <select
                    className="field w-auto max-w-[180px] text-xs"
                    aria-label="Choose section for selected questions"
                    value={bulkSection}
                    disabled={bulkBusy}
                    onChange={(event) => setBulkSection(event.target.value)}
                  >
                    <option value="">Move to section…</option>
                    <option value="__none__">No section</option>
                    {sections.map((section) => (
                      <option key={section.id} value={section.id}>
                        {section.heading}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={bulkBusy || !bulkSection}
                    onClick={() =>
                      bulkSection &&
                      void bulk('set_section', { sectionId: bulkSection === '__none__' ? null : bulkSection })
                    }
                  >
                    Move
                  </button>
                </div>
              )}

              <button
                type="button"
                className="btn btn-danger btn-sm ml-auto"
                disabled={bulkBusy}
                onClick={() => {
                  setDeleteError(null)
                  setDeleting('selected')
                }}
              >
                Delete ({chosen.length})
              </button>
            </div>
          )}
        </div>
      )}

      {editing && (
        <QuestionEditor
          question={editing === 'new' ? null : editing}
          duplicateOf={editing === 'new' ? duplicating : null}
          images={data?.images ?? []}
          onClose={() => {
            setEditing(null)
            setDuplicating(null)
          }}
          onSaved={(message) => {
            setEditing(null)
            setDuplicating(null)
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

      {playerPreview && <PlayerPreviewDialog question={playerPreview} onClose={() => setPlayerPreview(null)} />}
    </div>
  )
}

function PlayerPreviewDialog({ question, onClose }: { question: Question; onClose: () => void }) {
  const [revealed, setRevealed] = useState(false)
  const [picked, setPicked] = useState<number | boolean | null>(null)

  const choose = (val: number | boolean) => {
    setPicked(val)
    setRevealed(true)
  }

  return (
    <Dialog open onClose={onClose} title="Preview as player" wide>
      <div className="grid gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>{question.section ?? 'General'}</span>
          <Segmented
            label="Preview stage"
            value={revealed ? 'reveal' : 'question'}
            onChange={(stage) => setRevealed(stage === 'reveal')}
            options={[
              { value: 'question', label: '1. Question' },
              { value: 'reveal', label: '2. Answer reveal' },
            ]}
          />
        </div>

        <div className="rounded-xl border border-line bg-surface-2/40 p-4">
          {question.title && <p className="text-xs font-bold uppercase tracking-wide text-muted">{question.title}</p>}
          <p className="mt-1 text-base font-bold">{question.text}</p>
          {question.imageUrl && (
            <img
              src={question.imageUrl}
              alt=""
              className="mx-auto mt-3 max-h-60 rounded-lg border border-line object-contain"
            />
          )}
        </div>

        {question.kind === 'mcq' && question.options ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {question.options.map((opt, idx) => {
              const isCorrect = idx === question.correctIndex
              const isPicked = picked === idx
              return (
                <button
                  key={idx}
                  type="button"
                  className="ad-option text-left"
                  data-correct={revealed && isCorrect ? 'true' : undefined}
                  style={
                    revealed && isPicked && !isCorrect
                      ? { borderColor: 'var(--bad)', background: 'color-mix(in oklab, var(--bad) 8%, var(--surface))' }
                      : undefined
                  }
                  onClick={() => choose(idx)}
                >
                  <span className="ad-option-key">{LETTERS[idx] ?? idx + 1}</span>
                  <span className="min-w-0 flex-1">{opt}</span>
                  {revealed && isCorrect && <span className="text-xs font-bold text-good">Correct</span>}
                </button>
              )
            })}
          </div>
        ) : (
          <div className="grid max-w-sm grid-cols-2 gap-2">
            {[true, false].map((val) => {
              const isCorrect = question.isTrue === val
              return (
                <button
                  key={String(val)}
                  type="button"
                  className="ad-option justify-center font-bold"
                  data-correct={revealed && isCorrect ? 'true' : undefined}
                  onClick={() => choose(val)}
                >
                  {val ? 'FACT' : 'FAKE'}
                </button>
              )
            })}
          </div>
        )}

        {revealed && (
          <div className="grid gap-3 rounded-xl border border-line bg-surface-2 p-4 text-sm">
            {question.explanation && <p>{question.explanation}</p>}
            {question.sourceQuote && (
              <p className="text-xs text-muted">
                <strong>From the report:</strong> “{question.sourceQuote}”
              </p>
            )}
            {question.revealImageUrl && (
              <img
                src={question.revealImageUrl}
                alt="Reveal exhibit"
                className="mx-auto max-h-56 rounded-lg border border-line object-contain"
              />
            )}
            {question.videoUrl && (
              <p className="text-xs text-muted">
                Includes YouTube replay card:{' '}
                <a className="ad-link" href={question.videoUrl.replace(/#external$/i, '')} target="_blank" rel="noopener noreferrer">
                  {question.videoTitle || question.videoUrl}
                </a>
              </p>
            )}
          </div>
        )}

        <DialogActions>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        </DialogActions>
      </div>
    </Dialog>
  )
}
