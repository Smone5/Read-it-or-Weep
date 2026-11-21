// Shapes returned by the admin API (backend/routers/admin.py and the services it calls).
//
// Field names are exactly what the API sends. Most views are camelCase; rows passed
// straight through from the database (documents, sections, company settings, live
// sessions, admins, the "hardest questions" insight) are snake_case.

export type AdminUser = { id: string; email: string; name: string | null }

export type AdminRow = AdminUser & { created_at: string; last_login_at: string | null }

// ---- game rules -----------------------------------------------------------------

export type ModeId = 'show' | 'sudden_death' | 'speed' | 'straight' | 'jukebox'

export type ModeMeta = { id: ModeId; name: string; tagline: string }

export type QuestionSources = 'all' | 'manual' | 'ai'

export type PictureMode = 'auto' | 'show' | 'memory'

export type SoundtrackVolume = 'quiet' | 'medium' | 'loud'

export type MusicMix = {
  opening: number
  /** Seconds the theme plays on its own before the host's first word. */
  opening_sec: number
  under_host: number
  /** -3 is silent. */
  questions: number
  between: number
  /** The music eases off if a player lingers on an answer. */
  ease_down: boolean
}

/** Company defaults with one quiz's overrides on top (settings_store.quiz_rules). */
export type GameRules = {
  modes: ModeId[]
  default_mode: ModeId
  auto_approve: boolean
  question_sources: QuestionSources
  music_enabled: boolean
  /** Hits from the game's decade play on the results screen once a game is over. */
  after_party: boolean
  /** ...and quietly behind the questions while the game is being played. */
  soundtrack: boolean
  soundtrack_volume: SoundtrackVolume
  /** The music moment by moment, in steps of about 3 dB from the mix tuned by ear (0). */
  mix?: MusicMix
  decades: string[]
  picture_mode: PictureMode
  ready_threshold: number
  leaderboard: boolean
  report_url?: string | null
  allow_download?: boolean
  unlisted?: boolean
  confidential_voice?: boolean
  purge_source_after_process?: boolean
  show: {
    questions: number
    music_rounds: number
    lightning_items: number
    second_chance: number
    final_wager: boolean
    timer_sec: number
    picture_timer_sec: number
    lifelines: { fifty: number; peek: number; hint: number }
  }
  sudden_death: { strikes: number; timer_start_sec: number; timer_min_sec: number; music_every: number; max_questions: number }
  speed: { clock_sec: number; wrong_penalty_sec: number }
  straight: { per_section: number; max_questions: number; pass_mark: number }
  live: { questions: number; timer_sec: number; music_rounds: number; head_start: number }
}

/** Lists are replaced whole by the server's deep merge, so they stay whole here too. */
export type DeepPartial<T> = T extends readonly unknown[] ? T : T extends object ? { [K in keyof T]?: DeepPartial<T[K]> } : T

export type VoiceRegister = 'deeper' | 'lower-mid' | 'middle' | 'higher'

export type VoiceTone = 'clear' | 'smooth' | 'gravelly'

/** An OpenAI voice: two things measured from its audio, how low or high it sits and how clean it sounds. */
export type OpenAIVoiceFacts = { provider?: 'openai'; pitchHz: number; register: VoiceRegister; clarityDb: number; tone: VoiceTone }

/** An ElevenLabs host voice, described in words; `fallback` is the OpenAI voice that stands in for it without a key. */
export type ElevenVoiceFacts = {
  provider: 'elevenlabs'
  label: string
  blurb: string
  short: string
  fallback: string
  /** Where to add it to the ElevenLabs account ("Add to My Voices"): the API only uses voices the account has. */
  addLink: string
  /** Whether the account has it (absent when that could not be checked). */
  inAccount?: boolean
}

export type VoiceInfo = Record<string, OpenAIVoiceFacts | ElevenVoiceFacts>

/** How a host character performs: the acting, as opposed to the voice. */
export type HostManner = 'wiseguy' | 'deadpan' | 'showman'

export type MannerMeta = { id: HostManner; label: string; blurb: string }

export type Meta = {
  modes: ModeMeta[]
  decades: string[]
  voices: string[]
  voiceInfo?: VoiceInfo
  /** Whether an ElevenLabs key is set, so its voices are really heard (otherwise their stand-ins are). */
  elevenConfigured?: boolean
  manners?: MannerMeta[]
  extensions: string[]
  aiConfigured: boolean
  gameDefaults: GameRules
  webBaseUrl: string
}

// ---- company settings ---------------------------------------------------------------

