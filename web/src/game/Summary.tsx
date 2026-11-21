// End of a game: calm, lobby-matched two-card layout — hero verdict & replay actions up front,
// tabbed debrief (readiness, missed questions, standings), on-demand share modal, quiet footer.

import confetti from 'canvas-confetti'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  IconBolt,
  IconBook,
  IconBulb,
  IconCheck,
  IconClose,
  IconCrown,
  IconDisc,
  IconFlag,
  IconPlay,
  IconStar,
  IconTrophy,
} from '../components/Icons'
import { NowPlaying } from '../components/NowPlaying'
import { Meter, Modal, SoundToggle } from '../components/ui'
import { api } from '../lib/api'
import {
  buildShareText,
  renderCertificatePng,
  renderTrophyCardPng,
  shareOrDownloadImage,
  type Challenge,
} from '../lib/shareCard'
import { play } from '../lib/sound'
import type { BadgeInfo, Branding, Leaderboard, Summary as SummaryData } from '../lib/types'
import { HighScores, HostBox, RollingScore, type TalkControl } from './parts'
import { VideoClipCard } from './VideoClipCard'

function BadgeIcon({ icon, tone }: { icon?: BadgeInfo['icon']; tone?: BadgeInfo['tone'] }) {
  const color = tone === 'legend' ? 'var(--accent-text)' : 'var(--medal)'
  const props = { size: 17, style: { color } }
  switch (icon) {
    case 'crown': return <IconCrown {...props} />
    case 'star': return <IconStar {...props} />
    case 'book': return <IconBook {...props} />
    case 'bolt': return <IconBolt {...props} />
    case 'bulb': return <IconBulb {...props} />
    case 'disc': return <IconDisc {...props} />
    case 'flag': return <IconFlag {...props} />
    default: return <IconTrophy {...props} />
  }
}

type DebriefTab = 'readiness' | 'missed' | 'standings'

type Props = {
  slug: string
  quizTitle: string
  branding: Branding
  playerName?: string | null
  playerTeam?: string | null
  challenge?: Challenge | null
  summary: SummaryData
  hostName: string
  hostLine: string
  hostSpeaking: boolean
  hostTone?: 'accent' | 'brand' | 'cyan' | 'rose'
  talk?: TalkControl | null
  showLeaderboard: boolean
  shareable?: boolean
  alreadyCelebrated?: boolean
  onCelebrated?: () => void
  reportUrl?: string | null
  reportFiles?: { id: string; filename: string; url: string }[]
  onVideoStart?: () => void
  onOpenScores?: () => void
  onPlayReport?: () => void
  onPlayAgain: () => void
  /** Back to the lobby: another style, decade or host, the readiness map, the high scores. */
  onMainMenu: () => void
}

function minutes(seconds: number | null): string {
  if (!seconds) return '–'
  const m = Math.floor(seconds / 60)
  return `${m}:${String(seconds % 60).padStart(2, '0')}`
}

