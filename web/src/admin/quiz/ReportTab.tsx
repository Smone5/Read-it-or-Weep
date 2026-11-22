import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { Badge, Bar, ConfirmDialog, Dialog, DialogActions, EmptyState, Field, Note, Panel, Switch } from '../components'
import { useNotify } from '../context'
import { describeRejections, formatBytes, formatDateTime, isWorking, plural, splitSummary, type Tone } from '../format'
import { paths } from '../paths'
import type { QuizDocument, Section } from '../types'
import { useDocumentWatch, useQuiz } from './context'
import { YouTubePicker } from './YouTubePicker'

const MAX_UPLOAD_BYTES = 60 * 1024 * 1024 // the server's limit (MAX_UPLOAD_BYTES in admin.py)

type Pending = { action: 'reprocess' | 'delete'; doc: QuizDocument }

export default function ReportTab() {
  const { detail, meta, refresh } = useQuiz()
  const notify = useNotify()
  const { quiz, documents, sections } = detail
  const [pending, setPending] = useState<Pending | null>(null)
  const [videoSection, setVideoSection] = useState<Section | null>(null)
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
        description="The report is read, split into sections, and questions are written and checked for each part. Any YouTube links inside your Word, PDF, or PowerPoint file are automatically detected and paired with questions from that section."
      >
        <Uploader />
      </Panel>

      {watch.stalled && (
        <Note tone="warn" role="status">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>Nothing has changed for ten minutes, so the work has probably stopped. Check again, or use “Process again” on the document.</span>
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
                quizId={quiz.id}
                doc={doc}
                stalled={watch.stalled}
                sections={sections.filter((section) => section.document_id === doc.id)}
                onReprocess={() => setPending({ action: 'reprocess', doc })}
                onDelete={() => setPending({ action: 'delete', doc })}
                onEditVideo={setVideoSection}
              />
            </li>
          ))}
        </ul>
      )}

      <SectionVideoDialog
        section={videoSection}
        onClose={() => setVideoSection(null)}
        onSaved={(message) => {
          setVideoSection(null)
          notify(message)
          refresh().catch(() => {})
        }}
      />

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
  const [uploadStatus, setUploadStatus] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const disabled = busy || !meta.aiConfigured
  const others = meta.extensions.filter((extension) => extension !== '.docx')

  const upload = async (files: FileList | null) => {
    const chosen = files ? Array.from(files) : []
    if (chosen.length === 0 || disabled) return
    const valid: File[] = []
    const rejected: string[] = []
    for (const file of chosen) {
      const extension = extensionOf(file.name)
      if (!meta.extensions.includes(extension)) {
        rejected.push(
          extension === '.doc'
            ? `${file.name}: old .doc format is not supported (save as .docx).`
            : `${file.name}: unsupported type (use ${meta.extensions.join(', ')}).`,
        )
        continue
      }
      if (file.size === 0) {
        rejected.push(`${file.name}: file is empty.`)
        continue
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        rejected.push(`${file.name}: larger than 60 MB.`)
        continue
      }
      valid.push(file)
    }
    if (valid.length === 0) {
      setError(rejected.join(' '))
      if (input.current) input.current.value = ''
      return
    }
    setBusy(true)
    setError(rejected.length > 0 ? rejected.join(' ') : null)
    let uploaded = 0
    const failed: string[] = [...rejected]
    try {
      for (let i = 0; i < valid.length; i++) {
        const file = valid[i]
        setUploadStatus(valid.length > 1 ? `Uploading ${i + 1} of ${valid.length}: ${file.name}…` : `Uploading ${file.name}…`)
        try {
          const body = new FormData()
          body.append('file', file)
          const result = await call<{ documents: QuizDocument[] }>(`/quizzes/${quizId}/documents`, { method: 'POST', body })
          setDocuments(result.documents)
          uploaded += 1
        } catch (reason) {
          failed.push(`${file.name}: ${errorText(reason)}`)
        }
      }
      if (uploaded === 1) notify(`${valid[0].name} uploaded. Reading it now.`)
      else if (uploaded > 1) notify(`${uploaded} documents uploaded. Reading them now.`)
      if (failed.length > 0) setError(failed.join(' '))
    } finally {
      setBusy(false)
      setUploadStatus('')
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
        <p className="text-sm font-semibold">{busy ? uploadStatus || 'Uploading…' : 'Drag your report or packet files here'}</p>
        <button type="button" className="btn btn-primary" onClick={() => input.current?.click()} disabled={disabled}>
          Choose file(s)
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept={meta.extensions.join(',')}
          disabled={disabled}
          aria-label="Report files"
          onChange={(event) => void upload(event.target.files)}
        />
        <p className="max-w-xl text-xs text-muted">
          <strong>Word (.docx) is the main format</strong>: its headings become the quiz's sections, and its tables, charts and pictures are read
          too.{others.length > 0 && ` Supporting material can also be added as ${others.join(', ')}.`} Up to 60 MB per file — drop multiple files to queue a full packet.
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
  quizId,
  doc,
  stalled,
  sections,
  onReprocess,
  onDelete,
  onEditVideo,
}: {
  quizId: string
  doc: QuizDocument
  /** Nothing has moved for a while: the job has probably died, so let the person start it again. */
  stalled: boolean
  sections: Section[]
  onReprocess: () => void
  onDelete: () => void
  onEditVideo: (section: Section) => void
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
            <details open={sections.length <= 10}>
              <summary className="cursor-pointer py-1 text-sm font-bold">Sections, questions &amp; YouTube clips ({sections.length})</summary>
              <ul className="mt-2 grid gap-1.5 text-sm">
                {sections.map((section) => {
                  const vids = section.videos ?? 0
                  const totalQ = section.total_questions ?? section.questions
                  const pendingQ = Math.max(0, totalQ - section.questions)
                  const countText =
                    totalQ === 0
                      ? 'no questions'
                      : pendingQ > 0
                        ? `${section.questions} live · ${pendingQ} pending`
                        : plural(section.questions, 'question')
                  return (
                    <li key={section.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-surface-2 px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <Link
                          to={`${paths.quiz(quizId, 'questions')}?section=${encodeURIComponent(section.id)}`}
                          className="font-medium hover:underline [overflow-wrap:anywhere]"
                        >
                          {section.heading}
                        </Link>
                        {vids > 0 && (
                          <span className="ml-2 inline-flex items-center rounded bg-accent/15 px-1.5 py-0.5 text-xs font-semibold text-accent">
                            {plural(vids, 'video clip')}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-none items-center gap-3">
                        <Link
                          to={`${paths.quiz(quizId, 'questions')}?section=${encodeURIComponent(section.id)}`}
                          className="tabular-nums text-muted hover:underline"
                          style={totalQ === 0 ? { color: 'var(--warn)', fontWeight: 600 } : undefined}
                        >
                          {countText}
                        </Link>
                        {totalQ > 0 && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => onEditVideo(section)}
                          >
                            {vids > 0 ? 'Manage videos' : 'Add video'}
                          </button>
                        )}
                      </div>
                    </li>
                  )
                })}
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

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onReprocess}
          disabled={(working && !stalled) || Boolean(doc.sourcePurged)}
          title={doc.sourcePurged ? 'The source file was purged after processing. Upload the report again to reprocess it.' : undefined}
        >
          Process again
        </button>
        <button type="button" className="btn btn-danger btn-sm" onClick={onDelete}>
          Delete
        </button>
        {doc.sourcePurged && <span className="text-xs text-muted">Source file purged after processing</span>}
      </div>
    </article>
  )
}

