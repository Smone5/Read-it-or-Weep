import { useMemo, useState, type FormEvent } from 'react'
import { call, errorText } from './api'
import { ChoiceGroup, ErrorBlock, LoadingBlock, NumberField, numberError, PageHeader, Panel, SaveBar, Segmented, Switch } from './components'
import { useNotify, usePageTitle, useUnsavedGuard } from './context'
import { decadeLabel } from './format'
import { forgetMeta, useLoad, useMeta } from './hooks'
import type { GameRules, ModeId, PictureMode, QuestionSources, SettingsRead, SettingsWritten, SoundtrackVolume } from './types'

const QUESTION_SOURCES: { value: QuestionSources; label: string; description: string }[] = [
  { value: 'all', label: 'AI and hand-written together', description: 'Mix generated questions with questions admins write.' },
  { value: 'manual', label: 'Hand-written only', description: 'Ignore AI-generated questions by default.' },
  { value: 'ai', label: 'AI-generated only', description: 'Use only questions written from the report.' },
]

const PICTURE_MODES: { value: PictureMode; label: string }[] = [
  { value: 'show', label: 'Show picture' },
  { value: 'auto', label: 'Let the quiz decide' },
  { value: 'memory', label: 'Test from memory' },
]

const VOLUMES: { value: SoundtrackVolume; label: string }[] = [
  { value: 'quiet', label: 'Quiet' },
  { value: 'medium', label: 'Medium' },
  { value: 'loud', label: 'Loud' },
]

const ALL_MODES: { id: ModeId; name: string }[] = [
  { id: 'show', name: 'Game Show' },
  { id: 'sudden_death', name: 'Sudden Death' },
  { id: 'speed', name: 'Speed Run' },
  { id: 'straight', name: 'Straight Quiz' },
  { id: 'jukebox', name: 'Office Jukebox' },
]

function cloneRules(rules: GameRules): GameRules {
  return JSON.parse(JSON.stringify(rules)) as GameRules
}

function validate(draft: GameRules): string | null {
  if (!draft.modes.length) return 'Keep at least one play style enabled.'
  if (draft.music_enabled && !draft.decades.length) return 'Pick at least one music decade when music is on.'
  const checks: (string | null)[] = [
    numberError(draft.ready_threshold, 1, 100),
    numberError(draft.show.questions, 3, 30),
    numberError(draft.show.timer_sec, 10, 120),
    numberError(draft.show.music_rounds, 0, 5),
    numberError(draft.sudden_death.strikes, 1, 9),
    numberError(draft.speed.clock_sec, 30, 600),
    numberError(draft.straight.pass_mark, 1, 100),
    numberError(draft.live.questions, 3, 50),
    numberError(draft.live.timer_sec, 5, 120),
  ]
  return checks.find(Boolean) ?? null
}

export default function GameDefaultsPage() {
  usePageTitle('Defaults')
  const settings = useLoad<SettingsRead<GameRules>>('/settings/game')
  const { meta, error: metaError, retry: retryMeta } = useMeta()
  const ready = settings.data !== null && meta !== null
  const error = settings.error ?? metaError

  return (
    <>
      <PageHeader
        title="Company defaults"
        description="Starting rules for every new quiz — and for existing quizzes that have not overridden a setting. Any individual quiz can still customize its own rules."
      />
      {!ready && !error && <LoadingBlock label="Loading company defaults" />}
      {!ready && error && (
        <ErrorBlock
          message={error}
          onRetry={() => {
            settings.reload()
            retryMeta()
          }}
        />
      )}
      {settings.data && meta && <DefaultsForm initial={settings.data.value} decades={meta.decades} />}
    </>
  )
}

