import { useState, type FormEvent } from 'react'
import { ErrorNote } from '../components/ui'
import { call, errorText } from './api'
import { Badge, ConfirmDialog, Dialog, DialogActions, ErrorBlock, Field, LoadingBlock, PageHeader } from './components'
import { useNotify, usePageTitle, useSession } from './context'
import { formatDate, timeAgo } from './format'
import { useLoad } from './hooks'
import type { AdminRow } from './types'

const PASSWORD_MIN = 10 // the server's rule (NewAdmin in admin.py)

export default function AdminsPage() {
  usePageTitle('Admins')
  const { admin: me } = useSession()
  const notify = useNotify()
  const { data, error, loading, reload, refresh } = useLoad<{ admins: AdminRow[] }>('/admins')
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<AdminRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)

  const remove = async () => {
    if (!removing) return
    setBusy(true)
    setRemoveError(null)
    try {
      await call(`/admins/${removing.id}`, { method: 'DELETE' })
      notify(`${removing.email} can no longer sign in.`)
      setRemoving(null)
      await refresh().catch(() => {})
    } catch (reason) {
      setRemoveError(errorText(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Admins"
        description="People who can sign in here. Every admin can do everything: change quizzes, see results and add or remove other admins."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            Add an admin
          </button>
        }
      />

      {!data && loading && <LoadingBlock label="Loading the admins" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}
      {data && (
        <div className="ad-table-wrap">
          <table className="ad-table" style={{ minWidth: 620 }}>
            <thead>
              <tr>
                <th scope="col">Admin</th>
                <th scope="col">Added</th>
                <th scope="col">Last signed in</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.admins.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    <div className="font-semibold">
                      {row.name || row.email} {row.id === me.id && <Badge tone="brand">You</Badge>}
                    </div>
                    {row.name && <div className="text-muted [overflow-wrap:anywhere]">{row.email}</div>}
                  </th>
                  <td className="whitespace-nowrap">{formatDate(row.created_at)}</td>
                  <td className="whitespace-nowrap">{timeAgo(row.last_login_at, 'Never')}</td>
                  <td className="num">
                    {row.id === me.id ? (
                      <span className="text-xs text-muted">You cannot remove yourself</span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => {
                          setRemoveError(null)
                          setRemoving(row)
                        }}
                      >
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {adding && (
        <AddAdminDialog
          taken={data?.admins.map((row) => row.email.toLowerCase()) ?? []}
          onClose={() => setAdding(false)}
          onAdded={(email) => {
            setAdding(false)
            notify(`${email} can now sign in.`)
            refresh().catch(() => {})
          }}
        />
      )}

      <ConfirmDialog
        open={removing !== null}
        title="Remove this admin?"
        confirmLabel="Remove admin"
        busyLabel="Removing…"
        busy={busy}
        error={removeError}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>
          <strong>{removing?.name || removing?.email}</strong>
          {removing?.name ? ` (${removing.email})` : ''} will no longer be able to sign in. Quizzes they created are kept.
        </p>
      </ConfirmDialog>
    </>
  )
}

function AddAdminDialog({ taken, onClose, onAdded }: { taken: string[]; onClose: () => void; onAdded: (email: string) => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const duplicate = taken.includes(email.trim().toLowerCase())
  const tooShort = password.length > 0 && password.length < PASSWORD_MIN

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (duplicate) {
      setError('There is already an admin with that email.')
      return
    }
    if (password.length < PASSWORD_MIN) {
      setError(`The password needs at least ${PASSWORD_MIN} characters.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await call('/admins', { method: 'POST', body: { email: email.trim(), name: name.trim() || null, password } })
      onAdded(email.trim().toLowerCase())
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={busy ? () => {} : onClose} title="Add an admin">
      <form className="grid gap-4" onSubmit={submit}>
        <Field label="Email" hint="They sign in with this." error={duplicate ? 'There is already an admin with that email.' : null}>
          {(props) => (
            <input className="field" type="email" {...props} value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" required />
          )}
        </Field>
        <Field label="Name (optional)">
          {(props) => <input className="field" {...props} value={name} onChange={(event) => setName(event.target.value)} maxLength={80} autoComplete="off" />}
        </Field>
        <Field
          label="Password"
          hint={`At least ${PASSWORD_MIN} characters. It is not emailed to them, so pass it on yourself, somewhere safe.`}
          error={tooShort ? `${PASSWORD_MIN - password.length} more character${PASSWORD_MIN - password.length === 1 ? '' : 's'} needed.` : null}
        >
          {(props) => (
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <input
                  className="field"
                  type={reveal ? 'text' : 'password'}
                  {...props}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  maxLength={200}
                  autoComplete="new-password"
                  required
                />
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setReveal((shown) => !shown)}
                aria-label={reveal ? 'Hide the password' : 'Show the password'}
              >
                {reveal ? 'Hide' : 'Show'}
              </button>
            </div>
          )}
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Adding…' : 'Add admin'}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
