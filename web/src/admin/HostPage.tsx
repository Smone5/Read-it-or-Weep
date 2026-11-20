import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ErrorNote } from '../components/ui'
import { call, errorText, isAbort } from './api'
import { Bar, ChoiceGroup, ErrorBlock, Field, LoadingBlock, Note, PageHeader, Panel, SaveBar, Segmented, Switch } from './components'
import { useNotify, usePageTitle } from './context'
import { useLoad, useMeta } from './hooks'
import type {
  HostManner,
  HostPace,
  HostSample,
  HostSampleRequest,
  HostSettings,
  HostSettingsWrite,
  SettingsRead,
  SettingsWritten,
  MannerMeta,
  VoiceInfo,
  VoiceQuality,
  VoiceRegister,
} from './types'

export default function HostPage() {
  usePageTitle('Host')
  const settings = useLoad<SettingsRead<HostSettings>>('/settings/host')
  const { meta, error: metaError, retry: retryMeta } = useMeta()
  const ready = settings.data !== null && meta !== null
  const error = settings.error ?? metaError

  return (
    <>
      <PageHeader title="Host" description="The talking game-show host: who they are, how they sound and how cheeky they get. This applies to every quiz." />
      {!ready && !error && <LoadingBlock label="Loading the host" />}
      {!ready && error && (
        <ErrorBlock
          message={error}
          onRetry={() => {
            settings.reload()
            retryMeta()
          }}
        />
      )}
      {settings.data && meta && (
        <HostForm
          initial={settings.data.value}
          voices={meta.voices}
          voiceInfo={meta.voiceInfo ?? {}}
          manners={meta.manners?.length ? meta.manners : FALLBACK_MANNERS}
          aiConfigured={meta.aiConfigured}
          elevenConfigured={meta.elevenConfigured ?? false}
        />
      )}
    </>
  )
}

// ---- wording --------------------------------------------------------------------------

const MAX_CHARACTERS = 4
const NAME_MAX = 30
const INSTRUCTIONS_MAX = 600

/** A short name for each band of the scale. The fuller description comes from the server with each sample. */
function sarcasmLabel(level: number): string {
  if (level <= 1) return 'Warm, no teasing'
  if (level <= 3) return 'Light and friendly'
  if (level <= 5) return 'Playful'
  if (level <= 7) return 'Properly sarcastic'
  if (level <= 9) return 'Very sarcastic'
  return 'Maximum roast'
}

const REGISTER_WORDS: Record<VoiceRegister, string> = {
  deeper: 'deeper voice',
  'lower-mid': 'lower voice',
  middle: 'mid-range voice',
  higher: 'higher voice',
}

/** For example "Classic game-show host" or "cedar — lower voice, smooth tone". */
function voiceLabel(voice: string, info: VoiceInfo): string {
  const facts = info[voice]
  if (!facts) return voice
  if (facts.provider === 'elevenlabs') return facts.label
  return `${voice} — ${REGISTER_WORDS[facts.register] ?? `${facts.register} voice`}, ${facts.tone} tone`
}

const isEleven = (voice: string, info: VoiceInfo) => info[voice]?.provider === 'elevenlabs'

/** What the admin is told under the voice list. */
function voiceHint(voice: string, info: VoiceInfo, elevenConfigured: boolean): ReactNode {
  const facts = info[voice]
  if (!facts) return undefined
  if (facts.provider === 'elevenlabs') {
    if (!elevenConfigured) {
      return `${facts.blurb} Needs an ElevenLabs key (ELEVENLABS_API_KEY); until one is set, the ${facts.fallback} voice stands in.`
    }
    if (facts.inAccount === false) {
      return (
        <>
          {facts.blurb} Not in your ElevenLabs account yet:{' '}
          <a className="underline" href={facts.addLink} target="_blank" rel="noreferrer">
            add it to My Voices
          </a>
          . Until then, the {facts.fallback} voice stands in.
        </>
      )
    }
    return `${facts.blurb} Performed by ElevenLabs.`
  }
  return `Measured: pitch about ${facts.pitchHz} Hz, clarity ${facts.clarityDb} dB.`
}

