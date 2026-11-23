// Wording and filtering for the question bank.

import type { Difficulty, Question, QuestionPatch, QuestionStatus } from '../types'

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

export type QuestionSort = 'default' | 'hardest' | 'easiest' | 'most_asked' | 'most_flagged'

export type AuthorFilter = 'all' | 'ai' | 'ai_unedited' | 'ai_edited' | 'manual'

export type Filters = {
  status: 'all' | QuestionStatus
  author: AuthorFilter
  type: 'all' | QuestionType
  difficulty: 'all' | Difficulty
  /** A section id, `none` for questions that are not filed under a section, or `all`. */
  section: string
  search: string
  flaggedOnly: boolean
  hasVideoOnly: boolean
  pinnedOnly: boolean
  hasRevealVisualOnly: boolean
}

export const NO_FILTERS: Filters = {
  status: 'all',
  author: 'all',
  type: 'all',
  difficulty: 'all',
  section: 'all',
  search: '',
  flaggedOnly: false,
  hasVideoOnly: false,
  pinnedOnly: false,
  hasRevealVisualOnly: false,
}

export function countSecondaryFilters(filters: Filters): number {
  let n = 0
  if (filters.author !== 'all') n += 1
  if (filters.type !== 'all') n += 1
  if (filters.difficulty !== 'all') n += 1
  if (filters.section !== 'all') n += 1
  if (filters.flaggedOnly) n += 1
  if (filters.hasVideoOnly) n += 1
  if (filters.pinnedOnly) n += 1
  if (filters.hasRevealVisualOnly) n += 1
  return n
}

export const isFiltered = (filters: Filters) =>
  filters.status !== 'all' || filters.search.trim() !== '' || countSecondaryFilters(filters) > 0

export function matches(question: Question, filters: Filters): boolean {
  if (filters.status !== 'all' && question.status !== filters.status) return false
  if (filters.author === 'ai' && question.origin !== 'generated') return false
  if (filters.author === 'ai_unedited' && (question.origin !== 'generated' || question.edited)) return false
  if (filters.author === 'ai_edited' && (question.origin !== 'generated' || !question.edited)) return false
  if (filters.author === 'manual' && question.origin !== 'manual') return false
  if (filters.type !== 'all' && questionType(question) !== filters.type) return false
  if (filters.difficulty !== 'all' && question.difficulty !== filters.difficulty) return false
  if (filters.section === 'none' && question.sectionId !== null) return false
  if (filters.section !== 'all' && filters.section !== 'none' && question.sectionId !== filters.section) return false
  if (filters.flaggedOnly && question.flags === 0) return false
  if (filters.hasVideoOnly && !question.videoUrl) return false
  if (filters.pinnedOnly && !question.pinned) return false
  if (filters.hasRevealVisualOnly && !question.revealImageId) return false
  const needle = filters.search.trim().toLowerCase()
  if (needle) {
    const haystack = [question.title, question.text, ...(question.options ?? []), question.explanation, question.section, question.videoTitle]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    if (!haystack.includes(needle)) return false
  }
  return true
}

export function sortQuestions(list: Question[], sort: QuestionSort): Question[] {
  if (sort === 'default') return list
  const copy = [...list]
  if (sort === 'hardest') {
    return copy.sort((a, b) => {
      if (a.accuracy === null && b.accuracy === null) return 0
      if (a.accuracy === null) return 1
      if (b.accuracy === null) return -1
      return a.accuracy - b.accuracy || b.served - a.served
    })
  }
  if (sort === 'easiest') {
    return copy.sort((a, b) => {
      if (a.accuracy === null && b.accuracy === null) return 0
      if (a.accuracy === null) return 1
      if (b.accuracy === null) return -1
      return b.accuracy - a.accuracy || b.served - a.served
    })
  }
  if (sort === 'most_asked') {
    return copy.sort((a, b) => b.served - a.served)
  }
  return copy.sort((a, b) => b.flags - a.flags || b.served - a.served)
}

/** What a question looks like after the server has accepted `patch`. */
export function withPatch(question: Question, patch: QuestionPatch): Question {
  const next: Question = { ...question, ...patch }
  if (patch.status === 'approved' || patch.clearFlags) {
    next.flags = 0
    next.flagReasons = []
    if (patch.status === 'approved' || question.pulledReason === 'flagged by players') {
      next.pulledReason = null
    }
  }
  if (
    patch.status === 'rejected' ||
    patch.pinned === true ||
    patch.difficulty !== undefined ||
    patch.sectionId !== undefined ||
    patch.text !== undefined
  ) {
    next.edited = true
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
