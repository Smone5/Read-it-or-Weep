// Where the admin is mounted (see main.tsx). Links are written out in full so they mean
// the same thing on every screen, however deeply nested the screen that renders them.

export const ADMIN_BASE = '/admin'

export type QuizTab = 'overview' | 'report' | 'questions' | 'play-styles' | 'results' | 'live'

export const paths = {
  home: ADMIN_BASE,
  defaults: `${ADMIN_BASE}/defaults`,
  branding: `${ADMIN_BASE}/branding`,
  host: `${ADMIN_BASE}/host`,
  music: `${ADMIN_BASE}/music`,
  admins: `${ADMIN_BASE}/admins`,
  quiz: (quizId: string, tab: QuizTab = 'overview') => `${ADMIN_BASE}/quizzes/${quizId}${tab === 'overview' ? '' : `/${tab}`}`,
}

/** Which tab a quiz URL is showing. */
export function tabFromPath(pathname: string, quizId: string): QuizTab {
  const rest = pathname.slice(paths.quiz(quizId).length).replace(/^\/+|\/+$/g, '')
  const first = rest.split('/')[0]
  return (['report', 'questions', 'play-styles', 'results', 'live'] as const).find((tab) => tab === first) ?? 'overview'
}