// When this much of the month's ElevenLabs plan is used, new lines switch to the backup voice (lines already
// recorded still play in the real one). "Off": only when ElevenLabs itself refuses, the plan being used up.
const BUDGETS: { value: string; label: string }[] = [
  { value: '50', label: '50%' },
  { value: '75', label: '75%' },
  { value: '90', label: '90%' },
  { value: '0', label: 'Off' },
]

const QUALITIES: { value: VoiceQuality; label: string; description: string }[] = [
  {
    value: 'premium',
    label: 'Premium (recommended)',
    description: 'Acts the part: follows direction about pace and timing, and writes the sharper jokes (about 17 cents of AI cost per game).',
  },
  {
    value: 'standard',
    label: 'Standard',
    description: 'About 5 cents per game. Tends to talk at one speed whatever the direction, and the jokes are weaker.',
  },
]

const PACES: { value: HostPace; label: string }[] = [
  { value: 'relaxed', label: 'Slower' },
  { value: 'snappy', label: 'Natural (recommended)' },
  { value: 'rapid', label: 'Faster' },
]

/** Used only if the server is older than this page and does not list the manners itself. */
const FALLBACK_MANNERS: MannerMeta[] = [
  { id: 'wiseguy', label: 'Wiseguy', blurb: 'A cocky, grinning smart-aleck having more fun than anyone. Lively and playful.' },
  { id: 'deadpan', label: 'Deadpan', blurb: 'Dry and unhurried: a comedy writer in a radio booth, amused and in no rush.' },
  { id: 'showman', label: 'Showman', blurb: 'A larger-than-life prime-time host who knows exactly how silly this show is. Big and theatrical.' },
]

// ---- the form's working copy -------------------------------------------------------------

/** `key` only tells the rows apart on screen; the server gives each character its real id on saving. */
type Character = { key: number; name: string; voice: string; style: string; manner: HostManner }

type Draft = Omit<HostSettingsWrite, 'hosts'> & { hosts: Character[] }

let nextKey = 1

function draftOf(value: HostSettings, fallbackVoice: string): Draft {
  // An install saved before characters existed has only the single `name` and `voice`.
  const hosts: { name: string; voice: string; style?: string; manner?: HostManner }[] = value.hosts?.length
    ? value.hosts
    : [{ name: value.name || 'Max', voice: value.voice || fallbackVoice }]
  return {
    sarcasm: value.sarcasm,
    voice_enabled: value.voice_enabled,
    talk_enabled: value.talk_enabled ?? true,
    custom_instructions: value.custom_instructions ?? '',
    player_can_choose: value.player_can_choose ?? true,
    voice_quality: value.voice_quality ?? 'standard',
    pace: value.pace ?? 'snappy',
    voice_budget: value.voice_budget ?? 90,
    hosts: hosts
      .slice(0, MAX_CHARACTERS)
      .map((host) => ({ key: nextKey++, name: host.name, voice: host.voice, style: host.style ?? '', manner: host.manner ?? 'wiseguy' })),
  }
}

/** Exactly what PUT /settings/host expects. `name` and `voice` at the top level are copies and are never sent. */
const bodyOf = (draft: Draft): HostSettingsWrite => ({
  sarcasm: draft.sarcasm,
  voice_enabled: draft.voice_enabled,
  talk_enabled: draft.talk_enabled,
  custom_instructions: draft.custom_instructions.trim(),
  player_can_choose: draft.player_can_choose,
  voice_quality: draft.voice_quality,
  pace: draft.pace,
  voice_budget: draft.voice_budget,
  hosts: draft.hosts.map((host) => ({ name: host.name.trim(), voice: host.voice, style: host.style.trim(), manner: host.manner })),
})

function problemWith(draft: Draft): string | null {
  const names = draft.hosts.map((host) => host.name.trim().toLowerCase())
  if (names.some((name) => !name)) return 'Give every character a name.'
  if (new Set(names).size !== names.length) return 'Two characters have the same name. Give each one a different name.'
  return null
}