export type BrandingSettings = {
  company_name: string
  game_title: string
  tagline: string
  logo_key: string | null
  primary_color: string
  accent_color: string
  theme: 'dark' | 'light'
  support_email: string | null
}

export type HostCharacter = { id: string; name: string; voice: string; style?: string; manner?: HostManner }

export type VoiceQuality = 'standard' | 'premium'

export type HostPace = 'relaxed' | 'snappy' | 'rapid'

export type HostSettings = {
  sarcasm: number
  voice_enabled: boolean
  /** Players may hold a button and talk to the host. */
  talk_enabled: boolean
  custom_instructions: string
  player_can_choose: boolean
  voice_quality: VoiceQuality
  pace: HostPace
  /** % of the month's ElevenLabs plan after which new lines use the backup voice (0: only if it runs out). */
  voice_budget?: number
  /** One to three characters. `name` and `voice` below are a read-only copy of the first. */
  hosts: HostCharacter[]
  name: string
  voice: string
}

/** What the Host page sends. The server assigns each character's id, so the whole list goes every time. */
export type HostSettingsWrite = {
  sarcasm: number
  voice_enabled: boolean
  talk_enabled: boolean
  custom_instructions: string
  player_can_choose: boolean
  voice_quality: VoiceQuality
  pace: HostPace
  voice_budget: number
  hosts: { name: string; voice: string; style: string; manner: HostManner }[]
}

/** Body of POST /host/sample. With a `voice` the line comes back spoken as well as written. */
export type HostSampleRequest = {
  sarcasm: number
  name: string
  voice: string
  quality: VoiceQuality
  pace: HostPace
  style?: string
  manner?: HostManner
}

/** `audio` is a `data:audio/wav;base64,…` URL, or null when no voice was asked for. */
export type HostSample = { line: string; style: string; audio: string | null }

export type SettingsRead<T> = { value: T; defaults: T }

export type SettingsWritten<T> = { value: T }

export type MusicStatus = { decades: { decade: string; tracks: number }[] }

// ---- quizzes ------------------------------------------------------------------------

export type QuizStatus = 'draft' | 'open' | 'closed'

export type QuizOverview = {
  approved: number
  pending: number
  manual: number
  flagged: number
  players: number
  games: number
  documents: number
}

export type Quiz = {
  id: string
  slug: string
  title: string
  description: string | null
  meetingAt: string | null
  status: QuizStatus
  accessCode: string | null
  allowedEmailDomains: string[]
  teams: string[]
  /** Only this quiz's own overrides; `rules` is the merged result. */
  settings: DeepPartial<GameRules>
  createdAt: string
  playUrl: string
}

export type QuizListItem = Quiz & { overview: QuizOverview }

export type QuizPatch = {
  title?: string
  description?: string | null
  meetingAt?: string
  clearMeetingAt?: boolean
  status?: QuizStatus
  accessCode?: string
  allowedEmailDomains?: string[]
  teams?: string[]
  settings?: DeepPartial<GameRules>
}

export type QuizSaved = { quiz: Quiz; rules: GameRules }

export type DocumentStatus = 'uploaded' | 'processing' | 'ready' | 'failed'

/** While working: label/done/total. When ready: drafted/kept/rejected. After a top-up: added/rejected. */
export type DocumentProgress = {
  label?: string
  done?: number
  total?: number
  drafted?: number
  kept?: number
  added?: number
  rejected?: Record<string, number>
}

export type QuizDocument = {
  id: string
  filename: string
  role: 'primary' | 'supporting'
  status: DocumentStatus
  stage: string | null
  progress: DocumentProgress
  error: string | null
  warnings: string[]
  title: string | null
  summary: string | null
  word_count: number | null
  section_count: number | null
  image_count: number | null
  chunk_count: number | null
  question_count: number | null
  size_bytes: number | null
  created_at: string
  processed_at: string | null
}

/** `questions` counts the live (approved) questions filed under the section. */
export type Section = {
  id: string
  heading: string
  ordinal: number
  document_id: string
  questions: number
  videos?: number
  video_url?: string | null
  video_title?: string | null
}

export type VideoInspectResult = {
  videoId: string
  watchUrl: string
  thumbnailUrl: string
  startSec: number
  title: string | null
  author: string | null
  embeddable: boolean
  suggestedExternal: boolean
  note: string | null
  normalizedUrl: string
}

export type QuizDetailData = {
  quiz: Quiz
  rules: GameRules
  overview: QuizOverview
  documents: QuizDocument[]
  sections: Section[]
}

