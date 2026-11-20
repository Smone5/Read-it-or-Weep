import { useEffect, useRef, useState, type DragEvent } from 'react'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { Badge, Bar, ConfirmDialog, EmptyState, Note, Panel } from '../components'
import { useNotify } from '../context'
import { describeRejections, formatBytes, formatDateTime, isWorking, plural, splitSummary, type Tone } from '../format'
import type { QuizDocument, Section } from '../types'
import { useDocumentWatch, useQuiz } from './context'

const MAX_UPLOAD_BYTES = 60 * 1024 * 1024 // the server's limit (MAX_UPLOAD_BYTES in admin.py)

type Pending = { action: 'reprocess' | 'delete'; doc: QuizDocument }

export default function ReportTab() {
  const { detail, meta, refresh } = useQuiz()
  const notify = useNotify()
  const { quiz, documents, sections } = detail
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // "Process again" is accepted before the server marks the document as busy, so keep checking for a moment.
  const [expecting, setExpecting] = useState(false)
  const watch = useDocumentWatch(expecting)
  const wasWorking = useRef(watch.working)

  useEffect(() => {
    if (!expecting) return
    if (watch.working) {
      setExpecting(false)
      return
    }
    const timer = window.setTimeout(() => setExpecting(false), 12_000)
    return () => window.clearTimeout(timer)
  }, [expecting, watch.working])

  // When a job finishes, the sections, counts and question totals have all changed.
  useEffect(() => {
    if (wasWorking.current && !watch.working) refresh().catch(() => {})
    wasWorking.current = watch.working
  }, [watch.working, refresh])

  const confirm = async () => {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      if (pending.action === 'reprocess') {
        await call(`/documents/${pending.doc.id}/reprocess`, { method: 'POST' })
        setExpecting(true)
        notify('Reading the document again.')
      } else {
        await call(`/documents/${pending.doc.id}`, { method: 'DELETE' })
        notify(`${pending.doc.filename} was deleted.`)
      }
      setPending(null)
      await refresh().catch(() => {})
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setBusy(false)
    }
  }

  const closeConfirm = () => {
    setPending(null)
    setError(null)
  }

  return (
    <div className="grid gap-5">
      {!meta.aiConfigured && (
        <Note tone="warn" role="status">
          <strong>The AI service is not set up on this server</strong>, so a report cannot be turned into questions yet. Ask whoever runs the
          system to add the AI key. You can still write questions by hand on the Questions tab.
        </Note>
      )}

      <Panel
        title={documents.length ? 'Add another document' : 'Upload the report'}
        description="The report is read, split into sections, and questions are written and checked for each part. It usually takes a minute or two."
      >
        <Uploader />
      </Panel>

      {watch.stalled && (
        <Note tone="warn" role="status">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>Nothing has changed for five minutes, so the work has probably stopped. Check again, or use “Process again” on the document.</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={watch.resume}>
              Check again
            </button>
          </div>
        </Note>
      )}

      {documents.length === 0 ? (
        <EmptyState title="No report yet">Upload the document people should read before the meeting. Questions appear here once it has been read.</EmptyState>
      ) : (
        <ul className="grid gap-4">
          {documents.map((doc) => (
            <li key={doc.id}>
              <DocumentCard
                doc={doc}
                stalled={watch.stalled}
                sections={sections.filter((section) => section.document_id === doc.id)}
                onReprocess={() => setPending({ action: 'reprocess', doc })}
                onDelete={() => setPending({ action: 'delete', doc })}
              />
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={pending?.action === 'reprocess'}
        title="Process this document again?"
        confirmLabel="Process again"
        busyLabel="Starting…"
        danger={false}
        busy={busy}
        error={error}
        onConfirm={confirm}
        onCancel={closeConfirm}
      >
        <p>
          <strong>{pending?.doc.filename}</strong> will be read again from scratch and a fresh set of questions written.
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>AI questions you have not edited are replaced by the new set.</li>
          <li>Questions you edited or wrote by hand are kept, but lose their link to a section and to any picture.</li>
          <li>It uses the AI service again, which has a small cost.</li>
        </ul>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending?.action === 'delete'}
        title="Delete this document?"
        confirmLabel="Delete document"
        busyLabel="Deleting…"
        busy={busy}
        error={error}
        onConfirm={confirm}
        onCancel={closeConfirm}
      >
        <p>
          <strong>{pending?.doc.filename}</strong> and everything taken from it will be removed from “{quiz.title}”: its sections, its pictures and
          every question filed under it, including ones written by hand for its sections.
        </p>
        <p>Players' past results are kept. This cannot be undone.</p>
      </ConfirmDialog>
    </div>
  )
}

// ---- upload ----------------------------------------------------------------------------

const extensionOf = (filename: string) => {
  const dot = filename.lastIndexOf('.')
  return dot < 0 ? '' : filename.slice(dot).toLowerCase()
}

function Uploader() {
  const { detail, meta, setDocuments } = useQuiz()
  const notify = useNotify()
  const quizId = detail.quiz.id
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const disabled = busy || !meta.aiConfigured
  const others = meta.extensions.filter((extension) => extension !== '.docx')

  const upload = async (files: FileList | null) => {
    const chosen = files ? Array.from(files) : []
    if (chosen.length === 0 || disabled) return
    if (chosen.length > 1) {
      setError('Upload one file at a time.')
      return
    }
    const file = chosen[0]
    const extension = extensionOf(file.name)
    if (!meta.extensions.includes(extension)) {
      setError(
        extension === '.doc'
          ? 'Old-format .doc files are not supported. Open it in Word and save it as .docx.'
          : `That file type is not supported. Use ${meta.extensions.join(', ')}.`,
      )
      return
    }
    if (file.size === 0) {
      setError('That file is empty.')
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('That file is larger than 60 MB.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await call<{ documents: QuizDocument[] }>(`/quizzes/${quizId}/documents`, { method: 'POST', body })
      setDocuments(result.documents)
      notify(`${file.name} uploaded. Reading it now.`)
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = '' // so the same file can be chosen again
    }
  }

  const onDrag = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (!disabled) setOver(true)
  }

  const onLeave = (event: DragEvent<HTMLDivElement>) => {
    // Moving across the zone's own children also fires "leave"; only react to really leaving it.
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return
    setOver(false)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    void upload(event.dataTransfer.files)
  }

  return (
    <>
      <div className="ad-drop" data-over={over} onDragEnter={onDrag} onDragOver={onDrag} onDragLeave={onLeave} onDrop={onDrop}>
        <p className="text-sm font-semibold">{busy ? 'Uploading…' : 'Drag the report here'}</p>
        <button type="button" className="btn btn-primary" onClick={() => input.current?.click()} disabled={disabled}>
          Choose a file
        </button>
        <input
          ref={input}
          type="file"
          hidden
          accept={meta.extensions.join(',')}
          disabled={disabled}
          aria-label="Report file"
          onChange={(event) => void upload(event.target.files)}
        />
        <p className="max-w-xl text-xs text-muted">
          <strong>Word (.docx) is the main format</strong>: its headings become the quiz's sections, and its tables, charts and pictures are read
          too.{others.length > 0 && ` Supporting material can also be added as ${others.join(', ')}.`} Up to 60 MB, one file at a time.
        </p>
      </div>
      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </>
  )
}

// ---- one document ----------------------------------------------------------------------

function statusOf(doc: QuizDocument, stalled: boolean): { label: string; tone: Tone } {
  if (doc.status === 'failed') return { label: 'Failed', tone: 'bad' }
  if (isWorking(doc) && stalled) return { label: 'No progress', tone: 'warn' }
  if (doc.status === 'ready') return doc.stage ? { label: 'Writing more questions', tone: 'info' } : { label: 'Ready', tone: 'good' }
  if (isWorking(doc)) return doc.status === 'uploaded' ? { label: 'Waiting to start', tone: 'info' } : { label: 'Working', tone: 'info' }
  return { label: 'Did not start', tone: 'warn' } // uploaded a while ago and never picked up
}

function DocumentCard({
  doc,
  stalled,
  sections,
  onReprocess,
  onDelete,
}: {
  doc: QuizDocument
  /** Nothing has moved for a while: the job has probably died, so let the person start it again. */
  stalled: boolean
  sections: Section[]
  onReprocess: () => void
  onDelete: () => void
}) {
  const status = statusOf(doc, stalled)
  const working = isWorking(doc)
  const { progress } = doc
  const total = progress.total ?? 0
  const done = progress.done ?? 0
  const summary = splitSummary(doc.summary)
  const dropped = describeRejections(progress.rejected)
  const counts: { label: string; value: number | null }[] = [
    { label: 'Sections', value: doc.section_count },
    { label: 'Passages', value: doc.chunk_count },
    { label: 'Pictures', value: doc.image_count },
    { label: 'Questions', value: doc.question_count },
    { label: 'Words', value: doc.word_count },
  ]

  return (
    <article className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-bold [overflow-wrap:anywhere]">{doc.title || doc.filename}</h2>
          <p className="mt-0.5 text-sm text-muted [overflow-wrap:anywhere]">
            {doc.title ? `${doc.filename} · ` : ''}
            {doc.role === 'primary' ? 'Main report' : 'Supporting document'}
            {doc.size_bytes ? ` · ${formatBytes(doc.size_bytes)}` : ''} · uploaded {formatDateTime(doc.created_at)}
          </p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>

      {working && (
        <div className="mt-4" role="status">
          <div className="mb-1.5 flex flex-wrap justify-between gap-2 text-sm">
            <span className="font-semibold">{progress.label || 'Getting started'}</span>
            {total > 1 && (
              <span className="tabular-nums text-muted">
                {done} of {total}
              </span>
            )}
          </div>
          <Bar label={progress.label || 'Progress'} value={done} max={total} indeterminate={total <= 1} />
          <p className="mt-2 text-xs text-muted">
            {stalled
              ? 'This has probably stopped. Use “Process again” to restart it.'
              : 'You can leave this page. The work carries on and this updates by itself.'}
          </p>
        </div>
      )}

      {!working && doc.status === 'uploaded' && (
        <div className="mt-4">
          <Note tone="warn">This document was uploaded but never read. Use “Process again” to start it.</Note>
        </div>
      )}

      {doc.status === 'failed' && (
        <div className="mt-4">
          <ErrorNote>{doc.error || 'This document could not be processed.'}</ErrorNote>
        </div>
      )}

      {doc.status === 'ready' && (
        <div className="mt-4 grid gap-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-5">
            {counts.map((count) => (
              <div key={count.label}>
                <dt className="text-xs text-muted">{count.label}</dt>
                <dd className="text-lg font-bold tabular-nums">{count.value === null ? '—' : count.value.toLocaleString()}</dd>
              </div>
            ))}
          </dl>

          {(summary.text || summary.themes) && (
            <div>
              <h3 className="text-sm font-bold">What it is about</h3>
              {summary.text && <p className="mt-1 whitespace-pre-line text-sm">{summary.text}</p>}
              {summary.themes && (
                <p className="mt-2 text-sm text-muted">
                  <span className="font-semibold text-ink">Key themes: </span>
                  {summary.themes}
                </p>
              )}
            </div>
          )}

          {(progress.drafted !== undefined || progress.added !== undefined || dropped.length > 0) && (
            <div>
              <h3 className="text-sm font-bold">How the questions were checked</h3>
              <p className="mt-1 text-sm">
                {progress.drafted !== undefined && (
                  <>
                    {plural(progress.drafted, 'question')} drafted, {progress.kept ?? 0} kept.{' '}
                  </>
                )}
                {progress.added !== undefined && <>The last “write more” added {plural(progress.added, 'question')}. </>}
                {dropped.length === 0 && 'Nothing was dropped.'}
              </p>
              {dropped.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-muted">
                  {dropped.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {sections.length > 0 && (
            <details>
              <summary className="cursor-pointer py-1 text-sm font-bold">Sections and their live questions ({sections.length})</summary>
              <ul className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
                {sections.map((section) => (
                  <li key={section.id} className="flex items-baseline justify-between gap-3 rounded-md bg-surface-2 px-2.5 py-1.5">
                    <span className="min-w-0 [overflow-wrap:anywhere]">{section.heading}</span>
                    <span className="flex-none tabular-nums" style={section.questions === 0 ? { color: 'var(--warn)', fontWeight: 600 } : undefined}>
                      {section.questions === 0 ? 'no questions' : section.questions}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {doc.warnings.length > 0 && (
        <div className="mt-4">
          <Note tone="warn">
            <strong className="block">Worth knowing</strong>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {doc.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Note>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onReprocess} disabled={working && !stalled}>
          Process again
        </button>
        <button type="button" className="btn btn-danger btn-sm" onClick={onDelete}>
          Delete
        </button>
      </div>
    </article>
  )
}