export default function Summary({
  slug,
  quizTitle,
  branding,
  playerName,
  playerTeam,
  challenge,
  summary,
  hostName,
  hostLine,
  hostSpeaking,
  hostTone = 'accent',
  talk,
  showLeaderboard,
  shareable = true,
  alreadyCelebrated = false,
  onCelebrated,
  reportUrl,
  reportFiles,
  onVideoStart,
  onOpenScores,
  onPlayReport,
  onPlayAgain,
  onMainMenu,
}: Props) {
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [stars, setStars] = useState(0)
  const [thanked, setThanked] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [showAllReview, setShowAllReview] = useState(false)
  const [shareNote, setShareNote] = useState('')
  const [exporting, setExporting] = useState<'card' | 'cert' | null>(null)

  const uid = useId()
  const tabRefs = useRef<Partial<Record<DebriefTab, HTMLButtonElement | null>>>({})

  const matchingChallenge =
    challenge && (!challenge.mode || challenge.mode === summary.mode) ? challenge : null
  const isCompleted = summary.status === 'completed'
  const answeredAny = summary.report.answered + summary.music.answered > 0
  const quitEmpty = !isCompleted && !answeredAny
  const isNewBest = Boolean(summary.personalBest && summary.score > 0 && isCompleted)
  const beatChallenge = Boolean(matchingChallenge && isCompleted && summary.score > matchingChallenge.score)
  const failed = summary.mode === 'straight' && summary.passed === false
  const jukebox = summary.mode === 'jukebox'
  const completed = isCompleted && !failed
  const tier = summary.badgeInfo?.tier ?? 0
  const victory =
    beatChallenge ||
    (summary.mode === 'straight' && summary.passed === true) ||
    (isCompleted && tier >= 5) ||
    (isNewBest && tier >= 3) ||
    (!jukebox && summary.readiness.ready && completed)
  const shouldConfetti =
    beatChallenge ||
    (summary.mode === 'straight' && summary.passed === true) ||
    (isCompleted && tier >= 5) ||
    (isNewBest && tier >= 3)
  const previousBest = summary.previousBest
  const bestDelta = previousBest !== null && previousBest !== undefined ? summary.score - previousBest : null
  const canCertificate = !jukebox && completed && (summary.readiness.ready || summary.passed === true)

  const tabs = useMemo(() => {
    if (quitEmpty) return []
    const list: { id: DebriefTab; label: string; badge: string; tone?: string }[] = []
    if (!jukebox && summary.readiness.total > 0) {
      list.push({
        id: 'readiness',
        label: 'Overall readiness',
        badge: summary.readiness.ready ? 'Ready' : `${summary.readiness.cleared}/${summary.readiness.total}`,
        tone: summary.readiness.ready ? 'var(--good)' : undefined,
      })
    }
    if (summary.review.length > 0) {
      list.push({
        id: 'missed',
        label: 'Missed',
        badge: String(summary.review.length),
      })
    }
    if (board) {
      list.push({
        id: 'standings',
        label: 'Standings',
        badge: summary.rank ? `#${summary.rank}` : String(board.players),
      })
    }
    return list
  }, [quitEmpty, jukebox, summary.readiness.total, summary.readiness.ready, summary.readiness.cleared, summary.review.length, board, summary.rank])

  const [activeTab, setActiveTab] = useState<DebriefTab>(() =>
    !jukebox && summary.readiness.total > 0 ? 'readiness' : summary.review.length > 0 ? 'missed' : 'standings',
  )

  useEffect(() => {
    if (tabs.length > 0 && !tabs.some((t) => t.id === activeTab)) {
      setActiveTab(tabs[0].id)
    }
  }, [tabs, activeTab])

  const selectTab = (id: DebriefTab, focus = false) => {
    setActiveTab(id)
    const node = tabRefs.current[id]
    if (node) {
      if (focus) node.focus()
      node.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }
  }

  const onTabKeyDown = (event: React.KeyboardEvent, index: number) => {
    if (tabs.length < 2) return
    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = tabs.length - 1
    if (nextIndex !== null) {
      event.preventDefault()
      selectTab(tabs[nextIndex].id, true)
    }
  }

  const shareText = buildShareText({
    slug,
    quizTitle,
    gameTitle: branding.gameTitle,
    playerName,
    summary,
    includeLink: shareable,
  })

  const flashNote = (msg: string) => {
    setShareNote(msg)
    window.setTimeout(() => setShareNote((now) => (now === msg ? '' : now)), 3500)
  }

  const copyScore = async () => {
    try {
      await navigator.clipboard.writeText(shareText)
      flashNote('Copied! Paste into Slack, Teams, or a group text.')
    } catch {
      if (navigator.share) {
        try {
          await navigator.share({ title: `${branding.gameTitle} — ${quizTitle}`, text: shareText })
          flashNote('Shared!')
          return
        } catch {
          /* user cancelled */
        }
      }
      flashNote('Could not access clipboard — try saving the trophy card instead.')
    }
  }

  const nativeShare = async () => {
    if (!navigator.share) return copyScore()
    try {
      await navigator.share({ title: `${branding.gameTitle} — ${quizTitle}`, text: shareText })
      flashNote('Score shared!')
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') await copyScore()
    }
  }

  const saveTrophyCard = async () => {
    if (exporting) return
    setExporting('card')
    try {
      const blob = await renderTrophyCardPng({ branding, quizTitle, playerName, team: playerTeam, summary })
      const outcome = await shareOrDownloadImage(
        blob,
        `${slug}-score-${summary.score}.png`,
        `${branding.gameTitle} — ${summary.score.toLocaleString()} pts`,
        shareText,
      )
      if (outcome === 'shared') flashNote('Trophy card shared!')
      else if (outcome === 'downloaded') flashNote('Trophy card PNG saved!')
    } catch {
      flashNote('Could not generate trophy card.')
    } finally {
      setExporting(null)
    }
  }

  const saveCertificate = async () => {
    if (exporting) return
    setExporting('cert')
    try {
      const blob = await renderCertificatePng({ branding, quizTitle, playerName, team: playerTeam, summary })
      const outcome = await shareOrDownloadImage(
        blob,
        `${slug}-ready-certificate.png`,
        `${branding.gameTitle} — Certificate of Readiness`,
        `${playerName ? `${playerName} completed ` : 'Completed '}${quizTitle}`,
      )
      if (outcome === 'shared') flashNote('Certificate shared!')
      else if (outcome === 'downloaded') flashNote('Completion certificate PNG saved!')
    } catch {
      flashNote('Could not generate certificate.')
    } finally {
      setExporting(null)
    }
  }

  const onCelebratedRef = useRef(onCelebrated)
  onCelebratedRef.current = onCelebrated

  useEffect(() => {
    if (!alreadyCelebrated) {
      onCelebratedRef.current?.()
      if (isCompleted) {
        play(victory ? 'cheer' : completed ? 'levelup' : 'gameover')
        if (shouldConfetti && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
          const colors = [getComputedStyle(document.documentElement).getPropertyValue('--brand').trim() || '#6d5ef8', '#ffb020', '#ffffff']
          void confetti({ particleCount: 110, spread: 75, origin: { y: 0.32 }, colors, shapes: ['square'], scalar: 0.9 })
        }
      }
    }
    if (showLeaderboard) {
      api<Leaderboard>(`/api/quiz/${slug}/leaderboard?mode=${summary.mode}`).then(setBoard).catch(() => undefined)
    }
  }, [alreadyCelebrated, isCompleted, shouldConfetti, victory, completed, showLeaderboard, slug, summary.mode])

  // The server keeps one rating per game. Ratings are sent sequentially so the last tap wins.
  const rating = useRef<Promise<unknown>>(Promise.resolve())
  const rate = (value: number) => {
    setStars(value)
    rating.current = rating.current
      .then(() => api('/api/feedback', { body: { runId: summary.runId, stars: value }, retry: 3 }))
      .then(() => setThanked(true))
      .catch(() => undefined)
  }

  // Unified readiness lists: prioritize tried (!) and unplayed (·) sections; collapse cleared (✓) in a native <details>.
  const sectionsToReview = useMemo(() => {
    const tried = summary.readiness.sections.filter((s) => s.state === 'tried')
    const fresh = summary.readiness.sections.filter((s) => s.state === 'new')
    return [...tried, ...fresh]
  }, [summary.readiness.sections])
  const clearedSections = useMemo(
    () => summary.readiness.sections.filter((s) => s.state === 'cleared'),
    [summary.readiness.sections],
  )
  const sectionsNeeded = Math.max(
    1,
    Math.ceil((summary.readiness.threshold / 100) * summary.readiness.total) - summary.readiness.cleared,
  )

  // Single highest-leverage hook line below the badge (prevents stacked metadata clutter).
  let hookNode: React.ReactNode = null
  if (quitEmpty) {
    hookNode = (
      <span className="text-muted">
        This run wasn&rsquo;t counted — your previous progress on this report is unchanged.
      </span>
    )
  } else if (matchingChallenge && isCompleted) {
    hookNode = beatChallenge ? (
      <span className="font-semibold" style={{ color: 'var(--good)' }}>
        Beat {matchingChallenge.by} by +{(summary.score - matchingChallenge.score).toLocaleString()} pts!
      </span>
    ) : summary.score === matchingChallenge.score ? (
      <span className="font-semibold" style={{ color: 'var(--accent-text)' }}>
        Tied {matchingChallenge.by} at {matchingChallenge.score.toLocaleString()} pts — run it back!
      </span>
    ) : (
      <span className="text-muted">
        Only <b style={{ color: 'var(--accent-text)' }}>{(matchingChallenge.score - summary.score).toLocaleString()} pts</b> shy of {matchingChallenge.by} ({matchingChallenge.score.toLocaleString()})
      </span>
    )
  } else if (isNewBest) {
    hookNode = (
      <span className="font-semibold" style={{ color: 'var(--accent-text)' }}>
        New high score{bestDelta !== null && bestDelta > 0 ? ` · +${bestDelta.toLocaleString()} over your best` : '!'}
      </span>
    )
  } else if (bestDelta !== null && previousBest !== null && bestDelta < 0) {
    hookNode = (
      <span className="text-muted">
        Only <b style={{ color: 'var(--accent-text)' }}>{Math.abs(bestDelta).toLocaleString()} pts</b> off your best ({previousBest.toLocaleString()})
      </span>
    )
  } else if (jukebox) {
    hookNode = (
      <span className="text-muted">
        {summary.accuracy >= 70 ? 'Ear of a pro — take that momentum into the report.' : 'Warmed up — ready to take on the report.'}
      </span>
    )
  } else if (summary.badgeInfo?.nextHint) {
    hookNode = (
      <span
        style={{
          color:
            tier === 0
              ? 'var(--muted)'
              : summary.badgeInfo.nextLabel
                ? 'var(--accent-text)'
                : 'var(--good)',
        }}
      >
        {summary.badgeInfo.nextLabel ? `Next rank: ${summary.badgeInfo.nextHint}` : summary.badgeInfo.nextHint}
      </span>
    )
  }

  // Mode-tailored 4-metric strip (zero duplication with Standings).
  const stats: { label: string; value: string }[] = [
    jukebox
      ? { label: 'Music', value: `${summary.music.correct}/${summary.music.answered}` }
      : summary.mode === 'sudden_death'
        ? { label: 'Survived', value: String(summary.report.correct) }
        : summary.mode === 'speed'
          ? { label: 'Right', value: `${summary.report.correct}/${summary.report.answered}` }
          : { label: 'Report', value: `${summary.report.correct}/${summary.report.answered}` },
    summary.mode === 'straight'
      ? { label: 'Pass mark', value: `${summary.passMark}%` }
      : { label: 'Accuracy', value: `${summary.accuracy}%` },
    { label: 'Best streak', value: `x${summary.streakMax}` },
    summary.rank
      ? { label: 'Rank', value: `#${summary.rank}/${summary.players}` }
      : !jukebox && summary.music.answered > 0
        ? { label: 'Music', value: `${summary.music.correct}/${summary.music.answered}` }
        : { label: 'Time', value: minutes(summary.durationSec) },
  ]

  const primaryCtaLabel = !isCompleted
    ? 'Start fresh run'
    : jukebox && onPlayReport
      ? 'Take on the report'
      : failed
        ? 'Try again'
        : 'Play again'

  const shareCtaLabel = beatChallenge
    ? 'Send score back'
    : canCertificate
      ? 'Share & certificate'
      : 'Share & trophy'

  const kickerLabel = !isCompleted ? 'Game ended early' : failed ? 'Not quite yet' : victory ? 'Stage clear' : 'Run complete'
  const kickerColor = !isCompleted ? 'var(--muted)' : failed ? 'var(--bad)' : victory ? 'var(--good)' : 'var(--muted)'

  return (
    <div className="mx-auto flex w-full max-w-xl flex-1 flex-col gap-4 pb-8 lg:max-w-2xl">
      {/* 0. Slim utility header (matches Lobby rhythm, brings SoundToggle to Results) */}
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 truncate text-xs text-muted">
          <span className="font-semibold text-ink">{quizTitle}</span>
          {' · '}
          {summary.modeName}
        </p>
        <SoundToggle />
      </div>

      {/* 1. Hero Result & Action Card — above the fold on mobile & desktop */}
      <section className="card rise p-5">
        <header className="text-center">
          <p className="text-xs font-bold tracking-[0.14em] uppercase" style={{ color: kickerColor }}>
            {kickerLabel}
          </p>

          {quitEmpty ? (
            <h1 className="mt-2 !normal-case text-2xl font-bold text-ink" style={{ fontFamily: 'var(--font-sans)' }}>
              No questions answered
            </h1>
          ) : summary.mode === 'straight' ? (
            <>
              <h1 className="neon mt-2 text-3xl tabular-nums sm:text-4xl">
                <span className="sr-only">{failed ? 'Quiz score: ' : 'Passed with '}</span>
                {summary.accuracy}%
              </h1>
              <p className="mt-2 text-sm font-bold" style={{ color: failed ? 'var(--bad)' : 'var(--good)' }}>
                {failed ? `Need ${summary.passMark}% to pass` : 'Passed!'}
              </p>
            </>
          ) : (
            <>
              <h1 className="neon mt-2 text-3xl tabular-nums sm:text-4xl">
                <span className="sr-only">Final score: </span>
                <RollingScore
                  value={summary.score}
                  from={alreadyCelebrated ? summary.score : 0}
                  format="comma"
                  durationMs={650}
                />
              </h1>
              {summary.badge && (
                <div className="mt-3 flex justify-center">
                  <span className="medal-badge zoom-in" data-tone={summary.badgeInfo?.tone ?? 'gold'}>
                    <BadgeIcon icon={summary.badgeInfo?.icon} tone={summary.badgeInfo?.tone} />
                    <span className="pixel text-[11px]">{summary.badgeInfo?.label ?? summary.badge}</span>
                  </span>
                </div>
              )}
            </>
          )}

          {hookNode && <p className="mx-auto mt-2.5 max-w-md text-sm leading-snug">{hookNode}</p>}

          {(summary.lifelineBonus ?? 0) > 0 && (
            <p className="mt-1.5 text-xs text-muted">
              Includes <b style={{ color: 'var(--accent-text)' }}>+{summary.lifelineBonus}</b> saved lifeline bonus
            </p>
          )}
        </header>

        {/* Recessed 4-metric strip (omitted when quitting before answering anything) */}
        {!quitEmpty && (
          <>
            <h2 className="sr-only">Run statistics</h2>
            <dl className="mt-4 grid grid-cols-2 gap-3 rounded-lg border border-line bg-surface-2/70 px-3 py-2.5 sm:grid-cols-4">
              {stats.map((stat) => (
                <div key={stat.label} className="text-center">
                  <dt className="text-[11px] font-bold tracking-wider text-muted uppercase">{stat.label}</dt>
                  <dd className="mt-0.5 text-base font-bold tabular-nums text-ink">{stat.value}</dd>
                </div>
              ))}
            </dl>
          </>
        )}

        {/* Primary CTA + Quiet Secondary Actions */}
        <div className="mt-4 flex flex-col gap-3">
          <button
            type="button"
            className="btn btn-primary btn-lg w-full"
            onClick={jukebox && onPlayReport && isCompleted ? onPlayReport : onPlayAgain}
          >
            {primaryCtaLabel} <IconPlay size={14} />
          </button>

          <div
            className={`grid gap-2 border-t border-line pt-3 ${
              isCompleted && jukebox && onPlayReport
                ? 'grid-cols-2 sm:grid-cols-3'
                : isCompleted
                  ? 'grid-cols-2'
                  : 'grid-cols-1'
            }`}
          >
            {isCompleted && jukebox && onPlayReport && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={onPlayAgain}>
                Spin again
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-sm" onClick={onMainMenu}>
              Main menu
            </button>
            {isCompleted && (
              <button
                type="button"
                className={`btn btn-sm ${beatChallenge ? 'btn-accent' : 'btn-ghost'} ${jukebox && onPlayReport ? 'col-span-2 sm:col-span-1' : ''}`}
                onClick={() => setShareOpen(true)}
              >
                <IconTrophy size={13} style={{ color: beatChallenge ? undefined : 'var(--accent-text)' }} />
                <span>{shareCtaLabel}</span>
              </button>
            )}
          </div>
        </div>
      </section>

      {/* 2. Host sign-off directly beneath Card 1 (omitted on silent quit-early runs with no host line) */}
      {(isCompleted || hostLine || hostSpeaking) && (
        <HostBox
          name={hostName}
          line={hostLine}
          speaking={hostSpeaking}
          talk={talk}
          tone={hostTone}
          mood={completed ? 'happy' : 'wince'}
          idleHint={`Tap the mic to talk to ${hostName} about your run.`}
        />
      )}

      {/* 3. Unified Tabbed Debrief Card (Readiness · Missed · Standings) */}
      {tabs.length > 0 && (
        <section className="card p-4" aria-label="Post-game debrief">
          <div className="summary-tabs" role="tablist" aria-label="Debrief sections">
            {tabs.map((tab, index) => {
              const selected = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  ref={(el) => { tabRefs.current[tab.id] = el }}
                  id={`${uid}-tab-${tab.id}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  className="summary-tab"
                  onClick={() => selectTab(tab.id)}
                  onKeyDown={(event) => onTabKeyDown(event, index)}
                >
                  <span>{tab.label}</span>
                  <span
                    className="rounded px-1.5 py-0.5 text-xs tabular-nums"
                    style={{
                      background: 'var(--pill-bg)',
                      color: tab.tone ?? (selected ? 'var(--accent-text)' : 'var(--muted)'),
                    }}
                  >
                    {tab.badge}
                  </span>
                </button>
              )
            })}
          </div>

          {/* Tab 1: Unified Report Readiness + Re-read priority + Report links */}
          {!jukebox && summary.readiness.total > 0 && (
            <div
              role="tabpanel"
              aria-labelledby={`${uid}-tab-readiness`}
              hidden={activeTab !== 'readiness'}
              tabIndex={activeTab === 'readiness' ? 0 : undefined}
            >
              <div className="pt-4">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: summary.readiness.ready ? 'var(--good)' : 'var(--ink)' }}>
                      {summary.readiness.ready
                        ? 'Ready for the meeting!'
                        : `Clear ${sectionsNeeded} more section${sectionsNeeded === 1 ? '' : 's'} to be ready`}
                    </p>
                    <p className="text-xs text-muted">Cumulative progress across all your runs on this report</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-xs font-semibold">
                    {reportUrl && (
                      <a className="underline hover:opacity-80" style={{ color: 'var(--accent-text)' }} href={reportUrl} target="_blank" rel="noopener noreferrer">
                        Read report ↗
                      </a>
                    )}
                    {(reportFiles ?? []).map((file) => (
                      <a key={file.id} className="underline hover:opacity-80" style={{ color: 'var(--accent-text)' }} href={file.url} target="_blank" rel="noopener noreferrer">
                        {reportFiles!.length === 1 && !reportUrl ? 'Open report ↗' : `${file.filename} ↗`}
                      </a>
                    ))}
                    {canCertificate && (
                      <button
                        type="button"
                        className="underline hover:opacity-80"
                        style={{ color: 'var(--good)' }}
                        disabled={exporting !== null}
                        onClick={() => void saveCertificate()}
                      >
                        {exporting === 'cert' ? 'Building…' : 'Certificate PNG'}
                      </button>
                    )}
                  </div>
                </div>

                <Meter
                  value={summary.readiness.cleared}
                  max={summary.readiness.total}
                  tone={summary.readiness.ready ? 'var(--good)' : 'var(--accent)'}
                  label="Report readiness"
                />
                {!shareOpen && shareNote && (
                  <p className="mt-2 text-xs font-medium" style={{ color: 'var(--good)' }} role="status" aria-live="polite">
                    {shareNote}
                  </p>
                )}

                {sectionsToReview.length > 0 && (
                  <div className="mt-4">
                    <p className="text-xs font-bold tracking-wider text-muted uppercase">Worth a re-read</p>
                    <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
                      {sectionsToReview.map((section) => (
                        <li key={section.id} className="flex items-start gap-2 text-sm">
                          <span
                            aria-hidden
                            className="mt-0.5 grid h-5 w-5 flex-none place-items-center rounded-sm text-[11px] font-bold"
                            style={{
                              background: section.state === 'tried' ? 'var(--accent)' : 'color-mix(in oklab, var(--ink) 16%, transparent)',
                              color: section.state === 'tried' ? 'var(--accent-ink)' : 'var(--muted)',
                            }}
                          >
                            {section.state === 'tried' ? '!' : '·'}
                          </span>
                          <span className={section.state === 'new' ? 'text-muted' : ''}>{section.heading}</span>
                          <span className="sr-only">
                            {section.state === 'tried' ? ' – tried, not cleared yet' : ' – not played yet'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {clearedSections.length > 0 && (
                  <details className="summary-disclosure mt-3">
                    <summary>
                      {sectionsToReview.length === 0
                        ? `All ${clearedSections.length} sections cleared`
                        : `Cleared sections (${clearedSections.length})`}
                    </summary>
                    <ul className="grid gap-1.5 pb-1 pt-1 sm:grid-cols-2">
                      {clearedSections.map((section) => (
                        <li key={section.id} className="flex items-start gap-2 text-sm">
                          <span
                            aria-hidden
                            className="mt-0.5 grid h-5 w-5 flex-none place-items-center rounded-sm"
                            style={{ background: 'var(--good)', color: 'var(--good-ink)' }}
                          >
                            <IconCheck size={12} />
                          </span>
                          <span>{section.heading}</span>
                          <span className="sr-only"> – cleared</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </div>
          )}

          {/* Tab 2: Missed Questions Debrief */}
          {summary.review.length > 0 && (
            <div
              role="tabpanel"
              aria-labelledby={`${uid}-tab-missed`}
              hidden={activeTab !== 'missed'}
              tabIndex={activeTab === 'missed' ? 0 : undefined}
            >
              <div className="pt-4">
                <ol className="grid gap-4">
                  {(showAllReview ? summary.review : summary.review.slice(0, 3)).map((miss, index) => (
                    <li key={index} className="border-l-4 pl-3" style={{ borderColor: 'var(--bad)' }}>
                      <div className="flex flex-wrap items-center gap-2">
                        {miss.section && <p className="text-xs font-bold tracking-wider text-muted uppercase">{miss.section}</p>}
                        {miss.slot === 'fact_fake' && <span className="chip text-[10px]">Lightning debrief</span>}
                      </div>
                      <p className="mt-1 font-medium">{miss.text}</p>
                      <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                        <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: 'var(--good)' }}>
                          <IconCheck size={14} /> {miss.correctAnswer}
                        </span>
                        {miss.yourAnswer && (
                          <span className="inline-flex items-center gap-1.5 text-muted">
                            <IconClose size={13} /> {miss.yourAnswer}
                          </span>
                        )}
                      </p>
                      {miss.explanation && <p className="mt-1 text-sm text-muted">{miss.explanation}</p>}
                      {miss.sourceQuote && (
                        <blockquote className="mt-1.5 border-l-2 pl-2.5 text-xs text-muted italic" style={{ borderColor: 'var(--line)' }}>
                          “{miss.sourceQuote}”
                        </blockquote>
                      )}
                      {miss.video && <VideoClipCard video={miss.video} compact onPlayStart={onVideoStart} />}
                    </li>
                  ))}
                </ol>
                {summary.review.length > 3 && (
                  <button type="button" className="btn btn-ghost btn-sm mt-4 w-full" onClick={() => setShowAllReview((v) => !v)}>
                    {showAllReview ? 'Show top 3' : `Show all ${summary.review.length} missed questions`}
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Tab 3: Compact Standings */}
          {board && (
            <div
              role="tabpanel"
              aria-labelledby={`${uid}-tab-standings`}
              hidden={activeTab !== 'standings'}
              tabIndex={activeTab === 'standings' ? 0 : undefined}
            >
              <div className="pt-4">
                <HighScores board={board} minimal onOpenFull={onOpenScores} />
              </div>
            </div>
          )}
        </section>
      )}

      {/* 4. Quiet Borderless Footer — 0 CLS when NowPlaying fades in below the debrief */}
      <footer className="flex flex-col gap-3">
        <NowPlaying decade={summary.afterParty?.decade} subtle />

        {isCompleted && (
          <fieldset className="flex flex-wrap items-center justify-between gap-2 px-1 py-1">
            <legend className="sr-only">Rate this game out of five stars</legend>
            <span className="text-xs font-semibold text-muted" role="status" aria-live="polite">
              {thanked ? 'Thanks — rating saved' : 'Enjoyed this run? Rate it'}
            </span>
            <div className="flex items-center">
              {[1, 2, 3, 4, 5].map((value) => {
                const active = value <= stars
                return (
                  <label
                    key={value}
                    className="grid h-11 w-11 cursor-pointer place-items-center rounded-[4px] transition-transform active:scale-90 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 motion-reduce:transition-none"
                    style={{
                      color: active ? 'var(--accent-text)' : 'color-mix(in oklab, var(--ink) 42%, transparent)',
                      outlineColor: 'var(--accent)',
                    }}
                  >
                    <input
                      type="radio"
                      name={`${uid}-rating`}
                      value={value}
                      checked={stars === value}
                      onChange={() => rate(value)}
                      className="sr-only"
                      aria-label={`${value} star${value === 1 ? '' : 's'}`}
                    />
                    <IconStar size={22} />
                  </label>
                )
              })}
            </div>
          </fieldset>
        )}
      </footer>

      {/* 5. On-demand Share, Trophy Card & Certificate Modal */}
      <Modal open={shareOpen} onClose={() => setShareOpen(false)}>
        <div className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="pixel text-[10px]" style={{ color: 'var(--accent-text)' }}>Show off your score</p>
              <h2 className="mt-1 !normal-case text-lg font-bold" style={{ fontFamily: 'var(--font-sans)' }}>
                {summary.mode === 'straight' ? `${summary.accuracy}%` : `${summary.score.toLocaleString()} pts`}
                {summary.badgeInfo?.label ? ` · ${summary.badgeInfo.label}` : ''}
              </h2>
            </div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShareOpen(false)}>Close</button>
          </div>
          {summary.resultsGrid && summary.resultsGrid.length > 0 && (
            <div
              className="flex flex-wrap items-center justify-center gap-1.5 rounded-lg border border-line bg-surface-2/70 p-3"
              aria-label={`Answer grid: ${summary.resultsGrid.filter(Boolean).length} of ${summary.resultsGrid.length} correct`}
            >
              {summary.resultsGrid.slice(0, 20).map((ok, i) => (
                <span
                  key={i}
                  className="grid h-5 w-5 place-items-center rounded-[3px] text-[10px] font-bold"
                  style={
                    ok
                      ? { background: 'var(--good)', color: 'var(--good-ink)', border: '1px solid var(--good)' }
                      : { background: 'transparent', color: 'var(--bad)', border: '2px solid var(--bad)' }
                  }
                  title={ok ? 'Correct' : 'Missed'}
                  aria-hidden
                >
                  {ok ? <IconCheck size={11} /> : <IconClose size={10} />}
                </span>
              ))}
            </div>
          )}

          <p className="text-xs text-muted">
            {shareable
              ? 'Spoiler-free — shares your score, rank, and challenge link only (never report text).'
              : 'Spoiler-free — shares your score only (no direct quiz link for private reports).'}
          </p>

          <div className="grid gap-2">
            <button type="button" className="btn btn-primary w-full" onClick={() => void copyScore()}>
              {shareable ? 'Copy score & challenge link' : 'Copy score'}
            </button>
            {typeof navigator !== 'undefined' && 'share' in navigator && (
              <button type="button" className="btn btn-ghost w-full" onClick={() => void nativeShare()}>
                Share via device…
              </button>
            )}
            <button type="button" className="btn btn-ghost w-full" disabled={exporting !== null} onClick={() => void saveTrophyCard()}>
              {exporting === 'card' ? 'Building card…' : 'Save trophy card (PNG)'}
            </button>
            {canCertificate && (
              <button type="button" className="btn btn-ghost w-full" disabled={exporting !== null} onClick={() => void saveCertificate()}>
                {exporting === 'cert' ? 'Building certificate…' : 'Download ready certificate (PNG)'}
              </button>
            )}
          </div>

          <p className="min-h-[1.25rem] text-center text-xs font-medium" style={{ color: 'var(--good)' }} role="status" aria-live="polite">
            {shareNote}
          </p>
        </div>
      </Modal>
    </div>
  )
}
