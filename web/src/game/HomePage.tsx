// The front door: pick a quiz, or type a live-game code.

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FullScreenMessage, Logo, Spinner, Stage, untilText } from '../components/ui'
import { api } from '../lib/api'
import { applyBranding } from '../lib/theme'
import type { Branding } from '../lib/types'

type Config = { branding: Branding; quizzes: { slug: string; title: string; meetingAt: string | null }[] }

export default function HomePage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState('')
  const [code, setCode] = useState('')

  useEffect(() => {
    api<Config>('/api/config', { as: 'none' })
      .then((data) => {
        applyBranding(data.branding)
        setConfig(data)
      })
      .catch((caught: Error) => setError(caught.message))
  }, [])

  if (error) return <FullScreenMessage title="We could not load the game">{error}</FullScreenMessage>
  if (!config) return <Stage className="grid place-items-center"><Spinner /></Stage>

  const { branding, quizzes } = config
  return (
    <Stage>
      <div className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-md flex-col justify-center gap-5">
        <header className="text-center">
          <div className="mb-4 flex justify-center"><Logo branding={branding} size={56} /></div>
          <p className="pixel text-[9px] text-muted">{branding.companyName}</p>
          <h1 className="neon mt-2 text-lg sm:text-2xl">{branding.gameTitle}</h1>
          <p className="mt-3 text-muted">{branding.tagline}</p>
        </header>

        <section className="card rise p-5">
          <h2 className="text-xs">Choose a quiz</h2>
          {quizzes.length === 0 ? (
            <p className="mt-3 text-muted">No quiz is open right now. Use the link in your invitation when one is.</p>
          ) : (
            <div className="mt-3 grid gap-2.5">
              {quizzes.map((quiz) => (
                <button key={quiz.slug} className="answer" style={{ ['--tone' as string]: 'var(--brand)' }} onClick={() => navigate(`/q/${quiz.slug}`)}>
                  <span className="badge pixel" aria-hidden>▶</span>
                  <span className="min-w-0 flex-1">
                    <span className="block">{quiz.title}</span>
                    {untilText(quiz.meetingAt) && <span className="block text-sm text-muted">Meeting {untilText(quiz.meetingAt)}</span>}
                  </span>
                </button>
              ))}
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
            <input id="live-code" className="field pixel flex-1 !text-base tracking-widest" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={6} autoCapitalize="characters" autoComplete="off" placeholder="ABCDE" />
            <button className="btn btn-accent" disabled={code.trim().length < 4}>Join</button>
          </div>
        </form>
      </div>
    </Stage>
  )
}
