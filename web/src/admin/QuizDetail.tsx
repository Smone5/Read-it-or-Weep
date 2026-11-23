import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { call } from './api'
import { Badge, ErrorBlock, LoadingBlock } from './components'
import { useDirtyGuard, usePageTitle } from './context'
import { formatDateTime, isWorking, meetingHint, QUIZ_STATUS } from './format'
import { useLoad, useMeta } from './hooks'
import { paths, tabFromPath, type QuizTab } from './paths'
import { QuizContext, type QuizContextValue } from './quiz/context'
import LiveTab from './quiz/LiveTab'
import OverviewTab from './quiz/OverviewTab'
import PlayStylesTab from './quiz/PlayStylesTab'
import QuestionsTab from './quiz/QuestionsTab'
import ReportTab from './quiz/ReportTab'
import ResultsTab from './quiz/ResultsTab'
import type { LiveCreated, QuizDetailData, QuizDocument, QuizPatch, QuizSaved } from './types'

const TABS: { id: QuizTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'report', label: 'Report' },
  { id: 'questions', label: 'Questions' },
  { id: 'play-styles', label: 'Play styles' },
  { id: 'results', label: 'Results' },
  { id: 'live', label: 'Live' },
]

export default function QuizDetail() {
  const { quizId = '' } = useParams()
  // Keyed by quiz so nothing (an unsaved form, a live game's key) carries over to another quiz.
  return <QuizScreen key={quizId} quizId={quizId} />
}

function QuizScreen({ quizId }: { quizId: string }) {
  const { data: detail, error, reload, refresh, mutate } = useLoad<QuizDetailData>(`/quizzes/${quizId}`)
  const { meta, error: metaError, retry: retryMeta } = useMeta()
  const [liveGame, setLiveGame] = useState<LiveCreated | null>(null)
  const { confirmLeave } = useDirtyGuard()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const current = tabFromPath(pathname, quizId)
  const currentLabel = TABS.find((tab) => tab.id === current)?.label ?? 'Overview'
  const tabsRef = useRef<HTMLElement>(null)

  usePageTitle(detail ? `${currentLabel} · ${detail.quiz.title}` : 'Quiz')

  useEffect(() => {
    const active = tabsRef.current?.querySelector<HTMLElement>('[aria-current="page"]')
    active?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  }, [current, detail?.quiz.id])

  const guardedClick = (to: string) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    if (to === pathname) return
    event.preventDefault()
    confirmLeave(() => navigate(to))
  }

  const save = useCallback(
    async (changes: QuizPatch) => {
      const saved = await call<QuizSaved>(`/quizzes/${quizId}`, { method: 'PATCH', body: changes })
      mutate((current) => ({ ...current, quiz: saved.quiz, rules: saved.rules }))
      return saved
    },
    [quizId, mutate],
  )

  const setDocuments = useCallback(
    (documents: QuizDocument[]) => {
      // Polling returns the same list most of the time; only a real change should redraw the page.
      mutate((current) =>
        JSON.stringify(current.documents) === JSON.stringify(documents)
          ? current
          : { ...current, documents, overview: { ...current.overview, documents: documents.length } },
      )
    },
    [mutate],
  )

  const value = useMemo<QuizContextValue | null>(
    () => (detail && meta ? { detail, meta, refresh, save, setDocuments, liveGame, setLiveGame } : null),
    [detail, meta, refresh, save, setDocuments, liveGame],
  )

  if (!value) {
    const back = (
      <p className="mb-3 text-sm">
        <Link to={paths.home} className="ad-link">
          ← All quizzes
        </Link>
      </p>
    )
    if (error) {
      return (
        <>
          {back}
          <ErrorBlock message={error} onRetry={reload} />
        </>
      )
    }
    if (metaError) {
      return (
        <>
          {back}
          <ErrorBlock message={metaError} onRetry={retryMeta} />
        </>
      )
    }
    return <LoadingBlock label="Loading the quiz" />
  }

  const { quiz, overview, documents } = value.detail
  const status = QUIZ_STATUS[quiz.status]
  const hint = meetingHint(quiz.meetingAt)

  const tabBadge = (tab: QuizTab) => {
    if (tab === 'questions' && overview.pending > 0) return <Badge tone="warn">{overview.pending} to review</Badge>
    if (tab === 'questions' && overview.flagged > 0) return <Badge tone="bad">{overview.flagged} flagged</Badge>
    if (tab === 'questions') return <span className="font-normal tabular-nums">{overview.approved}</span>
    if (tab === 'report' && documents.some(isWorking)) return <Badge tone="info">Working</Badge>
    if (tab === 'live' && liveGame) return <Badge tone="good">{liveGame.code}</Badge>
    if (tab === 'live' && (overview.active_live_sessions ?? 0) > 0) {
      return <Badge tone="good">{overview.active_live_sessions} active</Badge>
    }
    return null
  }

  return (
    <>
      <p className="text-sm">
        <Link to={paths.home} onClick={guardedClick(paths.home)} className="ad-link">
          ← All quizzes
        </Link>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="min-w-0 text-2xl font-bold">{quiz.title}</h1>
        <Badge tone={status.tone} title={status.explain}>
          {status.label}
        </Badge>
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

      <div className="ad-tabs-wrap mb-5 mt-4">
        <nav ref={tabsRef} className="ad-tabs" aria-label="Parts of this quiz">
          {TABS.map((tab) => {
            const to = paths.quiz(quizId, tab.id)
            return (
              <Link
                key={tab.id}
                to={to}
                onClick={guardedClick(to)}
                className="ad-tab"
                aria-current={current === tab.id ? 'page' : undefined}
              >
                {tab.label}
                {tabBadge(tab.id)}
              </Link>
            )
          })}
        </nav>
      </div>

      <QuizContext.Provider value={value}>
        <Routes>
          <Route index element={<OverviewTab />} />
          <Route path="report" element={<ReportTab />} />
          <Route path="questions" element={<QuestionsTab />} />
          <Route path="play-styles" element={<PlayStylesTab />} />
          <Route path="results" element={<ResultsTab />} />
          <Route path="live" element={<LiveTab />} />
          <Route path="*" element={<Navigate to={paths.quiz(quizId)} replace />} />
        </Routes>
      </QuizContext.Provider>
    </>
  )
}
