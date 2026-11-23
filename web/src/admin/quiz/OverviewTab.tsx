import { useEffect, useId, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import QRCode from 'qrcode'
import { ErrorNote } from '../../components/ui'
import { call, errorText } from '../api'
import { AuditLogFeed } from '../AuditLogFeed'
import { ChoiceGroup, ConfirmDialog, CopyButton, Dialog, DialogActions, Field, Note, Panel, SaveBar, Switch } from '../components'
import { useNotify, useUnsavedGuard } from '../context'
import { formatDateTime, fromLocalInput, isThisSite, isWorking, onThisSite, plural, QUIZ_STATUS, toLocalInput } from '../format'
import { paths } from '../paths'
import type { QuestionSources, Quiz, QuizOverview, QuizPatch, QuizStatus } from '../types'
import { useQuiz } from './context'

export default function OverviewTab() {
  const { detail } = useQuiz()
  return (
    <div className="grid gap-5">
      {detail.quiz.status === 'draft' && <GettingReady />}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <StatusPanel />
        <SharePanel />
      </div>
      <AtAGlance />
      <DetailsForm />
      <AuditLogFeed quizId={detail.quiz.id} limit={15} title="Recent activity on this quiz" description="Access-code changes, resets, and deletions" collapsible />
      <LifecyclePanel />
    </div>
  )
}

// ---- getting a new quiz ready --------------------------------------------------------

function playableMcqCount(overview: QuizOverview, source?: QuestionSources): number {
  if (source === 'manual' && overview.approved_manual_mcq !== undefined) return overview.approved_manual_mcq
  if (source === 'ai' && overview.approved_ai_mcq !== undefined) return overview.approved_ai_mcq
  return overview.approved_mcq ?? overview.approved
}

function GettingReady() {
  const { detail } = useQuiz()
  const { quiz, overview, documents, rules } = detail
  const working = documents.some(isWorking)
  const docCount = documents.length
  const playableMcq = playableMcqCount(overview, rules.question_sources)
  const steps = [
    {
      done: docCount > 0 && !working,
      title: 'Upload the report',
      note: working
        ? 'The report is being read and the questions written. This takes a minute or two.'
        : docCount > 0
          ? `${plural(docCount, 'document')} uploaded.`
          : 'The questions are written from it automatically.',
      to: paths.quiz(quiz.id, 'report'),
      action: 'Go to Report',
    },
    {
      done: playableMcq >= 3,
      title: 'Check the questions',
      note:
        overview.approved + overview.pending === 0
          ? 'They appear once a report has been read. You can also write your own.'
          : playableMcq < 3 && overview.approved >= 3
            ? `${plural(playableMcq, 'live multiple-choice question')} eligible (${overview.approved} live total). At least 3 multiple-choice questions are needed to open.`
            : `${plural(overview.approved, 'question')} live${overview.pending ? `, ${overview.pending} waiting for your review` : ''}.`,
      to: paths.quiz(quiz.id, 'questions'),
      action: 'Go to Questions',
    },
    {
      done: false,
      title: 'Open the quiz and share the link',
      note: 'Set the status below to Open, then send people the play link.',
      to: null,
      action: '',
    },
  ]
  return (
    <Panel title="Getting this quiz ready" description="It is a draft, so only signed-in admins can play it for now.">
      {docCount === 0 && (
        <div className="mb-4">
          <Note tone="info">
            Uploading a confidential report? Review{' '}
            <a href="#confidentiality-panel" className="ad-link font-semibold">
              Report access &amp; confidentiality
            </a>{' '}
            below (Unlisted, Confidential voice mode, Purge source file) before Step 1.
          </Note>
        </div>
      )}
      <ol className="grid gap-3">
        {steps.map((step, index) => (
          <li key={step.title} className="flex flex-wrap items-start gap-x-3 gap-y-1">
            <span
              className="grid h-6 w-6 flex-none place-items-center rounded-full text-xs font-bold"
              style={step.done ? { background: 'var(--good)', color: '#fff' } : { background: 'var(--surface-2)', color: 'var(--muted)' }}
              aria-hidden
            >
              {step.done ? '✓' : index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">
                {step.title}
                {step.done && <span className="sr-only"> (done)</span>}
              </div>
              <div className="text-sm text-muted">{step.note}</div>
            </div>
            {step.to && (
              <Link to={step.to} className="btn btn-ghost btn-sm">
                {step.action}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  )
}

// ---- status ----------------------------------------------------------------------------

const STATUS_OPTIONS = (['draft', 'open', 'closed'] as const).map((status) => ({
  value: status,
  label: QUIZ_STATUS[status].label,
  description: QUIZ_STATUS[status].explain,
}))

function StatusPanel() {
  const { detail, save } = useQuiz()
  const notify = useNotify()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const current = detail.quiz.status

  const change = async (status: QuizStatus) => {
    if (status === current || saving) return
    setSaving(true)
    setError(null)
    try {
      await save({ status })
      notify(`The quiz is now ${QUIZ_STATUS[status].label.toLowerCase()}.`)
    } catch (reason) {
      // Opening a quiz with fewer than three live questions is refused; the server says why.
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel title="Status" description="Who can play right now. Changes take effect straight away.">
      <ChoiceGroup label="Quiz status" options={STATUS_OPTIONS} value={current} onChange={change} disabled={saving} />
      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </Panel>
  )
}

// ---- the play link ---------------------------------------------------------------------

function SharePanel() {
  const { detail } = useQuiz()
  const { quiz } = detail
  const [qr, setQr] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    QRCode.toDataURL(quiz.playUrl, { width: 360, margin: 1 }).then(
      (url) => {
        if (active) setQr(url)
      },
      () => {
        if (active) setQr(null)
      },
    )
    return () => {
      active = false
    }
  }, [quiz.playUrl])

  // Play-testing a draft relies on this browser's admin sign-in, which only pages on the
  // address in use right now can see. So the test always opens on this address.
  const elsewhere = !isThisSite(quiz.playUrl)
  const testUrl = elsewhere ? onThisSite(quiz.playUrl) : quiz.playUrl
  const inviteBlurb = [
    `Ready to test yourself on "${quiz.title}"?`,
    quiz.meetingAt ? `Play before our meeting on ${formatDateTime(quiz.meetingAt)}:` : 'Play here:',
    quiz.playUrl,
    quiz.accessCode ? `Access code: ${quiz.accessCode}` : null,
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <Panel title="Play link" description="Send this to the people who should read the report.">
      <div className="flex flex-wrap gap-2">
        <div className="min-w-0 flex-1 basis-56">
          <input className="field" readOnly value={quiz.playUrl} aria-label="Play link" onFocus={(event) => event.target.select()} />
        </div>
        <CopyButton text={quiz.playUrl} label="Copy link" />
        <CopyButton text={inviteBlurb} label="Copy invite" className="btn btn-ghost" />
      </div>

      {quiz.status === 'draft' && (
        <p className="mt-2 text-sm text-muted">While the quiz is a draft the link only works for signed-in admins.</p>
      )}
      {quiz.status === 'closed' && <p className="mt-2 text-sm text-muted">The quiz is closed, so the link tells players it has finished.</p>}
      {quiz.accessCode && (
        <p className="mt-2 text-sm text-muted">
          Players will also be asked for the access code: <strong className="text-ink">{quiz.accessCode}</strong>
        </p>
      )}
      {elsewhere && (
        <div className="mt-3">
          <Note tone="warn">
            This link points to a different address from the one you are using now ({window.location.origin}). If players cannot open it, ask
            whoever set the system up to correct the site address (the WEB_BASE_URL setting).
          </Note>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-4">
        {qr ? (
          <img src={qr} alt="QR code that opens the play link" width={148} height={148} className="rounded-lg border border-line bg-white p-1" />
        ) : (
          <div className="grid h-[148px] w-[148px] place-items-center rounded-lg border border-dashed border-line text-xs text-muted">QR code</div>
        )}
        <div className="grid gap-2">
          <a className="btn btn-primary" href={testUrl} target="_blank" rel="noopener">
            Play-test in a new tab
          </a>
          {qr && (
            <a className="btn btn-ghost" href={qr} download={`${quiz.slug}-qr-code.png`}>
              Download QR code
            </a>
          )}
          <p className="max-w-xs text-xs text-muted">Put the QR code on a slide or in the invitation so people can join from their phone.</p>
        </div>
      </div>
    </Panel>
  )
}

// ---- numbers ---------------------------------------------------------------------------

function AtAGlance() {
  const { detail } = useQuiz()
  const { quiz, overview } = detail
  const questions = paths.quiz(quiz.id, 'questions')
  const tiles = [
    { label: 'Live questions', value: overview.approved, to: `${questions}?status=approved`, hint: `${overview.manual} written by hand` },
    {
      label: 'Waiting for review',
      value: overview.pending,
      to: `${questions}?status=pending`,
      hint: overview.pending ? 'Not asked until you approve them' : 'Nothing to review',
      warn: overview.pending > 0,
    },
    {
      label: 'Flagged by players',
      value: overview.flagged,
      to: `${questions}?flagged=1`,
      hint: overview.flagged ? 'Worth a look' : 'No complaints',
      warn: overview.flagged > 0,
    },
    { label: 'Players', value: overview.players, to: paths.quiz(quiz.id, 'results'), hint: `${plural(overview.games, 'game')} finished` },
  ]
  return (
    <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((tile) => (
        <li key={tile.label}>
          <Link to={tile.to} className="card h-full p-4">
            <div className="text-xs font-semibold text-muted">{tile.label}</div>
            <div className="mt-1 text-2xl font-bold tabular-nums" style={tile.warn ? { color: 'var(--warn)' } : undefined}>
              {tile.value}
            </div>
            <div className="mt-0.5 text-xs text-muted">{tile.hint}</div>
          </Link>
        </li>
      ))}
    </ul>
  )
}

// ---- details ---------------------------------------------------------------------------

type Details = {
  title: string
  description: string
  meeting: string
  accessCode: string
  domains: string
  teams: string[]
  /** A team name typed but not yet added; it is added on save so it is never silently lost. */
  teamDraft: string
  reportUrl: string
  allowDownload: boolean
  unlisted: boolean
  confidentialVoice: boolean
  purgeSource: boolean
}

const detailsOf = (quiz: Quiz, rules: import('../types').GameRules): Details => ({
  title: quiz.title,
  description: quiz.description ?? '',
  meeting: toLocalInput(quiz.meetingAt),
  accessCode: quiz.accessCode ?? '',
  domains: quiz.allowedEmailDomains.join(', '),
  teams: quiz.teams,
  teamDraft: '',
  reportUrl: rules.report_url ?? '',
  allowDownload: Boolean(rules.allow_download),
  unlisted: Boolean(rules.unlisted),
  confidentialVoice: Boolean(rules.confidential_voice),
  purgeSource: Boolean(rules.purge_source_after_process),
})

const DOMAIN = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/
const HTTP_URL = /^https?:\/\/\S+$/i

function parseDomains(text: string): string[] {
  const cleaned = text
    .split(/[\s,;]+/)
    .map((domain) => domain.trim().toLowerCase().replace(/^@+/, ''))
    .filter(Boolean)
  return [...new Set(cleaned)]
}

function addTeam(teams: string[], name: string): string[] {
  const team = name.trim().slice(0, 60)
  if (!team || teams.some((existing) => existing.toLowerCase() === team.toLowerCase())) return teams
  return [...teams, team]
}

/** Only what differs from the saved quiz, in the shape PATCH /quizzes/{id} expects. */
function changesFrom(quiz: Quiz, rules: import('../types').GameRules, form: Details): QuizPatch {
  const saved = detailsOf(quiz, rules)
  const changes: QuizPatch = {}
  if (form.title.trim() !== quiz.title) changes.title = form.title.trim()
  if (form.description.trim() !== saved.description.trim()) changes.description = form.description.trim() || null
  if (form.meeting !== saved.meeting) {
    const meetingAt = fromLocalInput(form.meeting)
    if (meetingAt) changes.meetingAt = meetingAt
    else changes.clearMeetingAt = true
  }
  if (form.accessCode.trim() !== saved.accessCode) changes.accessCode = form.accessCode.trim()
  const domains = parseDomains(form.domains)
  if (domains.join(',') !== quiz.allowedEmailDomains.join(',')) changes.allowedEmailDomains = domains
  const teams = addTeam(form.teams, form.teamDraft)
  if (teams.join('\n') !== quiz.teams.join('\n')) changes.teams = teams
  const settingsPatch: NonNullable<QuizPatch['settings']> = {}
  if (form.reportUrl.trim() !== saved.reportUrl.trim()) settingsPatch.report_url = form.reportUrl.trim() || null
  if (form.allowDownload !== saved.allowDownload) settingsPatch.allow_download = form.allowDownload
  if (form.unlisted !== saved.unlisted) settingsPatch.unlisted = form.unlisted
  if (form.confidentialVoice !== saved.confidentialVoice) settingsPatch.confidential_voice = form.confidentialVoice
  if (form.purgeSource !== saved.purgeSource) settingsPatch.purge_source_after_process = form.purgeSource
  if (Object.keys(settingsPatch).length > 0) changes.settings = settingsPatch
  return changes
}

function DetailsForm() {
  const { detail, save } = useQuiz()
  const notify = useNotify()
  const { quiz, rules } = detail
  const [form, setForm] = useState<Details>(() => detailsOf(quiz, rules))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const teamsLabel = useId()

  const set = (patch: Partial<Details>) => {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  const changes = changesFrom(quiz, rules, form)
  const dirty = Object.keys(changes).length > 0
  useUnsavedGuard(dirty)
  const badDomain = parseDomains(form.domains).find((domain) => !DOMAIN.test(domain)) ?? null
  const badReportUrl = form.reportUrl.trim() !== '' && !HTTP_URL.test(form.reportUrl.trim())
  const rotatingOpenCode = quiz.status === 'open' && 'accessCode' in changes

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || !dirty) return
    if (form.title.trim().length < 2) {
      setError('The title needs at least two characters.')
      return
    }
    if (badDomain) {
      setError(`“${badDomain}” does not look like an email domain. Use something like example.com.`)
      return
    }
    if (badReportUrl) {
      setError('Report link must start with https:// or http://.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const prevUrl = quiz.playUrl
      const saved = await save(changes)
      setForm(detailsOf(saved.quiz, saved.rules))
      if (saved.quiz.playUrl !== prevUrl) {
        notify('Saved. Your play link changed to an unlisted address — copy the updated link above.')
      } else {
        notify('Quiz details saved.')
      }
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    // A flex column, not a grid: the save bar is `position: sticky`, and a grid item can only
    // stick within its own row, which would pin it to the bottom of the form instead of the screen.
    <form onSubmit={submit} className="flex flex-col gap-5">
      <Panel title="Details" description="What players see, and who is allowed in.">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Title">
            {(props) => <input className="field" {...props} value={form.title} onChange={(event) => set({ title: event.target.value })} maxLength={120} required />}
          </Field>
          <Field
            label="Meeting date and time"
            hint={
              form.meeting ? (
                <button type="button" className="ad-link" onClick={() => set({ meeting: '' })}>
                  Remove the date
                </button>
              ) : (
                'Optional. Players see a countdown to it.'
              )
            }
          >
            {(props) => <input className="field" type="datetime-local" {...props} value={form.meeting} onChange={(event) => set({ meeting: event.target.value })} />}
          </Field>
          <Field label="Description" hint="Optional. A line or two shown to players before they start." className="md:col-span-2">
            {(props) => (
              <textarea className="field" {...props} value={form.description} onChange={(event) => set({ description: event.target.value })} maxLength={600} rows={3} />
            )}
          </Field>
          <Field
            label="Access code"
            hint={
              rotatingOpenCode
                ? 'Warning: changing the access code on an open quiz signs current players out until they enter the new code.'
                : 'Optional. If set, players must type it to join. Capital letters do not matter.'
            }
          >
            {(props) => (
              <input className="field" {...props} value={form.accessCode} onChange={(event) => set({ accessCode: event.target.value })} maxLength={40} autoComplete="off" />
            )}
          </Field>
          <Field
            label="Allowed email domains"
            hint="Optional. Separate several with commas. Only people with an email at these domains can join; leave empty to let anyone in."
            error={badDomain ? `“${badDomain}” does not look like an email domain.` : null}
          >
            {(props) => (
              <input
                className="field"
                {...props}
                value={form.domains}
                onChange={(event) => set({ domains: event.target.value })}
                placeholder="example.com, example.org"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
              />
            )}
          </Field>

          <div className="md:col-span-2">
            <span className="label" id={teamsLabel}>
              Teams
            </span>
            {form.teams.length > 0 && (
              <ul className="mb-2 flex flex-wrap gap-2">
                {form.teams.map((team) => (
                  <li key={team} className="ad-pill">
                    {team}
                    <button type="button" aria-label={`Remove team ${team}`} onClick={() => set({ teams: form.teams.filter((other) => other !== team) })}>
                      <span aria-hidden>✕</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex max-w-md gap-2">
              <div className="min-w-0 flex-1">
                <input
                  className="field"
                  aria-labelledby={teamsLabel}
                  value={form.teamDraft}
                  onChange={(event) => set({ teamDraft: event.target.value })}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter') return
                    event.preventDefault() // add the team rather than saving the whole form
                    set({ teams: addTeam(form.teams, form.teamDraft), teamDraft: '' })
                  }}
                  maxLength={60}
                  placeholder="Add a team, for example Sales"
                />
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={!form.teamDraft.trim()}
                onClick={() => set({ teams: addTeam(form.teams, form.teamDraft), teamDraft: '' })}
              >
                Add team
              </button>
            </div>
            <p className="mt-1 text-xs text-muted">
              Optional. Players pick one of these when they join and the leaderboard compares the teams. With no teams, nobody is asked.
            </p>
          </div>
        </div>
      </Panel>

      <div id="confidentiality-panel">
        <Panel
          title="Report access & confidentiality"
          description="Control how players read the original report and keep sensitive documents private."
        >
        <div className="grid gap-4">
          <Field
            label="External report link (recommended)"
            hint="Optional. Link to your SharePoint, Google Drive, or Confluence doc so your company’s own SSO and permissions govern who can read it."
            error={badReportUrl ? 'Must start with https:// or http://.' : null}
          >
            {(props) => (
              <input
                className="field"
                {...props}
                type="url"
                value={form.reportUrl}
                onChange={(event) => set({ reportUrl: event.target.value })}
                placeholder="https://company.sharepoint.com/..."
                maxLength={500}
              />
            )}
          </Field>

          <div className="grid gap-1">
            <Switch
              checked={form.allowDownload}
              onChange={(checked) => set({ allowDownload: checked, ...(checked ? { purgeSource: false } : {}) })}
              label="Allow signed-in players to open/download the uploaded report"
              description={`Off by default. Streams the uploaded PDF/Word file via short-lived signed links in the lobby and results screen.${form.purgeSource ? ' Turning this on automatically turns off source-file purging below.' : ''}`}
            />
            <Switch
              checked={form.unlisted}
              onChange={(unlisted) => set({ unlisted })}
              label="Unlisted (direct link only)"
              description="Hides this quiz’s title from the public front door (/). Enabling this on a draft also updates the play link with an unguessable suffix."
            />
            <Switch
              checked={form.confidentialVoice}
              onChange={(confidentialVoice) => set({ confidentialVoice })}
              label="Confidential voice mode (no third-party ElevenLabs)"
              description="Prevents spoken questions and answers from being sent to ElevenLabs; uses your primary OpenAI/Azure tenant voice instead."
            />
            <Switch
              checked={form.purgeSource}
              onChange={(checked) => set({ purgeSource: checked, ...(checked ? { allowDownload: false } : {}) })}
              label="Purge source file after processing"
              description={`Automatically deletes the raw uploaded report file from storage once questions are generated (keeps questions and charts).${form.allowDownload ? ' Turning this on disables player file downloads above.' : ''}`}
            />
          </div>
        </div>
      </Panel>
      </div>

      <SaveBar dirty={dirty} saving={saving} error={error} onDiscard={() => set(detailsOf(quiz, rules))} />
    </form>
  )
}

// ---- duplicate & delete ----------------------------------------------------------------

function LifecyclePanel() {
  const { detail } = useQuiz()
  const navigate = useNavigate()
  const notify = useNotify()
  const { quiz, overview } = detail
  const [duplicating, setDuplicating] = useState(false)
  const [includeManual, setIncludeManual] = useState(false)
  const [dupBusy, setDupBusy] = useState(false)
  const [dupError, setDupError] = useState<string | null>(null)
  const [askingDelete, setAskingDelete] = useState(false)
  const [delBusy, setDelBusy] = useState(false)
  const [delError, setDelError] = useState<string | null>(null)

  const duplicate = async (event: FormEvent) => {
    event.preventDefault()
    if (dupBusy) return
    setDupBusy(true)
    setDupError(null)
    try {
      const res = await call<{ quiz: Quiz; copiedQuestions?: number }>(`/quizzes/${quiz.id}/duplicate`, {
        method: 'POST',
        body: { includeManualQuestions: includeManual },
      })
      setDuplicating(false)
      notify(`Created draft “${res.quiz.title}”.`)
      navigate(paths.quiz(res.quiz.id))
    } catch (reason) {
      setDupError(errorText(reason))
      setDupBusy(false)
    }
  }

  const remove = async () => {
    setDelBusy(true)
    setDelError(null)
    try {
      await call(`/quizzes/${quiz.id}`, { method: 'DELETE' })
      notify(`“${quiz.title}” was deleted.`)
      navigate(paths.home)
    } catch (reason) {
      setDelError(errorText(reason))
      setDelBusy(false)
    }
  }

  return (
    <div className="grid gap-5 md:grid-cols-2">
      <Panel
        title="Duplicate this quiz"
        description="Start a recurring meeting quiz with the same teams, allowed email domains, and play-style rules — ready for a new report."
      >
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            setIncludeManual(false)
            setDupError(null)
            setDuplicating(true)
          }}
        >
          Duplicate quiz…
        </button>
      </Panel>

      <Panel title="Delete this quiz" description="Removes the quiz, its report, its questions and everyone's results for good.">
        <button type="button" className="btn btn-danger" onClick={() => setAskingDelete(true)}>
          Delete quiz…
        </button>
      </Panel>

      <Dialog open={duplicating} onClose={dupBusy ? () => {} : () => setDuplicating(false)} title="Duplicate this quiz">
        <form className="grid gap-4" onSubmit={duplicate}>
          <p className="text-sm text-muted">
            Creates a new <strong>Draft</strong> quiz copying the title, description, teams, allowed email domains, and custom play-style rules.
            Uploaded reports, player results, and access codes are not copied.
          </p>
          {overview.manual > 0 && (
            <Switch
              checked={includeManual}
              onChange={setIncludeManual}
              label={`Also copy ${plural(overview.manual, 'hand-written question')}`}
              description="Copies text-only manual questions (picture questions stay tied to the original report)."
            />
          )}
          <ErrorNote>{dupError}</ErrorNote>
          <DialogActions>
            <button type="button" className="btn btn-ghost" onClick={() => setDuplicating(false)} disabled={dupBusy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={dupBusy}>
              {dupBusy ? 'Duplicating…' : 'Create copy'}
            </button>
          </DialogActions>
        </form>
      </Dialog>

      <ConfirmDialog
        open={askingDelete}
        title="Delete this quiz?"
        confirmLabel="Delete for good"
        busyLabel="Deleting…"
        typeToConfirm={quiz.title}
        busy={delBusy}
        error={delError}
        onConfirm={remove}
        onCancel={() => {
          setAskingDelete(false)
          setDelError(null)
        }}
      >
        <p>
          This permanently deletes <strong>{quiz.title}</strong>: the uploaded report, {plural(overview.approved + overview.pending, 'question')} and
          the results of {plural(overview.players, 'player')}, including past live games.
        </p>
        <p>It cannot be undone. If you only want to stop people playing, set the status to Closed instead.</p>
      </ConfirmDialog>
    </div>
  )
}
