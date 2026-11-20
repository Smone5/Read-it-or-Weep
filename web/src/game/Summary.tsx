// End of a game: the score, what to re-read, and a reason to play again.

import confetti from 'canvas-confetti'
import { useEffect, useRef, useState } from 'react'
import { NowPlaying } from '../components/NowPlaying'
import { api } from '../lib/api'
import { play } from '../lib/sound'
import type { Leaderboard, Summary as SummaryData } from '../lib/types'
import { HighScores, HostBox, ReadinessMap, type TalkControl } from './parts'

type Props = {
  slug: string
  summary: SummaryData
  hostName: string
  hostLine: string
  hostSpeaking: boolean
  talk?: TalkControl | null
  showLeaderboard: boolean
  onPlayAgain: () => void
  /** Back to the lobby: another style, decade or host, the readiness map, the high scores. */
  onMainMenu: () => void
}

function minutes(seconds: number | null): string {
  if (!seconds) return '–'
  const m = Math.floor(seconds / 60)
  return `${m}:${String(seconds % 60).padStart(2, '0')}`
}

export default function Summary({ slug, summary, hostName, hostLine, hostSpeaking, talk, showLeaderboard, onPlayAgain, onMainMenu }: Props) {
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [stars, setStars] = useState(0)
  const [thanked, setThanked] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const celebrate = summary.personalBest && summary.score > 0 && summary.status === 'completed'
  const failed = summary.mode === 'straight' && summary.passed === false
  const jukebox = summary.mode === 'jukebox' // the music side game: no report to be ready for

  useEffect(() => {
    play(celebrate ? 'cheer' : 'gameover')
    if (celebrate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const colors = [getComputedStyle(document.documentElement).getPropertyValue('--brand').trim() || '#6d5ef8', '#ffb020', '#ffffff']
      void confetti({ particleCount: 110, spread: 75, origin: { y: 0.35 }, colors, shapes: ['square'], scalar: 0.9 })
    }
    if (showLeaderboard) {
      api<Leaderboard>(`/api/quiz/${slug}/leaderboard?mode=${summary.mode}`).then(setBoard).catch(() => undefined)
    }
  }, [celebrate, showLeaderboard, slug, summary.mode])

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
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 pb-6">
      <header className="pt-4 text-center">
        <p className="pixel blink text-[10px] text-muted">{summary.status === 'completed' ? 'Game over' : 'Game ended early'}</p>
        <h1 className="neon mt-3 text-2xl sm:text-4xl">{String(summary.score).padStart(6, '0')}</h1>
        {celebrate && <p className="pixel neon-accent mt-3 text-[11px]">New high score!</p>}
        {summary.mode === 'straight' ? (
          <p className="pixel mt-3 text-xs" style={{ color: failed ? 'var(--bad)' : 'var(--good)' }}>
            {failed ? `Not yet – pass mark ${summary.passMark}%` : `Passed · ${summary.accuracy}%`}
          </p>
        ) : (
          summary.badge && <p className="chip chip-accent mt-4">{summary.badge}</p>
        )}
      </header>

      <HostBox name={hostName} line={hostLine} speaking={hostSpeaking} talk={talk} />

      {/* Appears when the results-screen playlist starts, once the host has signed off. */}
      <NowPlaying decade={summary.afterParty?.decade} />

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
          <h2 className="neon-accent text-xs">Worth a re-read</h2>
          <ul className="mt-3 grid gap-1.5">
            {reread.slice(0, 6).map((section) => (
              <li key={section.id} className="flex gap-2">
                <span aria-hidden style={{ color: 'var(--accent)' }}>▸</span>
                <span>{section.heading}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {summary.review.length > 0 && (
        <section className="card p-4">
          <button className="flex w-full items-center justify-between gap-3 text-left" onClick={() => setReviewOpen(!reviewOpen)} aria-expanded={reviewOpen}>
            <h2 className="text-xs">The ones that got away ({summary.review.length})</h2>
            <span className="pixel text-xs" aria-hidden>{reviewOpen ? '−' : '+'}</span>
          </button>
          {reviewOpen && (
            <ol className="mt-4 grid gap-4">
              {summary.review.map((miss, index) => (
                <li key={index} className="border-l-4 pl-3" style={{ borderColor: 'var(--bad)' }}>
                  {miss.section && <p className="pixel text-[8px] text-muted">{miss.section}</p>}
                  <p className="mt-1">{miss.text}</p>
                  <p className="mt-1 text-[15px]">
                    <span style={{ color: 'var(--good)' }}>✔ {miss.correctAnswer}</span>
                    {miss.yourAnswer && <span className="ml-3 text-muted">✘ {miss.yourAnswer}</span>}
                  </p>
                  {miss.explanation && <p className="mt-1 text-[15px] text-muted">{miss.explanation}</p>}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {board && (
        <section className="card p-4">
          <h2 className="neon mb-3 text-xs">High scores · {board.modeName}</h2>
          <HighScores board={board} />
        </section>
      )}

      <section className="card flex flex-col items-center gap-2 p-4 text-center">
        <p className="pixel text-[9px] text-muted">{thanked ? 'Thanks – noted' : 'Rate this game'}</p>
        <div className="flex gap-1" role="radiogroup" aria-label="Rate this game out of five">
          {[1, 2, 3, 4, 5].map((value) => (
            <button key={value} role="radio" aria-checked={stars === value} aria-label={`${value} star${value === 1 ? '' : 's'}`} className="px-1.5 text-3xl leading-none" style={{ color: value <= stars ? 'var(--accent)' : 'color-mix(in oklab, var(--ink) 25%, transparent)' }} onClick={() => rate(value)}>
              ★
            </button>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap gap-3">
        <button className="btn btn-primary btn-lg flex-1" onClick={onPlayAgain}>Play again ▶</button>
        <button className="btn btn-ghost btn-lg flex-1" onClick={onMainMenu}>Main menu</button>
      </div>
    </div>
  )
}
