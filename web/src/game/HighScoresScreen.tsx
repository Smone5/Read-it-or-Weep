// Full-page retro-arcade High Scores screen: mode tabs, live search, team filtering,
// pinned personal standing + score history, top-3 showcase, and full-page scrolling standings.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { IconArrowLeft, IconClose, IconCrown, IconSearch, IconStar, IconTrophy } from '../components/Icons'
import { FullScreenMessage, Logo, Spinner, Stage } from '../components/ui'
import { api } from '../lib/api'
import { applyBranding, DECADE_LABEL } from '../lib/theme'
import type { Branding, Leaderboard, LeaderboardEntry, ModeId, ModeInfo, QuizInfo } from '../lib/types'
import { formatScore } from './parts'

const PAGE_SIZE = 100

function rankTone(rank: number): { color: string; border: string } {
  if (rank === 1) return { color: '#ffd60a', border: 'color-mix(in oklab, #ffd60a 65%, transparent)' }
  if (rank === 2) return { color: '#e2e8f0', border: 'color-mix(in oklab, #cbd5e1 55%, transparent)' }
  if (rank === 3) return { color: '#f59e0b', border: 'color-mix(in oklab, #f59e0b 60%, transparent)' }
  return { color: 'var(--muted)', border: 'color-mix(in oklab, var(--ink) 16%, transparent)' }
}

type ScreenProps = {
  slug: string
  quizTitle: string
  branding: Branding
  initialMode: ModeId
  modes: ModeInfo[]
  teams?: string[]
  bestByMode?: Partial<Record<ModeId, number>>
  backLabel?: string
  onBack: () => void
}

