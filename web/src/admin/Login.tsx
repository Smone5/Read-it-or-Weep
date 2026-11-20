import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ErrorNote } from '../components/ui'
import { adminApi, session } from '../lib/api'
import { errorText } from './api'
import { Field, Note } from './components'
import { usePageTitle } from './context'
import type { AdminUser } from './types'

export default function Login({ notice, onSignedIn }: { notice: string | null; onSignedIn: (admin: AdminUser) => void }) {
  usePageTitle('Sign in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      // The shared client is used directly here: a wrong password is an ordinary error, not an expired session.
      const result = await adminApi<{ token: string; admin: AdminUser }>('/login', {
        method: 'POST',
        body: { email: email.trim(), password },
      })
      session.setAdminToken(result.token)
      onSignedIn(result.admin)
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-5 text-center">
          <h1 className="text-2xl font-bold">Admin sign in</h1>
          <p className="mt-1 text-sm text-muted">Set up quizzes, check the questions and see who is ready.</p>
        </div>
        <form className="card grid gap-4 p-5" onSubmit={submit}>
          {notice && (
            <Note tone="info" role="status">
              {notice}
            </Note>
          )}
          <Field label="Email">
            {(props) => (
              <input
                className="field"
                type="email"
                autoComplete="username"
                required
                autoFocus
                {...props}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <Field label="Password">
            {(props) => (
              <input
                className="field"
                type="password"
                autoComplete="current-password"
                required
                {...props}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            )}
          </Field>
          <ErrorNote>{error}</ErrorNote>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm">
          <Link className="ad-link" to="/">
            Go to the game
          </Link>
        </p>
      </div>
    </main>
  )
}
