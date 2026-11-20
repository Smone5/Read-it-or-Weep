// Shapes returned by the backend (see backend/routers/play.py, live.py and services/engine.py).

export type Branding = {
  companyName: string
  gameTitle: string
  tagline: string
  primaryColor: string
  accentColor: string
  theme: 'dark' | 'light'
  logoUrl: string | null
  supportEmail: string | null
  host: { name: string; voiceEnabled: boolean; talkEnabled: boolean; choices: { id: string; name: string; register: string }[] }
}

export type ModeId = 'show' | 'sudden_death' | 'speed' | 'straight'

export type ModeInfo = { id: ModeId; name: string; tagline: string; music: boolean }

export type SectionState = { id: string; heading: string; state: 'cleared' | 'tried' | 'new'; answered: number; correct: number }

export type Readiness = {
  cleared: number
  total: number
  percent: number
  ready: boolean
  threshold: number
  sections: SectionState[]
}

export type PlayerView = {
  id: string
  name: string
  email: string
  team: string | null
  readiness: Readiness
  best: Partial<Record<ModeId, number>>
  unfinishedRun: { id: string; mode: ModeId } | null
}

export type QuizView = {
  slug: string
  title: string
  description: string | null
  meetingAt: string | null
  status: 'draft' | 'open' | 'closed'
  requiresCode: boolean
  teams: string[]
  sections: number
  questions: number
  ready: boolean
  leaderboard: boolean
  modes: ModeInfo[]
  defaultMode: ModeId
  decades: string[]
}

export type QuizInfo = { branding: Branding; quiz: QuizView; player: PlayerView | null }

export type Slot = 'report' | 'image' | 'music' | 'fact_fake' | 'second_chance' | 'final'

export type ItemImage = { url: string; alt: string; mode: 'show' | 'memory' }

export type Item = {
  id: string
  position: number
  slot: Slot
  round: { number: number; name: string }
  title: string | null
  section: { id: string; heading: string } | null
  timeLimitSec: number
  multiplier: number
  lifelinesUsed: string[]
  removed: number[]
  hint: string | null
  peek: string | null
  /** Dealt but not yet shown: only the round is known until the game asks to open it. */
  sealed?: boolean
  needsWager?: boolean
  wagerOptions?: number[]
  wager?: number | null
  text: string | null
  options: string[] | null
  remainingMs?: number | null
  image?: ItemImage
  music?: { previewUrl: string; asks: 'artist' | 'title' }
}

export type RunState = {
  runId: string
  mode: ModeId
  modeName: string
  status: 'in_progress' | 'completed' | 'abandoned'
  score: number
  streak: number
  answered: number
  correct: number
  decade: string | null
  lifelines: Record<string, number>
  remaining: number | null
  nextRound: string | null
  strikes?: number
  maxStrikes?: number
  /** Speed Run: the length of the clock, and (once the first question has opened) when it runs out. */
  clockSec?: number
  deadlineAt?: string
  remainingMs?: number
}

export type Turn = { state: RunState; finished: boolean; item: Item | null }

export type Reveal = {
  itemId: string
  slot: Slot
  correct: boolean
  timedOut: boolean
  selectedIndex: number | null
  correctIndex: number
  correctAnswer: string
  points: number
  state: RunState
  finished: boolean
  explanation?: string | null
  sourceQuote?: string | null
  section?: { id: string; heading: string }
  image?: ItemImage
  music?: { artist: string; title: string; artworkUrl: string | null; trackUrl: string | null }
  strikeRestored?: boolean
}

/** One song of the playlist that plays behind the results. */
export type PartyTrack = { title: string; artist: string; previewUrl: string; artworkUrl: string | null; trackUrl: string | null }
export type AfterParty = { decade: string; tracks: PartyTrack[] }

export type Summary = {
  /** Music for the results screen: hits from the game's decade (null when music is off). */
  afterParty: AfterParty | null
  runId: string
  mode: ModeId
  modeName: string
  status: string
  score: number
  badge: string | null
  passed: boolean | null
  passMark: number | null
  accuracy: number
  report: { answered: number; correct: number }
  music: { answered: number; correct: number }
  streakMax: number
  durationSec: number | null
  decade: string | null
  sections: { id: string; heading: string; answered: number; correct: number }[]
  blindSpots: { id: string; heading: string }[]
  untouched: { id: string; heading: string }[]
  review: { title: string | null; text: string; section: string | null; correctAnswer: string; yourAnswer: string | null; explanation: string | null }[]
  readiness: Readiness
  rank: number | null
  players: number
  personalBest: boolean
  previousBest: number | null
}

export type LeaderboardEntry = { rank: number; name: string; team: string | null; score: number; badge: string | null; decade: string | null; passed: boolean | null; you: boolean }

export type Leaderboard = {
  mode: ModeId
  modeName: string
  entries: LeaderboardEntry[]
  you: LeaderboardEntry | null
  players: number
  teams: { team: string; average: number; players: number }[]
}

// ---- live group play -----------------------------------------------------------

export type LiveStatus = 'lobby' | 'question' | 'reveal' | 'scoreboard' | 'finished'

export type LiveQuestion = {
  number: number
  kind: 'mcq' | 'music'
  title: string | null
  text: string
  options: string[]
  limitSec: number
  section: string | null
  opensAt: string | null
  endsAt: string | null
  imageUrl?: string
  previewUrl?: string
}

export type LiveState = {
  code: string
  status: LiveStatus
  version: number
  index: number
  total: number
  quizTitle: string
  quizSlug: string
  players: number
  serverNow: string
  role: 'host' | 'player'
  decade: string | null
  you?: { name: string; score: number; rank: number; streak: number; headStart: number } | null
  roster?: { name: string; team: string | null; prepared: boolean }[]
  headStart?: number
  question?: LiveQuestion
  answersIn?: number
  yourAnswer?: { selectedIndex: number | null; correct?: boolean; points?: number }
  reveal?: {
    correctIndex: number
    counts: number[]
    explanation: string | null
    sourceQuote: string | null
    music?: { artist: string; title: string; artworkUrl: string | null }
  }
  standings?: { rank: number; name: string; team: string | null; score: number; streak: number }[]
  teams?: { team: string; average: number; players: number }[]
  roomMissed?: { text: string; correctAnswer: string; section: string | null; explanation: string | null; percentCorrect: number }[]
  /** Big screen only, once the game is over: music for the room while the results are up. */
  afterParty?: AfterParty
}