function SectionVideoDialog({
  section,
  onClose,
  onSaved,
}: {
  section: Section | null
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const [videoUrl, setVideoUrl] = useState('')
  const [videoTitle, setVideoTitle] = useState('')
  const [maxQuestions, setMaxQuestions] = useState(2)
  const [replaceExisting, setReplaceExisting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const clips = section?.video_clips ?? (section?.video_url ? [{ url: section.video_url, title: section.video_title ?? null, count: section.videos ?? 1 }] : [])

  useEffect(() => {
    if (!section) return
    setVideoUrl('')
    setVideoTitle('')
    setMaxQuestions(2)
    setReplaceExisting(false)
    setError(null)
    setSaving(false)
  }, [section])

  const removeClip = async (clipUrl: string, clipTitle: string | null) => {
    if (!section || saving) return
    setSaving(true)
    setError(null)
    try {
      await call(`/sections/${section.id}/video`, {
        method: 'POST',
        body: { mode: 'remove', removeUrl: clipUrl },
      })
      onSaved(`Removed “${clipTitle || 'YouTube clip'}” from “${section.heading}”.`)
    } catch (reason) {
      setError(errorText(reason))
      setSaving(false)
    }
  }

  const save = async () => {
    if (!section || !videoUrl.trim()) return
    setSaving(true)
    setError(null)
    try {
      const result = await call<{ updated: number }>(`/sections/${section.id}/video`, {
        method: 'POST',
        body: {
          videoUrl: videoUrl.trim(),
          videoTitle: videoTitle.trim() || null,
          maxQuestions,
          mode: replaceExisting ? 'replace' : 'add',
        },
      })
      onSaved(`Attached YouTube clip to ${plural(result.updated, 'question')} in “${section.heading}”.`)
    } catch (reason) {
      setError(errorText(reason))
      setSaving(false)
    }
  }

  return (
    <Dialog open={section !== null} title={section ? `YouTube clips · ${section.heading}` : ''} onClose={saving ? () => {} : onClose} wide>
      <div className="grid gap-4">
        <p className="text-sm text-muted">
          Attach YouTube clips to this section — players see the replay card right after answering questions from this part of the report. Adding a clip keeps existing clips on other questions intact.
        </p>

        {clips.length > 0 && (
          <div className="grid gap-2">
            <p className="text-xs font-bold uppercase tracking-wide text-muted">Attached clips ({clips.length})</p>
            <ul className="grid gap-1.5">
              {clips.map((clip) => (
                <li key={clip.url} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{clip.title || clip.url}</p>
                    <p className="text-xs text-muted">
                      On {plural(clip.count, 'question')} ·{' '}
                      <a href={clip.url.replace(/#external$/i, '')} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                        Open on YouTube
                      </a>
                    </p>
                  </div>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={saving} onClick={() => void removeClip(clip.url, clip.title)}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <YouTubePicker
          value={videoUrl}
          title={videoTitle}
          disabled={saving}
          onChange={({ videoUrl: nextUrl, videoTitle: nextTitle }) => {
            setVideoUrl(nextUrl)
            setVideoTitle(nextTitle)
          }}
        />

        {videoUrl.trim() && (
          <div className="grid gap-3">
            <Field
              label="Attach to how many questions in this section?"
              hint="Attaching to 2–3 multiple-choice questions keeps the replay feeling fresh instead of repeating on every turn."
            >
              {(props) => (
                <select
                  className="field max-w-xs"
                  {...props}
                  value={maxQuestions}
                  disabled={saving}
                  onChange={(e) => setMaxQuestions(Number(e.target.value))}
                >
                  <option value={1}>Up to 1 question</option>
                  <option value={2}>Up to 2 questions (recommended)</option>
                  <option value={3}>Up to 3 questions</option>
                  <option value={10}>Up to 10 questions in section</option>
                </select>
              )}
            </Field>
            {clips.length > 0 && (
              <Switch
                checked={replaceExisting}
                onChange={setReplaceExisting}
                label="Replace existing clips in this section"
                description="Leave off to keep other clips in this section and pair this clip with questions that do not have a video yet."
                disabled={saving}
              />
            )}
          </div>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}

        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Close
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving || !videoUrl.trim()}>
            {saving ? 'Saving…' : replaceExisting ? 'Replace section clips' : 'Add clip to section'}
          </button>
        </DialogActions>
      </div>
    </Dialog>
  )
}
