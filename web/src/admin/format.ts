// Wording and formatting shared by the admin screens.

import type { Difficulty, DocumentProgress, QuestionStatus, QuizDocument, QuizStatus } from './types'

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

const DATE = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(iso: string | null | undefined, fallback = '—'): string {
  const date = parse(iso)
  return date ? DATE_TIME.format(date) : fallback
}

export function formatDate(iso: string | null | undefined, fallback = '—'): string {
  const date = parse(iso)
  return date ? DATE.format(date) : fallback
}

/** "just now", "5 minutes ago", "yesterday", then a plain date. */
export function timeAgo(iso: string | null | undefined, fallback = '—'): string {
  const date = parse(iso)
  if (!date) return fallback
  const minutes = Math.round((Date.now() - date.getTime()) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return DATE.format(date)
}

/** How far off a meeting is: "in 3 days", "tomorrow", "in 2 hours", or "3 days ago" once it has passed. */
export function meetingHint(iso: string | null | undefined): string | null {
  const date = parse(iso)
  if (!date) return null
  const ms = date.getTime() - Date.now()
  if (ms < 0) return timeAgo(iso)
  const hours = ms / 3_600_000
  if (hours < 1) return `in ${Math.max(1, Math.round(ms / 60_000))} min`
  if (hours < 24) return `in ${Math.round(hours)} hour${Math.round(hours) === 1 ? '' : 's'}`
  const days = Math.round(hours / 24)
  return days === 1 ? 'tomorrow' : `in ${days} days`
}

/** An API timestamp as the value of an `<input type="datetime-local">`, in the viewer's own time zone. */
export function toLocalInput(iso: string | null | undefined): string {
  const date = parse(iso)
  if (!date) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** The reverse: a `datetime-local` value (local time) as a timestamp the API accepts. */
export function fromLocalInput(value: string): string | null {
  return parse(value)?.toISOString() ?? null
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return ''
  if (bytes < 1024) return `${bytes} bytes`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export const DECADE_LABEL: Record<string, string> = {
  '50s': '1950s',
  '60s': '1960s',
  '70s': '1970s',
  '80s': '1980s',
  '90s': '1990s',
  '00s': '2000s',
  '10s': '2010s',
  '20s': '2020s',
}

export const decadeLabel = (decade: string) => DECADE_LABEL[decade] ?? decade

export type Tone = 'neutral' | 'brand' | 'good' | 'warn' | 'bad' | 'info'

export const QUIZ_STATUS: Record<QuizStatus, { label: string; tone: Tone; explain: string }> = {
  draft: { label: 'Draft', tone: 'neutral', explain: 'Only signed-in admins can play-test it.' },
  open: { label: 'Open', tone: 'good', explain: 'Anyone with the link can play.' },
  closed: { label: 'Closed', tone: 'bad', explain: 'Finished. Nobody can play; results are kept.' },
}

export const QUESTION_STATUS: Record<QuestionStatus, { label: string; tone: Tone }> = {
  approved: { label: 'Live', tone: 'good' },
  pending: { label: 'Waiting for review', tone: 'warn' },
  rejected: { label: 'Rejected', tone: 'neutral' },
}

export const DIFFICULTY_LABEL: Record<Difficulty, string> = { easy: 'Easy', medium: 'Medium', hard: 'Hard' }

// ---- documents --------------------------------------------------------------------

const MINUTE = 60_000

/**
 * True while the server is (or is about to start) working on a document.
 *
 * There is no "started at" time to judge a stuck job by (a document's `created_at` is when it
 * was first uploaded, which says nothing once it has been processed again), so a job that has
 * died is noticed another way: by nothing changing for a while. See `useDocumentWatch`.
 */
export function isWorking(doc: QuizDocument): boolean {
  if (doc.status === 'processing') return true
  // Just uploaded: the job starts within a moment. Much longer than that and it never started.
  if (doc.status === 'uploaded') return Date.now() - new Date(doc.created_at).getTime() < 2 * MINUTE
  // A ready document with a stage is having more questions written for it.
  return doc.status === 'ready' && Boolean(doc.stage)
}

// The quality checker's own reason codes (ingest/generate.py), each worded to follow "3 dropped …".
const CHECKER_PROBLEMS: Record<string, string> = {
  ambiguous: 'as ambiguous',
  multiple_correct: 'because more than one answer could be defended',
  not_answerable: 'because the report does not settle the answer',
  leaks_answer: 'because the wording gave the answer away',
  refers_to_passage: 'for referring to “the passage” instead of standing alone',
  guessable: 'as guessable without reading the report',
  not_readable: 'because the answer could not be read clearly from the picture',
}

const REJECTION_REASONS: Record<string, string> = {
  'checker skipped it': 'after being skipped by the second check',
  'checker chose a different answer': 'because a second check picked a different answer',
  'checker disagreed on a fact/fake statement': 'because a second check disagreed about fact or fake',
  'wrong number of options': 'for not having exactly four answers',
  'duplicate options': 'for repeating an answer',
  'question or option too long/short': 'for being too long or too short',
  'statement too long/short': 'for being too long or too short',
  "refers to 'the passage'": 'for referring to “the passage” instead of standing alone',
  'answer appears in the question': 'because the answer appeared in the question',
  'correct option is conspicuously longer': 'because the right answer stood out as the longest',
  'all/none of the above': 'for using “all” or “none of the above”',
  'supporting quote not found in the passage': 'because the supporting quote was not in the report',
  'number in the answer is not in the passage': 'because the answer used a number that is not in the report',
  "a 'fact' uses a number that is not in the passage": 'because a “fact” used a number that is not in the report',
  'near-duplicate of another question': 'for being too similar to another question',
}

function rejectionLine(reason: string, count: number): string {
  if (reason === 'passage failed (AI error)') {
    return `${plural(count, 'passage')} could not be processed because the AI service had a problem`
  }
  if (reason === 'picture failed (AI error)') {
    return `${plural(count, 'picture')} could not be processed because the AI service had a problem`
  }
  const prefix = 'checker flagged: '
  if (reason.startsWith(prefix)) {
    const problems = reason
      .slice(prefix.length)
      .split(',')
      .map((problem) => CHECKER_PROBLEMS[problem.trim()] ?? `(${problem.trim().replace(/_/g, ' ')})`)
    return `${count} dropped ${problems.join(' and ')}`
  }
  const known = REJECTION_REASONS[reason]
  return known ? `${count} dropped ${known}` : `${count} dropped (${reason})`
}

/** The quality checker's tally in plain words, biggest group first. */
export function describeRejections(rejected: DocumentProgress['rejected']): string[] {
  return Object.entries(rejected ?? {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([reason, count]) => rejectionLine(reason, count))
}

/**
 * A document's summary arrives as one text: the summary, then an optional "Key themes: …" line.
 * The themes are joined with semicolons but may contain semicolons themselves, so that line
 * is kept whole rather than split into a list.
 */
export function splitSummary(summary: string | null): { text: string; themes: string } {
  const lines = (summary ?? '').split('\n')
  const marker = 'Key themes:'
  const themeLine = lines.find((line) => line.startsWith(marker))
  return {
    text: lines.filter((line) => line !== themeLine).join('\n').trim(),
    themes: themeLine ? themeLine.slice(marker.length).trim() : '',
  }
}

// ---- links ------------------------------------------------------------------------

/** True when a link the server built points at the site the admin is looking at right now. */
export function isThisSite(url: string): boolean {
  try {
    return new URL(url, window.location.href).origin === window.location.origin
  } catch {
    return false
  }
}

/**
 * The same page on the site the admin is using now. Play-testing a draft relies on the
 * admin's sign-in, which the browser only shares with pages on this exact address.
 */
export function onThisSite(url: string): string {
  try {
    const parsed = new URL(url, window.location.href)
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return url
  }
}
