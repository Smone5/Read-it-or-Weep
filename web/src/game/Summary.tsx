// End of a game: the score, what to re-read, and a reason to play again.

import confetti from 'canvas-confetti'
import { useEffect, useRef, useState } from 'react'
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
import { api } from '../lib/api'
import { cueJukebox } from '../lib/jukebox'
import {
  buildShareText,
  downloadBlob,
  renderCertificatePng,
  renderTrophyCardPng,
  shareOrDownloadImage,
  type Challenge,
} from '../lib/shareCard'
import { play } from '../lib/sound'
import type { BadgeInfo, Branding, Leaderboard, Summary as SummaryData } from '../lib/types'
import { HighScores, HostBox, ReadinessMap, RollingScore, type TalkControl } from './parts'
import { VideoClipCard } from './VideoClipCard'

function BadgeIcon({ icon, tone }: { icon?: BadgeInfo['icon']; tone?: BadgeInfo['tone'] }) {
  const color = tone === 'legend' ? 'var(--accent)' : 'var(--medal)'
  const props = { size: 18, style: { color } }
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
  reportUrl?: string | null
  reportFiles?: { id: string; filename: string; url: string }[]
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
  reportUrl,
  reportFiles,
  onOpenScores,
  onPlayReport,
  onPlayAgain,
  onMainMenu,
}: Props) {
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [stars, setStars] = useState(0)
  const [thanked, setThanked] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(true)
  const [showAllReview, setShowAllReview] = useState(false)
  const [shareNote, setShareNote] = useState('')
  const [exporting, setExporting] = useState<'card' | 'cert' | null>(null)
  const beatChallenge = Boolean(challenge && summary.status === 'completed' && summary.score > challenge.score)
  const celebrate = (summary.personalBest && summary.score > 0 && summary.status === 'completed') || beatChallenge
  const failed = summary.mode === 'straight' && summary.passed === false
  const jukebox = summary.mode === 'jukebox' // the music side game: no report to be ready for
  const completed = summary.status === 'completed' && !failed
  const victory = celebrate || (summary.mode === 'straight' && summary.passed === true) || (!jukebox && summary.readiness.ready && completed)
  const previousBest = summary.previousBest
  const bestDelta = previousBest !== null && previousBest !== undefined ? summary.score - previousBest : null
  const canCertificate = !jukebox && completed && (summary.readiness.ready || summary.passed === true)

  const shareText = buildShareText({
    slug,
    quizTitle,
    gameTitle: branding.gameTitle,
    playerName,
    summary,
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
      flashNote(outcome === 'shared' ? 'Trophy card ready to share!' : 'Trophy card PNG saved!')
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
      downloadBlob(blob, `${slug}-ready-certificate.png`)
      flashNote('Completion certificate PNG saved!')
    } catch {
      flashNote('Could not generate certificate.')
    } finally {
      setExporting(null)
    }
  }

  useEffect(() => {
    play(victory ? 'cheer' : completed ? 'levelup' : 'gameover')
    if (victory) cueJukebox('victory')
    if (celebrate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const colors = [getComputedStyle(document.documentElement).getPropertyValue('--brand').trim() || '#6d5ef8', '#ffb020', '#ffffff']
      void confetti({ particleCount: 110, spread: 75, origin: { y: 0.35 }, colors, shapes: ['square'], scalar: 0.9 })
    }
    if (showLeaderboard) {
      api<Leaderboard>(`/api/quiz/${slug}/leaderboard?mode=${summary.mode}`).then(setBoard).catch(() => undefined)
    }
  }, [celebrate, victory, completed, showLeaderboard, slug, summary.mode])

  // The server keeps one rating per game. Ratings are sent one after another, so when a player
  // taps a different star, the last one they tapped is the one that is kept.
  const rating = useRef<Promise<unknown>>(Promise.resolve())
  const rate = (value: number) => {
    setStars(value)
    rating.current = rating.current
      .then(() => api('/api/feedback', { body: { runId: summary.runId, stars: value }, retry: 3 }))
      .then(() => setThanked(true))
      .catch(() => undefined)
  }

  const reread = [...summary.blindSpots, ...summary.untouched.filter((u) => !summary.blindSpots.some((b) => b.id === u.id))]

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 pb-6 lg:max-w-4xl">
      <header className="pt-3 text-center">
        <p className="pixel blink text-[10px] text-muted">
          {summary.status !== 'completed' ? 'Game ended early' : failed ? 'Not quite yet' : victory ? 'Stage clear' : 'Run complete'}
        </p>
        <h1 className="neon mt-3 text-2xl tabular-nums sm:text-4xl"><RollingScore value={summary.score} pad={6} durationMs={650} /></h1>
        {celebrate && <p className="pixel neon-accent mt-3 text-[11px]">New high score!</p>}
        {(summary.lifelineBonus ?? 0) > 0 && (
          <p className="mt-2 text-xs text-muted">
            Includes <b style={{ color: 'var(--accent)' }}>+{summary.lifelineBonus}</b> unused lifeline bonus ({summary.unusedLifelines} saved)
          </p>
        )}
        {bestDelta !== null && previousBest !== null && (
          <p className="mt-2 text-sm text-muted">
            {bestDelta > 0
              ? <>Beat your previous best by <b style={{ color: 'var(--good)' }}>+{bestDelta.toLocaleString()}</b>!</>
              : bestDelta === 0
                ? 'Tied your personal best!'
                : <>Only <b style={{ color: 'var(--accent)' }}>{Math.abs(bestDelta).toLocaleString()}</b> pts off your best ({previousBest.toLocaleString()})</>}
          </p>
        )}
        {summary.mode === 'straight' ? (
          <p className="pixel mt-3 text-xs" style={{ color: failed ? 'var(--bad)' : 'var(--good)' }}>
            {failed ? `Not yet – pass mark ${summary.passMark}%` : `Passed · ${summary.accuracy}%`}
          </p>
        ) : (
          summary.badge && (
            <div className="mt-4 flex flex-col items-center gap-2">
              <span className="medal-badge zoom-in" data-tone={summary.badgeInfo?.tone ?? 'gold'}>
                <BadgeIcon icon={summary.badgeInfo?.icon} tone={summary.badgeInfo?.tone} />
                <span className="pixel text-[11px]">{summary.badgeInfo?.label ?? summary.badge}</span>
              </span>
              {summary.badgeInfo?.description && (
                <p className="max-w-md text-sm text-muted">
                  {summary.badgeInfo.description}
                </p>
              )}
              {summary.badgeInfo?.nextHint && (
                <p className="text-xs" style={{ color: summary.badgeInfo.nextLabel ? 'var(--accent-text)' : 'var(--good)' }}>
                  {summary.badgeInfo.nextLabel ? `Next rank: ${summary.badgeInfo.nextHint}` : summary.badgeInfo.nextHint}
                </p>
              )}
            </div>
          )
        )}
      </header>

      <HostBox name={hostName} line={hostLine} speaking={hostSpeaking} talk={talk} tone={hostTone} mood={completed ? 'happy' : 'wince'} />

      <section className="card grid grid-cols-2 gap-x-4 gap-y-4 p-4 sm:grid-cols-4">
        {jukebox ? (
          <div className="hud">Music<b>{summary.music.correct}/{summary.music.answered}</b></div>
        ) : (
          <div className="hud">Report<b>{summary.report.correct}/{summary.report.answered}</b></div>
        )}
        <div className="hud">Accuracy<b>{summary.accuracy}%</b></div>
        {jukebox ? (
          <div className="hud">Time<b>{minutes(summary.durationSec)}</b></div>
        ) : summary.music.answered > 0 ? (
          <div className="hud">Music<b>{summary.music.correct}/{summary.music.answered}</b></div>
        ) : (
          <div className="hud">Time<b>{minutes(summary.durationSec)}</b></div>
        )}
        <div className="hud">Best streak<b>x{summary.streakMax}</b></div>
        {summary.rank && (
          <div className="hud col-span-2 sm:col-span-4">
            Your rank · {summary.modeName}<b>#{summary.rank} of {summary.players}</b>
          </div>
        )}
      </section>

      {challenge && summary.status === 'completed' && (
        <section
          className="card flex items-center gap-3 p-4"
          style={{
            borderColor: beatChallenge ? 'var(--good)' : 'var(--accent)',
            background: beatChallenge
              ? 'color-mix(in oklab, var(--good) 12%, var(--surface))'
              : 'color-mix(in oklab, var(--accent) 10%, var(--surface))',
          }}
        >
          <IconTrophy size={22} className="flex-none" style={{ color: beatChallenge ? 'var(--good)' : 'var(--accent)' }} />
          <div className="min-w-0 flex-1">
            <p className="pixel text-[10px]" style={{ color: beatChallenge ? 'var(--good)' : 'var(--accent-text)' }}>
              {beatChallenge
                ? `Challenge beaten!`
                : summary.score === challenge.score
                  ? 'Dead heat!'
                  : `Target: ${challenge.by} (${challenge.score.toLocaleString()} pts)`}
            </p>
            <p className="mt-1 text-sm">
              {beatChallenge
                ? <>You topped {challenge.by}&rsquo;s {challenge.score.toLocaleString()} pts by <b>+{(summary.score - challenge.score).toLocaleString()}</b>! Send your score back to claim the crown.</>
                : summary.score === challenge.score
                  ? <>You tied {challenge.by}&rsquo;s {challenge.score.toLocaleString()} pts on the nose. One more run to take the lead!</>
                  : <>Only <b>{(challenge.score - summary.score).toLocaleString()} pts</b> shy of {challenge.by}&rsquo;s score. Run it back!</>}
            </p>
          </div>
        </section>
      )}

      {summary.status === 'completed' && (
        <section className="card card-accent p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="neon-accent text-xs">Show off your score</h2>
              <p className="mt-1 text-xs text-muted">
                Spoiler-free — shares your score, rank, and challenge link only (never report text).
              </p>
            </div>
            {summary.resultsGrid && summary.resultsGrid.length > 0 && (
              <div className="flex flex-wrap gap-1" aria-label={`Answer grid: ${summary.resultsGrid.filter(Boolean).length} of ${summary.resultsGrid.length} correct`}>
                {summary.resultsGrid.slice(0, 20).map((ok, i) => (
                  <span
                    key={i}
                    className="inline-block h-3.5 w-3.5 rounded-[3px]"
                    style={{ background: ok ? 'var(--good)' : 'var(--bad)' }}
                    title={ok ? 'Correct' : 'Missed'}
                  />
                ))}
              </div>
            )}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void copyScore()}>
              Copy score &amp; challenge link
            </button>
            {typeof navigator !== 'undefined' && 'share' in navigator && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void nativeShare()}>
                Share…
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-sm" disabled={exporting !== null} onClick={() => void saveTrophyCard()}>
              {exporting === 'card' ? 'Building card…' : 'Save trophy card (PNG)'}
            </button>
            {canCertificate && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={exporting !== null} onClick={() => void saveCertificate()}>
                {exporting === 'cert' ? 'Building certificate…' : 'Download ready certificate (PNG)'}
              </button>
            )}
          </div>
          {shareNote && (
            <p className="mt-2 text-xs font-medium" style={{ color: 'var(--good)' }} role="status">
              {shareNote}
            </p>
          )}
        </section>
      )}

      {/* Two-column desktop debrief; clean vertical flow on mobile. */}
      <div className="grid items-start gap-4 lg:grid-cols-12">
        <div className="flex flex-col gap-4 lg:col-span-7">
          {jukebox ? (
            <section className="card card-accent p-4">
              <h2 className="neon-accent text-xs">Warmed up</h2>
              <p className="mt-2">
                {summary.accuracy >= 70
                  ? 'That is the ear of a pro. Now take the same confidence into the report: the main game is waiting.'
                  : 'Every jingle intern starts somewhere. You are warmed up now: take on the report in the main game.'}
              </p>
            </section>
          ) : (
            <section className="card p-4">
              <ReadinessMap readiness={summary.readiness} />
              {summary.readiness.ready ? (
                <p className="mt-3" style={{ color: 'var(--good)' }}>You have cleared enough of the report to be ready for the meeting.</p>
              ) : (
                <p className="mt-3 text-muted">
                  Clear {Math.max(1, Math.ceil((summary.readiness.threshold / 100) * summary.readiness.total) - summary.readiness.cleared)} more
                  {' '}section{Math.ceil((summary.readiness.threshold / 100) * summary.readiness.total) - summary.readiness.cleared === 1 ? '' : 's'} to be ready. Every new game asks different questions.
                </p>
              )}
            </section>
          )}

          {!jukebox && reread.length > 0 && (
            <section className="card card-accent p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="neon-accent text-xs">Worth a re-read</h2>
                {reportUrl && (
                  <a className="btn btn-ghost btn-sm" href={reportUrl} target="_blank" rel="noopener noreferrer">
                    Read the report ↗
                  </a>
                )}
              </div>
              <ul className="mt-3 grid gap-1.5">
                {reread.slice(0, 6).map((section) => (
                  <li key={section.id} className="flex items-start gap-2">
                    <IconPlay size={11} className="mt-1.5" style={{ color: 'var(--accent-text)' }} />
                    <span>{section.heading}</span>
                  </li>
                ))}
              </ul>
              {reportFiles && reportFiles.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {reportFiles.map((file) => (
                    <a key={file.id} className="btn btn-ghost btn-sm" href={file.url} target="_blank" rel="noopener noreferrer">
                      {reportFiles.length === 1 && !reportUrl ? 'Open report ↗' : `${file.filename} ↗`}
                    </a>
                  ))}
                </div>
              )}
            </section>
          )}

          {summary.review.length > 0 && (
            <section className="card p-4">
              <button className="flex min-h-[36px] w-full items-center justify-between gap-3 text-left" onClick={() => setReviewOpen(!reviewOpen)} aria-expanded={reviewOpen}>
                <h2 className="text-xs">The ones that got away ({summary.review.length})</h2>
                <span className="pixel text-xs" aria-hidden>{reviewOpen ? '−' : '+'}</span>
              </button>
              {reviewOpen && (
                <>
                  <ol className="mt-4 grid gap-4">
                    {(showAllReview ? summary.review : summary.review.slice(0, 3)).map((miss, index) => (
                      <li key={index} className="border-l-4 pl-3" style={{ borderColor: 'var(--bad)' }}>
                        <div className="flex flex-wrap items-center gap-2">
                          {miss.section && <p className="text-xs font-bold tracking-wider text-muted uppercase">{miss.section}</p>}
                          {miss.slot === 'fact_fake' && <span className="chip text-[10px]">Lightning debrief</span>}
                        </div>
                        <p className="mt-1">{miss.text}</p>
                        <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[15px]">
                          <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--good)' }}>
                            <IconCheck size={14} /> {miss.correctAnswer}
                          </span>
                          {miss.yourAnswer && (
                            <span className="inline-flex items-center gap-1.5 text-muted">
                              <IconClose size={13} /> {miss.yourAnswer}
                            </span>
                          )}
                        </p>
                        {miss.explanation && <p className="mt-1 text-[15px] text-muted">{miss.explanation}</p>}
                        {miss.sourceQuote && (
                          <blockquote className="mt-1.5 border-l-2 pl-2.5 text-sm text-muted italic" style={{ borderColor: 'var(--line)' }}>
                            “{miss.sourceQuote}”
                          </blockquote>
                        )}
                        {miss.video && <VideoClipCard video={miss.video} compact restoreLevel="party" />}
                      </li>
                    ))}
                  </ol>
                  {summary.review.length > 3 && (
                    <button type="button" className="btn btn-ghost btn-sm mt-4 w-full" onClick={() => setShowAllReview((v) => !v)}>
                      {showAllReview ? 'Show top 3' : `Show all ${summary.review.length} missed questions`}
                    </button>
                  )}
                </>
              )}
            </section>
          )}
        </div>

        <div className="flex flex-col gap-4 lg:col-span-5">
          {/* Appears when the results-screen playlist starts, once the host has signed off. */}
          <NowPlaying decade={summary.afterParty?.decade} />

          {board && (
            <section className="card p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="neon text-xs">High scores · {board.modeName}</h2>
                <span className="text-xs text-muted">{board.players} player{board.players === 1 ? '' : 's'}</span>
              </div>
              <HighScores board={board} compact onOpenFull={onOpenScores} />
            </section>
          )}

          <section className="card flex flex-col items-center gap-2 p-4 text-center">
            <p className="pixel text-[10px] text-muted">{thanked ? 'Thanks – noted' : 'Rate this game'}</p>
            <div className="flex gap-1" role="radiogroup" aria-label="Rate this game out of five">
              {[1, 2, 3, 4, 5].map((value) => (
                <button
                  key={value}
                  role="radio"
                  aria-checked={stars === value}
                  aria-label={`${value} star${value === 1 ? '' : 's'}`}
                  className="grid h-11 w-11 place-items-center rounded-[4px] transition-transform active:scale-90"
                  style={{ color: value <= stars ? 'var(--accent)' : 'color-mix(in oklab, var(--ink) 25%, transparent)' }}
                  onClick={() => rate(value)}
                >
                  <IconStar size={26} />
                </button>
              ))}
            </div>
          </section>

          <div className="flex flex-wrap gap-3">
            {jukebox && onPlayReport ? (
              <>
                <button className="btn btn-primary btn-lg flex-1" onClick={onPlayReport}>
                  Take on the report <IconPlay size={14} />
                </button>
                <button className="btn btn-ghost btn-lg flex-1" onClick={onPlayAgain}>Play Jukebox again</button>
              </>
            ) : (
              <>
                <button className="btn btn-primary btn-lg flex-1" onClick={onPlayAgain}>
                  Play again <IconPlay size={14} />
                </button>
                <button className="btn btn-ghost btn-lg flex-1" onClick={onMainMenu}>Main menu</button>
              </>
            )}
          </div>
          {jukebox && onPlayReport && (
            <button className="btn btn-ghost w-full" onClick={onMainMenu}>Main menu</button>
          )}
        </div>
      </div>
    </div>
  )
}