export function HighScoresScreen({
  slug,
  quizTitle,
  branding,
  initialMode,
  modes,
  teams = [],
  bestByMode,
  backLabel = 'Back',
  onBack,
}: ScreenProps) {
  const [mode, setMode] = useState<ModeId>(initialMode)
  const [team, setTeam] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [entries, setEntries] = useState<LeaderboardEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [runsOpen, setRunsOpen] = useState(false)
  const yourRowRef = useRef<HTMLLIElement | null>(null)
  const pendingJumpRef = useRef(false)

  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [])

  // Debounce server search when the user types.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 200)
    return () => window.clearTimeout(timer)
  }, [search])

  // Escape navigates back unless typing inside the search input.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
        event.target.blur()
        return
      }
      onBack()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onBack])

  const loadPage = useCallback(
    async (targetMode: ModeId, targetTeam: string | null, query: string, offset: number, append: boolean, limit = PAGE_SIZE) => {
      if (append) setLoadingMore(true)
      else setLoading(true)
      const params = new URLSearchParams({ mode: targetMode, limit: String(limit), offset: String(offset) })
      if (targetTeam) params.set('team', targetTeam)
      if (query) params.set('q', query)
      const data = await api<Leaderboard>(`/api/quiz/${slug}/leaderboard?${params.toString()}`).catch(() => null)
      if (data) {
        setBoard(data)
        setEntries((current) => (append ? [...current, ...data.entries] : data.entries))
      } else if (!append) {
        setBoard(null)
        setEntries([])
      }
      setLoading(false)
      setLoadingMore(false)
    },
    [slug],
  )

  useEffect(() => {
    void loadPage(mode, team, debouncedSearch, 0, false)
  }, [mode, team, debouncedSearch, loadPage])

  // If "Find me" triggered a reload/expansion, scroll once our row mounts.
  useEffect(() => {
    if (pendingJumpRef.current && yourRowRef.current) {
      pendingJumpRef.current = false
      yourRowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [entries])

  const jumpToMe = () => {
    if (!board?.you) return
    if (yourRowRef.current) {
      yourRowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    pendingJumpRef.current = true
    if (search) {
      setSearch('')
      setDebouncedSearch('')
      return
    }
    // Expand loaded range far enough to include the player's rank.
    const needed = Math.min(500, Math.max(entries.length + PAGE_SIZE, board.you.rank + 5))
    void loadPage(mode, team, '', 0, false, needed)
  }

  const teamOptions = teams.length > 0 ? teams : (board?.teams ?? []).map((t) => t.team)
  const hasTeamsPanel = (board?.teams?.length ?? 0) >= 2 && !team
  const topScore = Math.max(1, entries[0]?.score ?? 0, board?.you?.score ?? 0)
  const filtering = Boolean(debouncedSearch)
  const showPodium = !filtering && entries.length >= 3 && entries[0].rank === 1
  const podiumOrder = showPodium ? [entries[1], entries[0], entries[2]] : []
  const totalCount = board?.players ?? 0
  const matchedCount = board?.matched ?? entries.length

  const renderRow = (entry: LeaderboardEntry, keyPrefix = '') => {
    const medal = rankTone(entry.rank)
    const share = topScore > 0 ? Math.max(0.03, Math.min(1, entry.score / topScore)) : 0
    const statusText = entry.passed === true ? 'Passed' : entry.passed === false ? 'Not yet' : entry.badge
    const metaText = [entry.team, statusText, entry.decade ? DECADE_LABEL[entry.decade] ?? entry.decade : null]
      .filter(Boolean)
      .join(' · ')
    return (
      <li
        key={`${keyPrefix}${entry.rank}-${entry.name}`}
        ref={entry.you ? yourRowRef : undefined}
        className="sb-row sm:gap-3.5 sm:px-4 sm:py-3"
        data-you={entry.you}
      >
        <span className="sb-bar" style={{ transform: `scaleX(${share})` }} aria-hidden />
        <span className="sb-rank sm:h-8 sm:min-w-[46px] sm:text-[11px]" style={{ color: medal.color, borderColor: medal.border }}>
          {entry.rank === 1 && <IconCrown size={12} />}
          {String(entry.rank).padStart(2, '0')}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-base font-bold text-ink sm:text-[17px]">{entry.name}</span>
            {entry.you && <span className="sb-you-pill">You</span>}
          </div>
          {metaText && <p className="mt-0.5 truncate text-xs text-muted sm:hidden">{metaText}</p>}
        </div>
        {entry.team && (
          <span className="hidden max-w-[140px] truncate rounded-[3px] border border-line bg-black/30 px-2 py-0.5 text-xs text-muted sm:inline-block">
            {entry.team}
          </span>
        )}
        {statusText && (
          <span
            className="hidden max-w-[180px] truncate text-xs font-semibold md:inline-block"
            style={{ color: entry.passed === true ? 'var(--good)' : entry.passed === false ? 'var(--bad)' : 'var(--muted)' }}
          >
            {statusText}
          </span>
        )}
        <span
          className="sb-score sm:!text-xl"
          style={{ color: entry.you ? 'var(--accent)' : entry.rank === 1 ? '#ffd60a' : 'var(--ink)' }}
        >
          {formatScore(entry.score)}
        </span>
      </li>
    )
  }

  return (
    <div className={`mx-auto flex w-full flex-1 flex-col gap-4 pb-10 ${hasTeamsPanel ? 'max-w-5xl' : 'max-w-4xl'}`}>
      {/* Top navigation header */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <button type="button" className="btn btn-ghost btn-sm flex-none" onClick={onBack}>
            <IconArrowLeft size={14} />
            <span>{backLabel}</span>
            <span className="kbd">ESC</span>
          </button>
          <Logo branding={branding} size={34} />
          <div className="min-w-0">
            <h1 className="neon flex items-center gap-2 truncate text-sm sm:text-lg">
              <IconTrophy size={18} style={{ color: 'var(--accent)' }} />
              <span>High scores</span>
            </h1>
            <p className="truncate text-xs text-muted sm:text-sm">{quizTitle}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {board?.you && (
            <button type="button" className="btn btn-accent btn-sm" onClick={jumpToMe}>
              Find me (#{board.you.rank})
            </button>
          )}
        </div>
      </header>

      {/* Mode tabs + search + team filters */}
      <section className="card flex flex-col gap-3 p-4">
        {modes.length > 1 && (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Game mode">
            {modes.map((m) => (
              <button
                key={m.id}
                type="button"
                className="sb-tab"
                aria-pressed={mode === m.id}
                onClick={() => {
                  setMode(m.id)
                  setRunsOpen(false)
                }}
              >
                <span>{m.name}</span>
                {bestByMode?.[m.id] !== undefined && (
                  <span className="rounded-sm bg-black/45 px-1.5 py-0.5 text-xs tabular-nums" style={{ color: 'var(--accent)' }}>
                    {formatScore(bestByMode[m.id]!)}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <IconSearch size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              type="search"
              className="field min-h-[44px] pl-10 pr-10 text-base"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search players, teams or titles…"
              aria-label="Search high scores"
              autoComplete="off"
            />
            {search && (
              <button
                type="button"
                className="absolute right-1.5 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded text-muted hover:text-ink"
                onClick={() => {
                  setSearch('')
                  setDebouncedSearch('')
                }}
                aria-label="Clear search"
              >
                <IconClose size={13} />
              </button>
            )}
          </div>
          {teamOptions.length >= 2 && (
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by team">
              <button type="button" className="sb-tab" aria-pressed={team === null} onClick={() => setTeam(null)}>
                All teams
              </button>
              {teamOptions.map((t) => (
                <button key={t} type="button" className="sb-tab" aria-pressed={team === t} onClick={() => setTeam(t)}>
                  {t}
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Pinned personal standing + your recent scores in this mode */}
      {board && (
        <section className="flex flex-col gap-3">
          {board.you ? (
            <div className="sb-you-card flex flex-wrap items-center justify-between gap-4 px-4 py-3.5 sm:px-5">
              <div className="flex items-center gap-3.5">
                <span
                  className="pixel grid h-12 min-w-[56px] place-items-center rounded-[4px] border-2 px-2.5 text-base tabular-nums"
                  style={{
                    borderColor: 'var(--accent)',
                    background: 'rgb(0 0 0 / 0.5)',
                    color: board.you.rank === 1 ? '#ffd60a' : 'var(--accent)',
                  }}
                >
                  #{board.you.rank}
                </span>
                <div className="min-w-0">
                  <p className="pixel text-[9px] text-muted">
                    Your high score · {board.modeName} · #{board.you.rank} of {totalCount}
                  </p>
                  <p className="mt-1 text-base text-ink">
                    {board.you.rank === 1 ? (
                      <span className="inline-flex items-center gap-1.5 font-bold" style={{ color: '#ffd60a' }}>
                        <IconCrown size={15} /> You hold the #1 spot!
                      </span>
                    ) : board.you.gapToNext !== undefined ? (
                      <>
                        <b style={{ color: 'var(--accent)' }}>{formatScore(board.you.gapToNext)}</b> pts behind{' '}
                        {board.you.aheadName ?? `#${board.you.rank - 1}`} (#{board.you.rank - 1})
                      </>
                    ) : (
                      `Ranked #${board.you.rank} of ${totalCount}`
                    )}
                  </p>
                </div>
              </div>
              <div className="text-right">
                <p className="pixel text-[9px] text-muted">Personal best</p>
                <p className="sb-score !text-2xl sm:!text-3xl" style={{ color: 'var(--accent)' }}>
                  {formatScore(board.you.score)}
                </p>
              </div>
            </div>
          ) : (
            <div className="card px-4 py-3 text-sm text-muted">
              You haven&apos;t finished a <b className="text-ink">{board.modeName}</b> game yet — finish one to put your high score on this board.
            </div>
          )}

          {board.yourRuns && board.yourRuns.length > 0 && (
            <div className="card px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <p className="pixel text-[10px] text-muted">
                  Your {board.modeName} runs ({board.yourRuns.length})
                </p>
                {board.yourRuns.length > 5 && (
                  <button
                    type="button"
                    className="text-xs font-bold underline text-muted hover:text-ink"
                    onClick={() => setRunsOpen((open) => !open)}
                  >
                    {runsOpen ? 'Show fewer' : `Show all ${board.yourRuns.length}`}
                  </button>
                )}
              </div>
              <div className="mt-2.5 flex flex-wrap gap-2">
                {(runsOpen ? board.yourRuns : board.yourRuns.slice(0, 5)).map((run, index) => (
                  <span
                    key={run.id}
                    className="inline-flex items-center gap-1.5 rounded-[4px] border px-3 py-1.5 text-sm tabular-nums"
                    style={{
                      borderColor: run.isBest ? 'var(--accent)' : 'var(--line)',
                      background: run.isBest ? 'color-mix(in oklab, var(--accent) 16%, transparent)' : 'rgb(0 0 0 / 0.32)',
                      color: run.isBest ? 'var(--accent)' : 'var(--ink)',
                    }}
                  >
                    {run.isBest && <IconStar size={12} />}
                    <b>{formatScore(run.score)}</b>
                    {run.answered > 0 && (
                      <span className="text-xs text-muted">{Math.round((100 * run.correct) / run.answered)}%</span>
                    )}
                    {index === 0 && <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-muted">latest</span>}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* Main full-page standings */}
      {loading && !board ? (
        <div className="card py-14"><Spinner label="Loading high scores" /></div>
      ) : !board ? (
        <div className="card py-12 text-center text-muted">Could not load the high scores right now.</div>
      ) : (
        <div className={`grid items-start gap-5 ${hasTeamsPanel ? 'lg:grid-cols-12' : ''}`}>
          <div className={`flex flex-col gap-4 ${hasTeamsPanel ? 'lg:col-span-8' : ''}`}>
            {/* Top 3 showcase strip on tablet/desktop */}
            {showPodium && (
              <div className="hidden gap-3 sm:grid sm:grid-cols-3 sm:items-end">
                {podiumOrder.map((person, idx) => {
                  const isFirst = idx === 1
                  const medal = rankTone(person.rank)
                  return (
                    <div
                      key={person.rank}
                      className={`card flex flex-col items-center p-4 text-center ${isFirst ? 'card-accent sm:-mt-2 sm:py-5' : ''}`}
                      style={person.you ? { borderColor: 'var(--accent)' } : undefined}
                    >
                      <span
                        className="pixel inline-flex items-center gap-1.5 text-xs"
                        style={{ color: medal.color }}
                      >
                        {isFirst && <IconCrown size={15} />}
                        #{person.rank}
                      </span>
                      <p className="mt-2 w-full truncate text-lg font-bold text-ink">
                        {person.name}
                      </p>
                      {person.you && <span className="sb-you-pill mt-1">You</span>}
                      {person.team && <p className="mt-0.5 w-full truncate text-xs text-muted">{person.team}</p>}
                      <p
                        className="sb-score mt-2 !text-2xl"
                        style={{ color: isFirst ? '#ffd60a' : person.you ? 'var(--accent)' : 'var(--ink)' }}
                      >
                        {formatScore(person.score)}
                      </p>
                    </div>
                  )
                })}
              </div>
            )}

            <section className={`card p-4 sm:p-5 ${loading ? 'opacity-65 transition-opacity' : ''}`}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="pixel text-xs text-muted">
                  {board.modeName}
                  {team ? ` · ${team}` : ''}
                </h2>
                <p className="text-xs text-muted">
                  {filtering
                    ? `${matchedCount} match${matchedCount === 1 ? '' : 'es'} of ${totalCount} player${totalCount === 1 ? '' : 's'}`
                    : `Showing ${entries.length} of ${totalCount} player${totalCount === 1 ? '' : 's'}`}
                </p>
              </div>

              {entries.length === 0 ? (
                <p className="py-10 text-center text-muted">
                  {filtering ? `No players matching “${debouncedSearch}”.` : 'No scores yet. Be the first on the board.'}
                </p>
              ) : (
                <ol className="grid gap-1.5">
                  {entries.map((entry) => renderRow(entry))}
                </ol>
              )}

              {board.aroundYou && board.aroundYou.length > 0 && (
                <div className="mt-4">
                  <div className="my-2 flex items-center gap-2 text-xs text-muted" aria-hidden>
                    <span className="h-px flex-1 bg-line" />
                    <span className="pixel text-[9px]">Around your rank</span>
                    <span className="h-px flex-1 bg-line" />
                  </div>
                  <ol className="grid gap-1.5">
                    {board.aroundYou.map((entry) => renderRow(entry, 'near-'))}
                  </ol>
                </div>
              )}

              {board.hasMore && (
                <div className="mt-4 flex justify-center">
                  <button
                    type="button"
                    className="btn btn-ghost w-full sm:w-auto"
                    disabled={loadingMore}
                    onClick={() => void loadPage(mode, team, debouncedSearch, entries.length, true)}
                  >
                    {loadingMore
                      ? 'Loading more…'
                      : `Load more scores (${matchedCount - entries.length} more)`}
                  </button>
                </div>
              )}
            </section>
          </div>

          {hasTeamsPanel && (
            <aside className="card p-4 sm:p-5 lg:sticky lg:top-4 lg:col-span-4">
              <h2 className="pixel mb-3 text-xs text-muted">Team averages</h2>
              <ol className="grid gap-2">
                {board.teams.map((t, index) => {
                  const topAvg = Math.max(1, board.teams[0]?.average ?? 1)
                  const medal = rankTone(index + 1)
                  return (
                    <li key={t.team} className="sb-row">
                      <span className="sb-bar" style={{ transform: `scaleX(${t.average / topAvg})` }} aria-hidden />
                      <span className="sb-rank" style={{ color: medal.color, borderColor: medal.border }}>
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => setTeam(t.team)}
                        title={`Filter board to ${t.team}`}
                      >
                        <span className="block truncate font-bold text-ink hover:underline">{t.team}</span>
                        <span className="block text-xs text-muted">{t.players} player{t.players === 1 ? '' : 's'}</span>
                      </button>
                      <span className="sb-score" style={{ color: index === 0 ? '#ffd60a' : 'var(--ink)' }}>
                        {formatScore(t.average)}
                      </span>
                    </li>
                  )
                })}
              </ol>
            </aside>
          )}
        </div>
      )}
    </div>
  )
}

/** Standalone route wrapper at `/q/:slug/scores`. */
export default function HighScoresPage() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const [info, setInfo] = useState<QuizInfo | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api<QuizInfo>(`/api/quiz/${slug}`)
      .then((data) => {
        applyBranding(data.branding)
        setInfo(data)
      })
      .catch((caught: Error) => setError(caught.message))
  }, [slug])

  if (error) {
    return (
      <FullScreenMessage title="Could not load high scores" action={<a className="btn btn-primary" href={`/q/${slug}`}>Back to quiz</a>}>
        {error}
      </FullScreenMessage>
    )
  }
  if (!info) return <Stage className="grid place-items-center"><Spinner label="Loading high scores" /></Stage>
  if (!info.player) {
    return (
      <FullScreenMessage title="Join to see high scores" action={<a className="btn btn-primary" href={`/q/${slug}`}>Open quiz</a>}>
        Enter your name on the quiz page first to view the leaderboard.
      </FullScreenMessage>
    )
  }
  if (!info.quiz.leaderboard) {
    return (
      <FullScreenMessage title="High scores are off" action={<a className="btn btn-primary" href={`/q/${slug}`}>Back to quiz</a>}>
        The organiser has switched off the leaderboard for this quiz.
      </FullScreenMessage>
    )
  }

  return (
    <Stage>
      <HighScoresScreen
        slug={slug}
        quizTitle={info.quiz.title}
        branding={info.branding}
        initialMode={info.quiz.defaultMode}
        modes={info.quiz.modes}
        teams={info.quiz.teams}
        bestByMode={info.player.best}
        backLabel="Quiz"
        onBack={() => navigate(`/q/${slug}`)}
      />
    </Stage>
  )
}