// ---- question bank ------------------------------------------------------------------

export type QuestionKind = 'mcq' | 'fact_fake'

export type QuestionStatus = 'pending' | 'approved' | 'rejected'

export type Difficulty = 'easy' | 'medium' | 'hard'

export type Question = {
  id: string
  kind: QuestionKind
  sourceType: 'text' | 'table' | 'chart' | 'image'
  skill: 'recall' | 'understanding'
  title: string | null
  text: string
  /** Four answers for `mcq`; null for `fact_fake`. */
  options: string[] | null
  correctIndex: number | null
  /** Set for `fact_fake` only: true = FACT, false = FAKE. */
  isTrue: boolean | null
  explanation: string | null
  sourceQuote: string | null
  videoUrl?: string | null
  videoTitle?: string | null
  difficulty: Difficulty
  status: QuestionStatus
  origin: 'generated' | 'manual'
  edited: boolean
  pinned: boolean
  flags: number
  served: number
  /** Percentage answered correctly, or null when it has never been asked. */
  accuracy: number | null
  pulledReason: string | null
  sectionId: string | null
  section: string | null
  imageId: string | null
  imageUrl: string | null
  flagReasons: string[]
}

export type QuizImage = { id: string; label: string; url: string }

export type QuestionBank = { questions: Question[]; images: QuizImage[] }

/** Body of POST /quizzes/{id}/questions (`QuestionWrite` in admin.py). */
export type QuestionWrite = {
  kind: QuestionKind
  title: string | null
  text: string
  options: string[] | null
  correctIndex: number | null
  isTrue: boolean | null
  explanation: string | null
  videoUrl?: string | null
  videoTitle?: string | null
  difficulty: Difficulty
  sectionId: string | null
  imageId: string | null
  pinned: boolean
  status: QuestionStatus
}

/** Body of PATCH /questions/{id}: any subset. Sending text/options/correctIndex/isTrue marks it "edited". */
export type QuestionPatch = Partial<{
  status: QuestionStatus
  pinned: boolean
  text: string
  options: string[]
  correctIndex: number
  isTrue: boolean
  title: string
  explanation: string
  videoUrl: string
  videoTitle: string
  difficulty: Difficulty
  sectionId: string | null
}>

export type BulkAction = 'approve' | 'reject' | 'delete' | 'pin' | 'unpin'

export type Suggestion = { wrongAnswers: string[]; title: string; explanation: string; concern: string }

// ---- results ------------------------------------------------------------------------

export type Participant = {
  id: string
  name: string
  email: string
  team: string | null
  games: number
  bestScore: number | null
  answered: number
  correct: number
  accuracy: number
  sectionsCleared: number
  sectionsTotal: number
  readiness: number
  ready: boolean
  peeksUsed: number
  passed: boolean | null
  lastPlayedAt: string | null
}

export type LeaderboardEntry = {
  rank: number
  name: string
  team: string | null
  score: number
  badge: string | null
  decade: string | null
  passed: boolean | null
  you: boolean
}

export type Leaderboard = {
  mode: ModeId
  modeName: string
  entries: LeaderboardEntry[]
  you: LeaderboardEntry | null
  players: number
  teams: { team: string; average: number; players: number }[]
}

export type SectionInsight = {
  id: string
  heading: string
  answered: number
  correct: number
  players: number
  accuracy: number | null
}

export type HardQuestion = {
  id: string
  title: string | null
  question_text: string
  kind: QuestionKind
  times_served: number
  times_correct: number
  flag_count: number
  heading: string | null
  accuracy: number
}

export type FeedbackComment = { stars: number | null; comment: string; created_at: string }

export type Results = {
  participants: Participant[]
  ready: number
  threshold: number
  leaderboards: Partial<Record<ModeId, Leaderboard>>
  insights: { sections: SectionInsight[]; hardestQuestions: HardQuestion[]; weakestSections: SectionInsight[] }
  feedback: { average: number | null; count: number; comments: FeedbackComment[] }
}

// ---- live games ---------------------------------------------------------------------

/** Returned once when a live game is created. `hostUrl` carries the control key. */
export type LiveCreated = { code: string; hostKey: string; questions: number; hostUrl: string; joinUrl: string }

export type LiveSession = {
  id: string
  code: string
  status: 'lobby' | 'question' | 'reveal' | 'scoreboard' | 'finished'
  created_at: string
  finished_at: string | null
  questions: number
  players: number
}

export type LiveStanding = {
  name: string
  email: string
  team: string | null
  score: number
  correct_count: number
  head_start: number
}