// ---- samples ------------------------------------------------------------------------------

type Sample =
  | { status: 'loading' }
  | { status: 'ready'; line: string; audio: string | null; blocked: boolean }
  | { status: 'error'; message: string }

// Ten milliseconds of silence. Browsers only let a page start sound in direct response to a
// click, and a sample takes a few seconds to arrive, so the player is started on this at the
// moment of the click and then handed the real recording.
const SILENCE =
  'data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA=='

function HostForm({
  initial,
  voices,
  voiceInfo,
  manners,
  aiConfigured,
  elevenConfigured,
}: {
  initial: HostSettings
  voices: string[]
  voiceInfo: VoiceInfo
  manners: MannerMeta[]
  aiConfigured: boolean
  elevenConfigured: boolean
}) {
  const notify = useNotify()
  const fallbackVoice = voices[0] ?? ''
  const [saved, setSaved] = useState(initial)
  const [draft, setDraft] = useState<Draft>(() => draftOf(initial, fallbackVoice))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** One entry per character (by `key`) that has had a sample asked for. */
  const [samples, setSamples] = useState<Record<number, Sample>>({})
  /** The character whose sample is sounding right now. */
  const [playing, setPlaying] = useState<number | null>(null)
  /** The server's own description of each sarcasm level, as samples bring them in. */
  const [styles, setStyles] = useState<Record<number, string>>({})
  const player = useRef<HTMLAudioElement | null>(null)
  const request = useRef<{ id: number; controller: AbortController } | null>(null)
  const requestCount = useRef(0)

  // The ElevenLabs host voices first, in the server's order; then OpenAI's, lowest first, so they read as a scale.
  const voiceOptions = useMemo(() => {
    const pitch = (voice: string) => {
      const facts = voiceInfo[voice]
      return facts && facts.provider !== 'elevenlabs' ? facts.pitchHz : Infinity
    }
    const elevenVoices = voices.filter((voice) => isEleven(voice, voiceInfo))
    const openaiVoices = voices.filter((voice) => !isEleven(voice, voiceInfo)).sort((a, b) => pitch(a) - pitch(b) || a.localeCompare(b))
    const known = [...elevenVoices, ...openaiVoices]
    const extra = draft.hosts.map((host) => host.voice).filter((voice) => voice && !known.includes(voice))
    return [...known, ...new Set(extra)]
  }, [voices, voiceInfo, draft.hosts])

  const savedBody = useMemo(() => JSON.stringify(bodyOf(draftOf(saved, fallbackVoice))), [saved, fallbackVoice])
  const dirty = JSON.stringify(bodyOf(draft)) !== savedBody

  const stopSound = useCallback(() => {
    const audio = player.current
    if (audio) {
      audio.onended = null
      audio.pause()
    }
    player.current = null
    setPlaying(null)
  }, [])

  /** Stop whatever is sounding and give up on a sample that is still on its way. */
  const cancelSample = useCallback(() => {
    request.current?.controller.abort()
    request.current = null
    stopSound()
  }, [stopSound])

  useEffect(() => cancelSample, [cancelSample]) // leaving the page silences it

  const startSound = (audio: HTMLAudioElement, key: number, onBlocked: () => void) => {
    player.current = audio
    audio.onended = () => {
      if (player.current !== audio) return
      player.current = null
      setPlaying(null)
    }
    audio.play().then(
      () => {
        if (player.current === audio) setPlaying(key)
      },
      () => {
        if (player.current !== audio) return
        player.current = null
        onBlocked()
      },
    )
  }

  const forgetSample = (key: number) =>
    setSamples((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })

  /** Ask for a line spoken by this character, using the settings on screen (saved or not), and play it. */
  const hear = (character: Character) => {
    cancelSample()
    const controller = new AbortController()
    const id = ++requestCount.current
    request.current = { id, controller }

    const audio = new Audio(SILENCE)
    audio.play().catch(() => {}) // still inside the click: this is what lets the recording play later

    const body: HostSampleRequest = {
      sarcasm: draft.sarcasm,
      name: character.name.trim() || 'Max',
      voice: character.voice,
      quality: draft.voice_quality,
      pace: draft.pace,
      style: character.style.trim() || undefined,
      manner: character.manner,
    }
    setSamples((current) => {
      const next: Record<number, Sample> = {}
      for (const [key, sample] of Object.entries(current)) if (sample.status !== 'loading') next[Number(key)] = sample
      next[character.key] = { status: 'loading' }
      return next
    })

    call<HostSample>('/host/sample', { method: 'POST', body, signal: controller.signal }).then(
      (result) => {
        if (request.current?.id !== id) return // another sample was asked for meanwhile
        request.current = null
        setStyles((current) => ({ ...current, [body.sarcasm]: result.style }))
        setSamples((current) => ({ ...current, [character.key]: { status: 'ready', line: result.line, audio: result.audio, blocked: false } }))
        if (!result.audio) return
        audio.src = result.audio
        startSound(audio, character.key, () =>
          setSamples((current) => {
            const sample = current[character.key]
            return sample?.status === 'ready' ? { ...current, [character.key]: { ...sample, blocked: true } } : current
          }),
        )
      },
      (reason: unknown) => {
        if (isAbort(reason) || request.current?.id !== id) return
        request.current = null
        setSamples((current) => ({ ...current, [character.key]: { status: 'error', message: errorText(reason) } }))
      },
    )
  }

  const replay = (key: number, source: string) => {
    cancelSample()
    setSamples((current) => {
      const sample = current[key]
      return sample?.status === 'ready' ? { ...current, [key]: { ...sample, blocked: false } } : current
    })
    startSound(new Audio(source), key, () => {})
  }

  const change = (patch: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...patch }))
    setError(null)
  }

  /** For settings a sample depends on: the samples on screen no longer match, so they go. */
  const changeSound = (patch: Partial<Draft>) => {
    cancelSample()
    setSamples({})
    change(patch)
  }

  const changeCharacter = (key: number, patch: Partial<Pick<Character, 'name' | 'voice' | 'style' | 'manner'>>) => {
    cancelSample()
    forgetSample(key)
    change({ hosts: draft.hosts.map((host) => (host.key === key ? { ...host, ...patch } : host)) })
  }

  const addCharacter = () => {
    const used = new Set(draft.hosts.map((host) => host.voice))
    const voice = voiceOptions.find((option) => !used.has(option)) ?? fallbackVoice
    change({ hosts: [...draft.hosts, { key: nextKey++, name: '', voice, style: '', manner: 'wiseguy' }] })
  }

  const removeCharacter = (key: number) => {
    cancelSample()
    forgetSample(key)
    change({ hosts: draft.hosts.filter((host) => host.key !== key) })
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || !dirty) return
    const problem = problemWith(draft)
    if (problem) {
      setError(problem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await call<SettingsWritten<HostSettings>>('/settings/host', { method: 'PUT', body: bodyOf(draft) })
      cancelSample()
      setSamples({})
      setSaved(result.value)
      setDraft(draftOf(result.value, fallbackVoice))
      notify('Host saved. New games use it straight away.')
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  const names = draft.hosts.map((host) => host.name.trim().toLowerCase())
  const waiting = Object.values(samples).some((sample) => sample.status === 'loading')

  return (
    // A flex column, not a grid, so the save bar (position: sticky) can follow the screen.
    <form onSubmit={submit} className="flex flex-col gap-5">
      {!aiConfigured && (
        <Note tone="warn" role="status">
          <strong>The AI service is not set up on this server</strong>, so the host cannot speak and samples are not available. You can still set
          everything up here for when it is.
        </Note>
      )}

      <Panel
        title="Host characters"
        description="One to four characters, each with a name and a voice. The first one is the host everyone gets unless players are allowed to choose."
      >
        <ol className="grid gap-3">
          {draft.hosts.map((character, index) => {
            const sample = samples[character.key]
            const name = character.name.trim().toLowerCase()
            const nameError = !name ? 'Give this character a name.' : names.indexOf(name) !== index ? 'Another character already has this name.' : null
            return (
              <li key={character.key} className="rounded-xl border border-line p-3 sm:p-4">
                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
                  <Field label={index === 0 ? 'Name (the main host)' : `Name of character ${index + 1}`} error={character.name === '' && !error ? null : nameError}>
                    {(props) => (
                      <input
                        className="field"
                        {...props}
                        value={character.name}
                        onChange={(event) => changeCharacter(character.key, { name: event.target.value })}
                        maxLength={NAME_MAX}
                        autoComplete="off"
                        placeholder="For example Max"
                      />
                    )}
                  </Field>
                  <Field
                    label="Voice"
                    hint={voiceHint(character.voice, voiceInfo, elevenConfigured)}
                  >
                    {(props) => (
                      <select className="field" {...props} value={character.voice} onChange={(event) => changeCharacter(character.key, { voice: event.target.value })}>
                        <optgroup label="ElevenLabs host voices">
                          {voiceOptions.filter((voice) => isEleven(voice, voiceInfo)).map((voice) => (
                            <option key={voice} value={voice}>
                              {voiceLabel(voice, voiceInfo)}
                            </option>
                          ))}
                        </optgroup>
                        <optgroup label="OpenAI voices">
                          {voiceOptions.filter((voice) => !isEleven(voice, voiceInfo)).map((voice) => (
                            <option key={voice} value={voice}>
                              {voiceLabel(voice, voiceInfo)}
                            </option>
                          ))}
                        </optgroup>
                      </select>
                    )}
                  </Field>
                </div>

                <div className="mt-3">
                  <Field label="How they perform" hint={manners.find((manner) => manner.id === character.manner)?.blurb}>
                    {(props) => (
                      <select
                        className="field"
                        {...props}
                        value={character.manner}
                        onChange={(event) => changeCharacter(character.key, { manner: event.target.value as HostManner })}
                      >
                        {manners.map((manner) => (
                          <option key={manner.id} value={manner.id}>
                            {manner.label}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                </div>

                <div className="mt-3">
                  <Field
                    label="Anything particular about this character (optional)"
                    hint="One extra line of direction, on top of the manner above. Use Hear a sample to judge any change by ear."
                  >
                    {(props) => (
                      <input
                        className="field"
                        {...props}
                        value={character.style}
                        onChange={(event) => changeCharacter(character.key, { style: event.target.value })}
                        maxLength={300}
                        autoComplete="off"
                        placeholder="For example: a little exasperated, a laugh never far away"
                      />
                    )}
                  </Field>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => hear(character)}
                    disabled={!aiConfigured || sample?.status === 'loading'}
                    aria-label={`Hear a sample of ${character.name.trim() || `character ${index + 1}`}`}
                  >
                    {sample?.status === 'loading' ? 'Getting a sample…' : 'Hear a sample'}
                  </button>
                  {sample?.status === 'ready' &&
                    sample.audio &&
                    (playing === character.key ? (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={stopSound}>
                        Stop
                      </button>
                    ) : (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => replay(character.key, sample.audio ?? '')}>
                        {sample.blocked ? 'Play' : 'Play again'}
                      </button>
                    ))}
                  <span className="ml-auto">
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => removeCharacter(character.key)}
                      disabled={draft.hosts.length === 1}
                      title={draft.hosts.length === 1 ? 'There must be at least one character' : undefined}
                      aria-label={`Remove ${character.name.trim() || `character ${index + 1}`}`}
                    >
                      Remove
                    </button>
                  </span>
                </div>

                <div role="status">
                  {sample?.status === 'loading' && <p className="mt-2 text-sm text-muted">Writing a line and recording it. This takes a few seconds.</p>}
                  {sample?.status === 'ready' && (
                    <figure className="mt-3 rounded-lg bg-surface-2 p-3">
                      <blockquote className="text-sm font-semibold">“{sample.line}”</blockquote>
                      <figcaption className="mt-1 text-xs text-muted">
                        {sample.blocked
                          ? 'Your browser did not let the sound start by itself. Press Play to hear it.'
                          : sample.audio
                            ? `${character.name.trim() || 'The host'}, reacting to a wrong answer about quarterly revenue.`
                            : 'No recording came back, so here is the line in writing.'}
                      </figcaption>
                    </figure>
                  )}
                </div>
                {sample?.status === 'error' && (
                  <div className="mt-3">
                    <ErrorNote>{sample.message}</ErrorNote>
                  </div>
                )}
              </li>
            )
          })}
        </ol>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
          <button type="button" className="btn btn-ghost" onClick={addCharacter} disabled={draft.hosts.length >= MAX_CHARACTERS}>
            Add a character
          </button>
          <span className="text-xs text-muted">
            {draft.hosts.length >= MAX_CHARACTERS ? 'Four is the most.' : 'Up to four.'} Samples use what is on this page right now, saved or not
            {waiting ? '.' : ', and each one is a new line.'}
          </span>
        </div>
        <p className="mt-3 text-xs text-muted">
          Voices are listed from the lowest to the highest. The pitch and tone shown are measured from the audio; whether a voice suits your host is
          best judged by ear.
        </p>

        <div className="mt-3 border-t border-line pt-2">
          <Switch
            checked={draft.player_can_choose}
            onChange={(checked) => change({ player_can_choose: checked })}
            label="Let players choose their host in the lobby"
            description={
              draft.hosts.length < 2
                ? 'With only one character there is nothing to choose. Add a second one to offer a choice.'
                : 'When off, everyone gets the first character.'
            }
          />
        </div>
      </Panel>

      <Panel title="Personality" description="How much the host teases. Move the slider, then use “Hear a sample” above to judge it before you save.">
        <Field label="Sarcasm">
          {(props) => (
            <input
              type="range"
              min={0}
              max={10}
              step={1}
              {...props}
              value={draft.sarcasm}
              onChange={(event) => changeSound({ sarcasm: Number(event.target.value) })}
              aria-valuetext={`${draft.sarcasm} out of 10: ${sarcasmLabel(draft.sarcasm)}`}
            />
          )}
        </Field>
        <div className="flex justify-between text-xs text-muted" aria-hidden>
          <span>0 · Warm</span>
          <span>10 · Full roast</span>
        </div>
        <p className="mt-3 text-sm">
          <strong>
            {draft.sarcasm} out of 10: {sarcasmLabel(draft.sarcasm)}.
          </strong>{' '}
          {styles[draft.sarcasm] ?? ''}
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted">
          <li>At every level the host stays workplace-safe: jokes are about the answers and office life, never about a person.</li>
          <li>The Straight Quiz style always uses the warmest setting, whatever is chosen here.</li>
        </ul>

        <div className="mt-5">
          <Field
            label="Extra instructions for the host (optional)"
            hint={`${draft.custom_instructions.length} of ${INSTRUCTIONS_MAX} characters. Plain sentences work best: what to mention, what to stay away from.`}
          >
            {(props) => (
              <textarea
                className="field"
                {...props}
                value={draft.custom_instructions}
                onChange={(event) => change({ custom_instructions: event.target.value })}
                maxLength={INSTRUCTIONS_MAX}
                rows={4}
                placeholder="For example: We are a logistics company, so the odd trucking pun is welcome. Never joke about job cuts. Call the chief executive “the boss”."
              />
            )}
          </Field>
        </div>
      </Panel>

      <VoiceUsage />

      <Panel title="Voice">
        <Switch
          checked={draft.voice_enabled}
          onChange={(checked) => change({ voice_enabled: checked })}
          label="The host speaks out loud"
          description="When off, the host's lines appear on screen as text only."
        />
        <Switch
          checked={draft.talk_enabled}
          onChange={(checked) => change({ talk_enabled: checked })}
          label="Let players talk to the host (hold-to-talk microphone)"
          description="The microphone is on only while a player holds the button."
        />

        <div className="mt-3 grid gap-x-6 gap-y-5 border-t border-line pt-4 lg:grid-cols-2">
          <div>
            <span className="label">Quality</span>
            <ChoiceGroup label="Voice quality" options={QUALITIES} value={draft.voice_quality} onChange={(quality) => changeSound({ voice_quality: quality })} />
            <div className="mt-4">
              <span className="label">Save the ElevenLabs plan</span>
              <Segmented label="Switch to the backup voice when this much of the month's plan is used" options={BUDGETS} value={String(draft.voice_budget)} onChange={(budget) => change({ voice_budget: Number(budget) })} />
              <p className="mt-1 text-xs text-muted">
                When this much of the month's ElevenLabs characters is used, new lines switch to the closest OpenAI voice until the count
                starts again. Lines already recorded (round introductions, the final question) keep playing in the real voice.
              </p>
            </div>
          </div>
          <div>
            <span className="label">How fast the host talks</span>
            <Segmented label="How fast the host talks" options={PACES} value={draft.pace} onChange={(pace) => changeSound({ pace })} />
            <p className="mt-1 text-xs text-muted">Samples are spoken with the quality and pace chosen here.</p>
          </div>
        </div>
      </Panel>

      <SaveBar
        dirty={dirty}
        saving={saving}
        error={error}
        onDiscard={() => {
          cancelSample()
          setSamples({})
          setDraft(draftOf(saved, fallbackVoice))
          setError(null)
        }}
      />
    </form>
  )
}

type VoiceUsageRead =
  | { configured: false }
  | { configured: true; error: string }
  | { configured: true; error?: undefined; tier: string | null; used: number; limit: number; resetsAt: string | null; perGame: number; gamesLeft: number | null }

/** How much of the ElevenLabs plan this month's games have used: the host's voice is paid for by the character. */
function VoiceUsage() {
  const { data, error } = useLoad<VoiceUsageRead>('/voice-usage')
  if (error) return null // the rest of the page still works
  if (!data) return null
  if (!data.configured) {
    return (
      <Panel title="Voice usage">
        <p className="text-sm text-muted">No ElevenLabs key is set, so the host speaks with OpenAI's voices (paid with the OpenAI account).</p>
      </Panel>
    )
  }
  if (data.error !== undefined) {
    return (
      <Panel title="Voice usage">
        <Note tone="warn">
          ElevenLabs did not say how much has been used: {data.error} If the key is not allowed to read the account, give it
          the "User: Read" permission in ElevenLabs.
        </Note>
      </Panel>
    )
  }
  const share = data.limit > 0 ? data.used / data.limit : 0
  const resets = data.resetsAt ? new Date(data.resetsAt).toLocaleDateString(undefined, { day: 'numeric', month: 'long' }) : null
  return (
    <Panel title="Voice usage" description="The host's ElevenLabs voice is paid for by the character. This is this month's use of the plan.">
      <p className="text-sm">
        <b>{data.used.toLocaleString()}</b> of {data.limit.toLocaleString()} characters used ({Math.round(share * 100)}%)
        {data.tier ? <span className="text-muted"> · {data.tier.charAt(0).toUpperCase() + data.tier.slice(1)} plan</span> : null}
      </p>
      <div className="my-2">
        <Bar value={data.used} max={data.limit} label="Characters used this month" tone={share > 0.9 ? 'var(--bad)' : share > 0.75 ? 'var(--accent)' : undefined} />
      </div>
      <p className="text-xs text-muted">
        {data.gamesLeft !== null ? <>Roughly {data.gamesLeft.toLocaleString()} more games this month, at about {data.perGame.toLocaleString()} characters a game. </> : null}
        {resets ? <>The count starts again on {resets}. </> : null}
        If it runs out, the host carries on in the closest OpenAI voice.
      </p>
    </Panel>
  )
}
