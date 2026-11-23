import { useState, type FormEvent } from 'react'
import { ErrorNote } from '../components/ui'
import { call, errorText } from './api'
import { AuditLogFeed } from './AuditLogFeed'
import { Badge, ConfirmDialog, CopyButton, Dialog, DialogActions, ErrorBlock, Field, LoadingBlock, PageHeader } from './components'
import { useNotify, usePageTitle, useSession } from './context'
import { formatDate, timeAgo } from './format'
import { useLoad } from './hooks'
import type { AdminRow } from './types'

const PASSWORD_MIN = 10 // the server's rule (NewAdmin in admin.py)

function generatePassword(length = 16): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%&*?'
  const bytes = new Uint8Array(length)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('')
}

export default function AdminsPage() {
  usePageTitle('Admins')
  const { admin: me } = useSession()
  const notify = useNotify()
  const { data, error, loading, reload, refresh } = useLoad<{ admins: AdminRow[] }>('/admins')
  const [adding, setAdding] = useState(false)
  const [changingOwnPassword, setChangingOwnPassword] = useState(false)
  const [resetting, setResetting] = useState<AdminRow | null>(null)
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
    <div className="grid gap-6">
      <div>
        <PageHeader
          title="Admins"
          description="People who can sign in here. Every admin can do everything: change quizzes, see results and add or remove other admins."
          actions={
            <>
              <button type="button" className="btn btn-ghost" onClick={() => setChangingOwnPassword(true)}>
                Change my password
              </button>
              <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
                Add an admin
              </button>
            </>
          }
        />

        {!data && loading && <LoadingBlock label="Loading the admins" />}
        {!data && error && <ErrorBlock message={error} onRetry={reload} />}
        {data && (
          <div className="ad-table-wrap ad-table-sticky" tabIndex={0} role="region" aria-label="Admin accounts">
            <table className="ad-table" style={{ minWidth: 680 }}>
              <thead>
                <tr>
                  <th scope="col">Admin</th>
                  <th scope="col">Added</th>
                  <th scope="col">Last signed in</th>
                  <th scope="col" className="num">
                    Actions
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
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setChangingOwnPassword(true)}>
                          Change password
                        </button>
                      ) : (
                        <div className="inline-flex flex-wrap justify-end gap-2">
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setResetting(row)}>
                            Reset password
                          </button>
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
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <AuditLogFeed limit={30} />

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

      {changingOwnPassword && (
        <ChangeOwnPasswordDialog
          onClose={() => setChangingOwnPassword(false)}
          onChanged={() => {
            setChangingOwnPassword(false)
            notify('Your password has been updated.')
          }}
        />
      )}

      {resetting && (
        <ResetPasswordDialog
          target={resetting}
          onClose={() => setResetting(null)}
          onReset={() => {
            notify(`Password reset for ${resetting.email}.`)
            setResetting(null)
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
    </div>
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
            <div className="grid gap-2">
              <div className="flex gap-2">
                <div className="min-w-0 flex-1">
                  <input
                    className="field font-mono"
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
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setPassword(generatePassword())
                    setReveal(true)
                  }}
                >
                  Generate strong password
                </button>
                {password && <CopyButton text={password} label="Copy password" className="btn btn-ghost btn-sm" />}
              </div>
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

function ChangeOwnPasswordDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (newPassword.length < PASSWORD_MIN) {
      setError(`The new password needs at least ${PASSWORD_MIN} characters.`)
      return
    }
    if (newPassword !== confirmPassword) {
      setError('The new passwords do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await call('/me/password', { method: 'POST', body: { currentPassword, newPassword } })
      onChanged()
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={busy ? () => {} : onClose} title="Change your password">
      <form className="grid gap-4" onSubmit={submit}>
        <Field label="Current password">
          {(props) => (
            <input
              className="field"
              type="password"
              autoComplete="current-password"
              required
              {...props}
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          )}
        </Field>
        <Field label="New password" hint={`At least ${PASSWORD_MIN} characters.`}>
          {(props) => (
            <input
              className="field"
              type="password"
              autoComplete="new-password"
              maxLength={200}
              required
              {...props}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          )}
        </Field>
        <Field label="Confirm new password">
          {(props) => (
            <input
              className="field"
              type="password"
              autoComplete="new-password"
              maxLength={200}
              required
              {...props}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          )}
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : 'Update password'}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}

function ResetPasswordDialog({ target, onClose, onReset }: { target: AdminRow; onClose: () => void; onReset: () => void }) {
  const [password, setPassword] = useState(() => generatePassword())
  const [reveal, setReveal] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (password.length < PASSWORD_MIN) {
      setError(`The password needs at least ${PASSWORD_MIN} characters.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await call(`/admins/${target.id}/reset-password`, { method: 'POST', body: { password } })
      onReset()
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={busy ? () => {} : onClose} title={`Reset password for ${target.name || target.email}`}>
      <form className="grid gap-4" onSubmit={submit}>
        <p className="text-sm text-muted">
          Set a new temporary password for <strong>{target.email}</strong>. Copy it and share it securely — it is not emailed automatically.
        </p>
        <Field label="New password" hint={`At least ${PASSWORD_MIN} characters.`}>
          {(props) => (
            <div className="grid gap-2">
              <div className="flex gap-2">
                <div className="min-w-0 flex-1">
                  <input
                    className="field font-mono"
                    type={reveal ? 'text' : 'password'}
                    maxLength={200}
                    required
                    {...props}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </div>
                <button type="button" className="btn btn-ghost" onClick={() => setReveal((v) => !v)}>
                  {reveal ? 'Hide' : 'Show'}
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPassword(generatePassword())}>
                  Generate another
                </button>
                {password && <CopyButton text={password} label="Copy password" className="btn btn-ghost btn-sm" />}
              </div>
            </div>
          )}
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Resetting…' : 'Set new password'}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
