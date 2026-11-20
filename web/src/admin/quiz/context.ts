import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { call } from '../api'
import { isWorking } from '../format'
import { useInterval } from '../hooks'
import type { LiveCreated, Meta, QuizDetailData, QuizDocument, QuizPatch, QuizSaved } from '../types'

/** What every tab of one quiz shares. Provided by QuizDetail. */
export type QuizContextValue = {
  detail: QuizDetailData
  meta: Meta
  /** Fetch the quiz again quietly: its counts, sections and documents. */
  refresh: () => Promise<void>
  /** Save changes to the quiz. The page picks up the saved quiz and the rules that result. */
  save: (changes: QuizPatch) => Promise<QuizSaved>
  setDocuments: (documents: QuizDocument[]) => void
  /**
   * The live game started from this page, if any. Kept in memory only, and only while this
   * quiz stays open, because its big-screen link carries the key that controls the game.
   */
  liveGame: LiveCreated | null
  setLiveGame: (game: LiveCreated | null) => void
}

export const QuizContext = createContext<QuizContextValue | null>(null)

export function useQuiz(): QuizContextValue {
  const value = useContext(QuizContext)
  if (!value) throw new Error('useQuiz must be used inside a quiz page')
  return value
}

const POLL_MS = 2000
const STALL_MS = 5 * 60_000

/**
 * Keeps the quiz's documents up to date while the server is working on one of them, by
 * asking every two seconds, and only then. `expectWork` keeps it asking for a moment after
 * a job has been requested but before the server reports it has started.
 *
 * If nothing at all changes for five minutes the job has probably died (for example the
 * server was restarted), so it stops asking and reports `stalled`.
 */
export function useDocumentWatch(expectWork = false): { working: boolean; stalled: boolean; resume: () => void } {
  const { detail, setDocuments } = useQuiz()
  const quizId = detail.quiz.id
  const working = detail.documents.some(isWorking)
  const signature = JSON.stringify(detail.documents.map((doc) => [doc.id, doc.status, doc.stage, doc.progress]))
  const lastChange = useRef(Date.now())
  const asking = useRef(false)
  const [stalled, setStalled] = useState(false)

  useEffect(() => {
    lastChange.current = Date.now()
    setStalled(false)
  }, [signature])

  const resume = useCallback(() => {
    lastChange.current = Date.now()
    setStalled(false)
  }, [])

  useInterval(
    () => {
      if (document.hidden) {
        lastChange.current = Date.now() // time spent in a background tab does not count as a stall
        return
      }
      if (asking.current) return
      if (working && Date.now() - lastChange.current > STALL_MS) {
        setStalled(true)
        return
      }
      asking.current = true
      call<{ documents: QuizDocument[] }>(`/quizzes/${quizId}/documents`)
        .then(
          (result) => setDocuments(result.documents),
          () => {}, // a failed check is simply tried again on the next tick
        )
        .finally(() => {
          asking.current = false
        })
    },
    (working || expectWork) && !stalled ? POLL_MS : null,
  )

  // Whether a document counts as "working" also depends on the clock, so it can stop being so
  // without the server saying anything; then there is nothing left to call stalled.
  return { working, stalled: stalled && working, resume }
}
