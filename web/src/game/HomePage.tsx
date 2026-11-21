// The front door: pick a quiz, or type a live-game code.

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconCheck, IconPlay, IconStar } from '../components/Icons'
import { FullScreenMessage, Logo, Spinner, Stage, untilText } from '../components/ui'
import { api } from '../lib/api'
import { applyBranding } from '../lib/theme'
import type { Branding } from '../lib/types'

type FrontQuiz = {
  slug: string
  title: string
  description?: string | null
  meetingAt: string | null
  teams?: string[]
  readiness?: { cleared: number; total: number; percent: number; ready: boolean }
}

type Config = {
  branding: Branding
  quizzes: FrontQuiz[]
  player?: { name: string; team: string | null } | null
}

function isUpcoming(iso: string | null): boolean {
  if (!iso) return false
  const ms = new Date(iso).getTime()
  return !Number.isNaN(ms) && ms >= Date.now() - 24 * 3600 * 1000
}

export default function HomePage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState('')
  const [code, setCode] = useState('')
  const [query, setQuery] = useState('')
  const [team, setTeam] = useState<string>('all')

  useEffect(() => {
    api<Config>('/api/config')
      .then((data) => {
        applyBranding(data.branding)
        setConfig(data)
      })
      .catch((caught: Error) => setError(caught.message))
  }, [])

  const allTeams = useMemo(() => {
    if (!config) return []
    const set = new Set<string>()
    for (const q of config.quizzes) {
      for (const t of q.teams ?? []) set.add(t)
    }
    return [...set].sort((a, b) => a.localeCompare(b))
  }, [config])

  const filtered = useMemo(() => {
    if (!config) return []
    const needle = query.trim().toLowerCase()
    return config.quizzes.filter((q) => {
      if (team !== 'all' && !(q.teams ?? []).includes(team)) return false
      if (!needle) return true
      return (
        q.title.toLowerCase().includes(needle) ||
        (q.description ?? '').toLowerCase().includes(needle) ||
        (q.teams ?? []).some((t) => t.toLowerCase().includes(needle))
      )
    })
  }, [config, query, team])

  if (error) return <FullScreenMessage title="We could not load the game">{error}</FullScreenMessage>
  if (!config) return <Stage className="grid place-items-center"><Spinner /></Stage>

  const { branding, quizzes, player } = config
  const showFilters = quizzes.length >= 6
  const upcoming = filtered.filter((q) => isUpcoming(q.meetingAt))
  const other = filtered.filter((q) => !isUpcoming(q.meetingAt))
  const splitGroups = upcoming.length > 0 && other.length > 0 && quizzes.length > 3

  const renderQuizButton = (quiz: FrontQuiz) => {
    const meeting = untilText(quiz.meetingAt)
    const r = quiz.readiness
    return (
      <button
        key={quiz.slug}
        className="answer w-full min-w-0 items-center overflow-hidden text-left"
        style={{ ['--tone' as string]: 'var(--brand)', ['--tone-ink' as string]: 'var(--brand-ink)' }}
        onClick={() => navigate(`/q/${quiz.slug}`)}
      >
        <span className="badge flex-none" aria-hidden><IconPlay size={14} /></span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold leading-snug">{quiz.title}</span>
          {(meeting || quiz.description) && (
            <span className="mt-0.5 block text-sm leading-snug text-muted">
              {meeting && <span style={{ color: 'var(--accent-text)' }}>Meeting {meeting}</span>}
              {!meeting && quiz.description && <span className="line-clamp-2">{quiz.description}</span>}
            </span>
          )}
        </span>
        {r && r.total > 0 && (
          <span
            className="pixel inline-flex flex-none items-center gap-1 rounded-[4px] border px-2 py-1 text-[9px] tabular-nums"
            style={{
              borderColor: r.ready ? 'var(--good)' : 'var(--accent)',
              color: r.ready ? 'var(--good)' : 'var(--accent-text)',
              background: r.ready ? 'color-mix(in oklab, var(--good) 14%, transparent)' : 'color-mix(in oklab, var(--accent) 12%, transparent)',
            }}
          >
            {r.ready ? <><IconStar size={10} /> Ready</> : <><IconCheck size={10} /> {r.cleared}/{r.total}</>}
          </span>
        )}
      </button>
    )
  }

  return (
    <Stage>
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-5 py-4 lg:max-w-xl">
        <header className="text-center">
          <div className="mb-4 flex justify-center"><Logo branding={branding} size={56} /></div>
          <p className="pixel text-[10px] text-muted">{branding.companyName}</p>
          <h1 className="neon mt-2 text-lg sm:text-2xl">{branding.gameTitle}</h1>
          <p className="mt-3 text-muted">{branding.tagline}</p>
          {player && (
            <p className="mt-2 text-xs text-muted">
              Signed in as <b className="text-ink">{player.name}</b>{player.team ? ` · ${player.team}` : ''}
            </p>
          )}
        </header>

        <section className="card rise p-5">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs">Choose a report</h2>
            {quizzes.length > 0 && <span className="text-xs text-muted">{filtered.length} open</span>}
          </div>

          {showFilters && (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <input
                type="search"
                className="field min-w-0 flex-1 text-sm"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search reports or topics…"
                aria-label="Search open reports"
              />
              {allTeams.length > 1 && (
                <select
                  className="field text-sm sm:w-44"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                  aria-label="Filter by team"
                >
                  <option value="all">All teams</option>
                  {allTeams.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              )}
            </div>
          )}

          {quizzes.length === 0 ? (
            <p className="mt-3 text-muted">No quiz is open right now. Use the link in your invitation when one is.</p>
          ) : filtered.length === 0 ? (
            <p className="mt-4 text-sm text-muted">No open reports match that filter.</p>
          ) : splitGroups ? (
            <div className="mt-4 flex flex-col gap-4">
              <div>
                <p className="pixel mb-2 text-[9px]" style={{ color: 'var(--accent-text)' }}>Up next</p>
                <div className="flex flex-col gap-2.5">{upcoming.map(renderQuizButton)}</div>
              </div>
              <div>
                <p className="pixel mb-2 text-[9px] text-muted">All open reports</p>
                <div className="scroll-thin flex max-h-[38dvh] flex-col gap-2.5 overflow-y-auto pr-0.5">{other.map(renderQuizButton)}</div>
              </div>
            </div>
          ) : (
            <div className="scroll-thin mt-3 flex max-h-[52dvh] flex-col gap-2.5 overflow-y-auto pr-0.5">
              {filtered.map(renderQuizButton)}
            </div>
          )}
        </section>

        <form
          className="card card-accent p-5"
          onSubmit={(event) => {
            event.preventDefault()
            if (code.trim()) navigate(`/live/${code.trim().toUpperCase()}`)
          }}
        >
          <label className="label" htmlFor="live-code">Joining a live game? Enter the code on the big screen</label>
          <div className="flex gap-2.5">
            <input id="live-code" className="field pixel flex-1 text-base tracking-widest" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={6} autoCapitalize="characters" autoComplete="off" placeholder="ABCDE" />
            <button className="btn btn-accent" disabled={code.trim().length < 4}>Join</button>
          </div>
        </form>
      </div>
    </Stage>
  )
}