function DefaultsForm({ initial, decades }: { initial: GameRules; decades: string[] }) {
  const notify = useNotify()
  const [saved, setSaved] = useState<GameRules>(() => cloneRules(initial))
  const [draft, setDraft] = useState<GameRules>(() => cloneRules(initial))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const savedJson = useMemo(() => JSON.stringify(saved), [saved])
  const dirty = JSON.stringify(draft) !== savedJson
  useUnsavedGuard(dirty)

  const update = (patch: Partial<GameRules>) => {
    setDraft((cur) => ({ ...cur, ...patch }))
    setError(null)
  }

  const toggleMode = (mode: ModeId) => {
    setDraft((cur) => {
      const has = cur.modes.includes(mode)
      if (has && cur.modes.length === 1) return cur
      const modes = has ? cur.modes.filter((m) => m !== mode) : [...cur.modes, mode]
      const default_mode = modes.includes(cur.default_mode) ? cur.default_mode : modes[0]
      return { ...cur, modes, default_mode }
    })
    setError(null)
  }

  const toggleDecade = (decade: string) => {
    setDraft((cur) => {
      const has = cur.decades.includes(decade)
      if (has && cur.decades.length === 1) return cur
      const next = has ? cur.decades.filter((d) => d !== decade) : [...cur.decades, decade]
      return { ...cur, decades: decades.filter((d) => next.includes(d)) }
    })
    setError(null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || !dirty) return
    const problem = validate(draft)
    if (problem) {
      setError(problem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = {
        modes: draft.modes,
        default_mode: draft.default_mode,
        auto_approve: draft.auto_approve,
        question_sources: draft.question_sources,
        music_enabled: draft.music_enabled,
        after_party: draft.after_party,
        soundtrack: draft.soundtrack,
        soundtrack_volume: draft.soundtrack_volume,
        decades: draft.decades,
        picture_mode: draft.picture_mode,
        ready_threshold: draft.ready_threshold,
        leaderboard: draft.leaderboard,
        allow_download: Boolean(draft.allow_download),
        unlisted: Boolean(draft.unlisted),
        confidential_voice: Boolean(draft.confidential_voice),
        purge_source_after_process: Boolean(draft.purge_source_after_process),
        show: draft.show,
        sudden_death: draft.sudden_death,
        speed: draft.speed,
        straight: draft.straight,
        live: draft.live,
      }
      const res = await call<SettingsWritten<GameRules>>('/settings/game', { method: 'PUT', body })
      forgetMeta()
      setSaved(cloneRules(res.value))
      setDraft(cloneRules(res.value))
      notify('Company defaults saved.')
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <Panel title="Confidentiality & report handling" description="Applied automatically to newly created quizzes (including unguessable links when Unlisted is on).">
        <div className="grid gap-1">
          <Switch
            checked={Boolean(draft.unlisted)}
            onChange={(unlisted) => update({ unlisted })}
            label="Unlisted by default (direct link only)"
            description="New quizzes get an unguessable link suffix and stay hidden from the public front door (/)."
          />
          <Switch
            checked={Boolean(draft.confidential_voice)}
            onChange={(confidential_voice) => update({ confidential_voice })}
            label="Confidential voice mode by default"
            description="Keeps spoken report questions and answers off third-party ElevenLabs synthesis."
          />
          <Switch
            checked={Boolean(draft.purge_source_after_process)}
            onChange={(purge_source_after_process) =>
              update({
                purge_source_after_process,
                ...(purge_source_after_process ? { allow_download: false } : {}),
              })
            }
            label="Purge raw report files after processing"
            description="Deletes uploaded PDFs/Word files from storage once questions and charts are extracted."
          />
          <Switch
            checked={Boolean(draft.allow_download)}
            onChange={(allow_download) =>
              update({
                allow_download,
                ...(allow_download ? { purge_source_after_process: false } : {}),
              })
            }
            label="Allow signed-in players to download uploaded reports"
            description="Off by default. Cannot be combined with source-file purging."
          />
        </div>
      </Panel>

      <Panel title="Play styles & readiness">
        <div className="grid gap-4">
          <div>
            <span className="label">Enabled play styles</span>
            <div className="flex flex-wrap gap-2">
              {ALL_MODES.map((m) => {
                const active = draft.modes.includes(m.id)
                return (
                  <button
                    key={m.id}
                    type="button"
                    aria-pressed={active}
                    className={`btn btn-sm ${active ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => toggleMode(m.id)}
                  >
                    {m.name}
                  </button>
                )
              })}
            </div>
          </div>
          {draft.modes.length > 1 && (
            <div>
              <span className="label">Default style selected first</span>
              <Segmented
                label="Default play style"
                value={draft.default_mode}
                onChange={(default_mode) => update({ default_mode })}
                options={draft.modes.map((id) => ({ value: id, label: ALL_MODES.find((m) => m.id === id)?.name ?? id }))}
              />
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField
              label="Readiness pass mark"
              value={draft.ready_threshold}
              onChange={(ready_threshold) => update({ ready_threshold })}
              min={1}
              max={100}
              unit="% of sections cleared"
            />
            <div>
              <span className="label">Picture questions</span>
              <Segmented label="Picture mode" options={PICTURE_MODES} value={draft.picture_mode} onChange={(picture_mode) => update({ picture_mode })} />
            </div>
          </div>
          <Switch
            checked={draft.leaderboard}
            onChange={(leaderboard) => update({ leaderboard })}
            label="Show leaderboard to players"
            description="Players see top scores and team standings after a game."
          />
        </div>
      </Panel>

      <Panel title="Question generation & review">
        <div className="grid gap-4">
          <Switch
            checked={draft.auto_approve}
            onChange={(auto_approve) => update({ auto_approve })}
            label="Approve AI-written questions automatically"
            description="When off, generated questions wait in review until an admin approves them."
          />
          <div>
            <span className="label">Default question sources</span>
            <ChoiceGroup
              label="Question sources"
              options={QUESTION_SOURCES}
              value={draft.question_sources}
              onChange={(question_sources) => update({ question_sources })}
            />
          </div>
        </div>
      </Panel>

      <Panel title="Music & soundtrack">
        <div className="grid gap-3">
          <Switch
            checked={draft.music_enabled}
            onChange={(music_enabled) => update({ music_enabled })}
            label="Include music breaks and soundtrack"
            description="Name That Tune rounds, background music, and after-party tracks."
          />
          {draft.music_enabled && (
            <>
              <div className="grid gap-1 sm:grid-cols-2">
                <Switch checked={draft.soundtrack} onChange={(soundtrack) => update({ soundtrack })} label="Background soundtrack during questions" />
                <Switch checked={draft.after_party} onChange={(after_party) => update({ after_party })} label="After-party music on results screen" />
              </div>
              {draft.soundtrack && (
                <div>
                  <span className="label">Soundtrack volume</span>
                  <Segmented label="Soundtrack volume" options={VOLUMES} value={draft.soundtrack_volume} onChange={(soundtrack_volume) => update({ soundtrack_volume })} />
                </div>
              )}
              <div>
                <span className="label">Default music decades</span>
                <div className="flex flex-wrap gap-2">
                  {decades.map((d) => {
                    const on = draft.decades.includes(d)
                    return (
                      <button
                        key={d}
                        type="button"
                        aria-pressed={on}
                        className={`btn btn-sm ${on ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => toggleDecade(d)}
                      >
                        {decadeLabel(d)}
                      </button>
                    )
                  })}
                </div>
              </div>
            </>
          )}
        </div>
      </Panel>

      <Panel title="Default timers & round sizes" description="Per-mode starting numbers. Each quiz can fine-tune these on its Play Styles tab.">
        <details>
          <summary className="cursor-pointer text-sm font-semibold">
            Game Show ({draft.show.questions} Qs · {draft.show.timer_sec}s) · Sudden Death ({draft.sudden_death.strikes} strikes) · Speed ({draft.speed.clock_sec}s) · Live ({draft.live.questions} Qs)
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <NumberField label="Game Show questions" value={draft.show.questions} onChange={(questions) => update({ show: { ...draft.show, questions } })} min={3} max={30} />
            <NumberField label="Game Show timer" value={draft.show.timer_sec} onChange={(timer_sec) => update({ show: { ...draft.show, timer_sec } })} min={10} max={120} unit="sec" />
            <NumberField label="Game Show music breaks" value={draft.show.music_rounds} onChange={(music_rounds) => update({ show: { ...draft.show, music_rounds } })} min={0} max={5} />
            <NumberField label="Sudden Death strikes" value={draft.sudden_death.strikes} onChange={(strikes) => update({ sudden_death: { ...draft.sudden_death, strikes } })} min={1} max={9} />
            <NumberField label="Speed Run clock" value={draft.speed.clock_sec} onChange={(clock_sec) => update({ speed: { ...draft.speed, clock_sec } })} min={30} max={600} unit="sec" />
            <NumberField label="Straight Quiz pass mark" value={draft.straight.pass_mark} onChange={(pass_mark) => update({ straight: { ...draft.straight, pass_mark } })} min={1} max={100} unit="%" />
            <NumberField label="Live game questions" value={draft.live.questions} onChange={(questions) => update({ live: { ...draft.live, questions } })} min={3} max={50} />
            <NumberField label="Live game timer" value={draft.live.timer_sec} onChange={(timer_sec) => update({ live: { ...draft.live, timer_sec } })} min={5} max={120} unit="sec" />
          </div>
        </details>
      </Panel>

      <SaveBar
        dirty={dirty}
        saving={saving}
        error={error}
        onDiscard={() => {
          setDraft(cloneRules(saved))
          setError(null)
        }}
      />
    </form>
  )
}
