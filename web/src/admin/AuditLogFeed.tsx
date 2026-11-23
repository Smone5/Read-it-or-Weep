import { useState } from 'react'
import { ErrorBlock, LoadingBlock, Panel } from './components'
import { formatDateTime, timeAgo } from './format'
import { useLoad } from './hooks'
import type { AuditLogEntry } from './types'

const ACTION_LABELS: Record<string, string> = {
  add_admin: 'Added an admin',
  remove_admin: 'Removed an admin',
  change_password: 'Changed own password',
  reset_admin_password: 'Reset an admin password',
  update_settings: 'Updated company settings',
  upload_logo: 'Uploaded company logo',
  remove_logo: 'Removed company logo',
  rotate_access_code: 'Changed quiz access code',
  reset_quiz_settings: 'Reset quiz rules to company defaults',
  duplicate_quiz: 'Duplicated a quiz',
  delete_quiz: 'Deleted a quiz',
  delete_document: 'Removed a report document',
  bulk_delete_questions: 'Bulk deleted questions',
  reset_results: 'Reset quiz results',
  finish_live_session: 'Ended a live game',
}

function describeEntry(entry: AuditLogEntry): string {
  const base = ACTION_LABELS[entry.action] ?? entry.action.replace(/_/g, ' ')
  const d = entry.details ?? {}
  if (entry.action === 'add_admin' || entry.action === 'remove_admin' || entry.action === 'reset_admin_password') {
    return d.email ? `${base} (${String(d.email)})` : base
  }
  if (entry.action === 'update_settings') {
    const scope = entry.target_id ? `${entry.target_id} settings` : 'settings'
    const fields = Array.isArray(d.fields) && d.fields.length ? `: ${(d.fields as string[]).join(', ')}` : ''
    return `Updated ${scope}${fields}`
  }
  if (entry.action === 'rotate_access_code') {
    return d.hasCode ? 'Set or changed the access code (signed players out)' : 'Cleared the access code'
  }
  if (entry.action === 'delete_quiz' && d.title) {
    return `Deleted quiz “${String(d.title)}”`
  }
  if (entry.action === 'delete_document' && d.filename) {
    return `Removed report “${String(d.filename)}”`
  }
  if (entry.action === 'bulk_delete_questions' && typeof d.count === 'number') {
    return `Bulk deleted ${d.count} question${d.count === 1 ? '' : 's'}`
  }
  if (entry.action === 'duplicate_quiz' && typeof d.copiedManualQuestions === 'number' && d.copiedManualQuestions > 0) {
    return `Duplicated quiz (+${d.copiedManualQuestions} manual question${d.copiedManualQuestions === 1 ? '' : 's'})`
  }
  if (entry.action === 'finish_live_session' && d.code) {
    return `Ended live game ${String(d.code)}`
  }
  return base
}

export function AuditLogFeed({
  quizId,
  limit = 25,
  title = 'Recent admin activity',
  description = 'Security-sensitive and destructive actions recorded on the server.',
  collapsible = false,
}: {
  quizId?: string
  limit?: number
  title?: string
  description?: string
  collapsible?: boolean
}) {
  const [open, setOpen] = useState(!collapsible)
  const query = quizId ? `/audit-log?quizId=${encodeURIComponent(quizId)}&limit=${limit}` : `/audit-log?limit=${limit}`
  const { data, error, loading, reload, refresh } = useLoad<{ entries: AuditLogEntry[] }>(open ? query : null)
  const [refreshing, setRefreshing] = useState(false)

  const onRefresh = async () => {
    setRefreshing(true)
    try {
      await refresh()
    } catch {
      /* keep current entries */
    } finally {
      setRefreshing(false)
    }
  }

  const content = (
    <>
      {!data && loading && <LoadingBlock label="Loading activity" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}
      {data && data.entries.length === 0 && (
        <p className="text-sm text-muted">No recorded activity yet.</p>
      )}
      {data && data.entries.length > 0 && (
        <ul className="divide-y divide-line">
          {data.entries.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <span className="font-semibold text-ink">{describeEntry(entry)}</span>
                {entry.admin_email && <span className="ml-2 text-xs text-muted">by {entry.admin_email}</span>}
              </div>
              <time
                dateTime={entry.created_at}
                title={formatDateTime(entry.created_at)}
                className="flex-none whitespace-nowrap text-xs text-muted tabular-nums"
              >
                {timeAgo(entry.created_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </>
  )

  if (collapsible) {
    return (
      <section className="card p-4 sm:p-5">
        <details
          open={open}
          onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer font-bold">
            {title}
            <span className="ml-2 text-xs font-normal text-muted">({description})</span>
          </summary>
          <div className="mt-3">
            {open && (
              <div className="mb-2 flex justify-end">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onRefresh()} disabled={refreshing}>
                  {refreshing ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
            )}
            {content}
          </div>
        </details>
      </section>
    )
  }

  return (
    <Panel
      title={title}
      description={description}
      actions={
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onRefresh()} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      }
    >
      {content}
    </Panel>
  )
}
