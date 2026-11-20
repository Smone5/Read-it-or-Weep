// Wording and filtering for the question bank.

import type { Question, QuestionPatch, QuestionStatus } from '../types'

/** How a question is asked. A picture question is a multiple-choice question with a picture. */
export type QuestionType = 'mcq' | 'fact_fake' | 'picture'

export const questionType = (question: Question): QuestionType =>
  question.kind === 'fact_fake' ? 'fact_fake' : question.imageId ? 'picture' : 'mcq'

export const TYPE_LABEL: Record<QuestionType, string> = {
  mcq: 'Multiple choice',
  fact_fake: 'Fact or Fake',
  picture: 'Picture',
}

export function originLabel(question: Question): string {
  if (question.origin === 'manual') return 'By hand'
  return question.edited ? 'AI · edited' : 'AI'
}

export const SKILL_LABEL: Record<Question['skill'], string> = { recall: 'Recall', understanding: 'Understanding' }

export const LETTERS = ['A', 'B', 'C', 'D'] as const

export type Filters = {
  status: 'all' | QuestionStatus
  author: 'all' | 'ai' | 'manual'
  type: 'all' | QuestionType
  /** A section id, `none` for questions that are not filed under a section, or `all`. */
  section: string
  search: string
  flaggedOnly: boolean
}

export const NO_FILTERS: Filters = { status: 'all', author: 'all', type: 'all', section: 'all', search: '', flaggedOnly: false }

export const isFiltered = (filters: Filters) =>
  filters.status !== 'all' ||
  filters.author !== 'all' ||
  filters.type !== 'all' ||
  filters.section !== 'all' ||
  filters.search.trim() !== '' ||
  filters.flaggedOnly

export function matches(question: Question, filters: Filters): boolean {
  if (filters.status !== 'all' && question.status !== filters.status) return false
  if (filters.author === 'ai' && question.origin !== 'generated') return false
  if (filters.author === 'manual' && question.origin !== 'manual') return false
  if (filters.type !== 'all' && questionType(question) !== filters.type) return false
  if (filters.section === 'none' && question.sectionId !== null) return false
  if (filters.section !== 'all' && filters.section !== 'none' && question.sectionId !== filters.section) return false
  if (filters.flaggedOnly && question.flags === 0) return false
  const needle = filters.search.trim().toLowerCase()
  if (needle) {
    const haystack = [question.title, question.text, ...(question.options ?? []), question.explanation, question.section]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    if (!haystack.includes(needle)) return false
  }
  return true
}

/** What a question looks like after the server has accepted `patch` (approving also clears its flags). */
export function withPatch(question: Question, patch: QuestionPatch): Question {
  const next = { ...question, ...patch }
  if (patch.status === 'approved') {
    next.flags = 0
    next.pulledReason = null
  }
  return next
}

/**
 * Picture links are signed afresh on every request. Keeping the first link we were given
 * (until shortly before it expires) stops every thumbnail reloading after each refresh.
 */
export function stableImageUrls() {
  const known = new Map<string, string>()
  const stillGood = (url: string) => {
    const expires = Number(new URLSearchParams(url.split('?')[1] ?? '').get('exp'))
    return Number.isFinite(expires) && expires * 1000 - Date.now() > 10 * 60_000
  }
  return (imageId: string, url: string): string => {
    const earlier = known.get(imageId)
    if (earlier && stillGood(earlier)) return earlier
    known.set(imageId, url)
    return url
  }
}
