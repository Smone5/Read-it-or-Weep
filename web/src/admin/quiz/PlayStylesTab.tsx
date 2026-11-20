import { useId, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { errorText } from '../api'
import { Badge, ChoiceGroup, Note, NumberField, numberError, Panel, SaveBar, Segmented, Switch } from '../components'
import { useNotify } from '../context'
import { decadeLabel } from '../format'
import { paths } from '../paths'
import type { DeepPartial, GameRules, ModeId, PictureMode, SoundtrackVolume } from '../types'
import { useQuiz } from './context'

// ---- the numbers ----------------------------------------------------------------------
// The server stores whatever it is sent and reads every one of these with int(), so the
// limits here are the only thing standing between a typo and a broken game.

type NumberSpec = {
  id: string
  label: string
  min: number
  max: number
  step?: number
  unit?: string
  hint?: string
  get: (rules: GameRules) => number
  set: (rules: GameRules, value: number) => GameRules
}

type Extra = { step?: number; unit?: string; hint?: string }
type ShowNumber = 'questions' | 'music_rounds' | 'lightning_items' | 'second_chance' | 'timer_sec' | 'picture_timer_sec'

const show = (key: ShowNumber, label: string, min: number, max: number, extra: Extra = {}): NumberSpec => ({
  id: `show.${key}`,
  label,
  min,
  max,
  ...extra,
  get: (rules) => rules.show[key],
  set: (rules, value) => ({ ...rules, show: { ...rules.show, [key]: value } }),
})

const lifeline = (key: keyof GameRules['show']['lifelines'], label: string, hint: string): NumberSpec => ({
  id: `show.lifelines.${key}`,
  label,
  min: 0,
  max: 5,
  unit: 'per game',
  hint,
  get: (rules) => rules.show.lifelines[key],
  set: (rules, value) => ({ ...rules, show: { ...rules.show, lifelines: { ...rules.show.lifelines, [key]: value } } }),
})

const suddenDeath = (key: keyof GameRules['sudden_death'], label: string, min: number, max: number, extra: Extra = {}): NumberSpec => ({
  id: `sudden_death.${key}`,
  label,
  min,
  max,
  ...extra,
  get: (rules) => rules.sudden_death[key],
  set: (rules, value) => ({ ...rules, sudden_death: { ...rules.sudden_death, [key]: value } }),
})

const speed = (key: keyof GameRules['speed'], label: string, min: number, max: number, extra: Extra = {}): NumberSpec => ({
  id: `speed.${key}`,
  label,
  min,
  max,
  ...extra,
  get: (rules) => rules.speed[key],
  set: (rules, value) => ({ ...rules, speed: { ...rules.speed, [key]: value } }),
})

const straight = (key: keyof GameRules['straight'], label: string, min: number, max: number, extra: Extra = {}): NumberSpec => ({
  id: `straight.${key}`,
  label,
  min,
  max,
  ...extra,
  get: (rules) => rules.straight[key],
  set: (rules, value) => ({ ...rules, straight: { ...rules.straight, [key]: value } }),
})

const live = (key: keyof GameRules['live'], label: string, min: number, max: number, extra: Extra = {}): NumberSpec => ({
  id: `live.${key}`,
  label,
  min,
  max,
  ...extra,
  get: (rules) => rules.live[key],
  set: (rules, value) => ({ ...rules, live: { ...rules.live, [key]: value } }),
})

const MODE_NUMBERS: Record<ModeId, NumberSpec[]> = {
  show: [
    show('questions', 'Questions', 3, 30, { hint: 'Report questions in a game, counting the picture round and the final question.' }),
    show('music_rounds', 'Music breaks', 0, 5, { hint: 'Only when music is switched on below.' }),
    show('lightning_items', 'Lightning statements', 0, 20, { hint: 'Fact-or-Fake statements in the rapid-fire round. Fewer than 4 skips the round.' }),
    show('second_chance', 'Second-chance questions', 0, 5, { hint: 'Missed questions that come back for half points.' }),
    show('timer_sec', 'Time per question', 10, 120, { unit: 'seconds' }),
    show('picture_timer_sec', 'Time per picture question', 10, 180, { unit: 'seconds' }),
  ],
  sudden_death: [
    suddenDeath('strikes', 'Strikes', 1, 9, { hint: 'Wrong answers allowed before the game is over. A right answer in a music break wins one back.' }),
    suddenDeath('timer_start_sec', 'Time per question at the start', 10, 120, { unit: 'seconds' }),
    suddenDeath('timer_min_sec', 'Shortest time per question', 5, 60, { unit: 'seconds', hint: 'The clock loses 3 seconds each level, down to this.' }),
    suddenDeath('music_every', 'Questions per level', 3, 20, { hint: 'With music on, the last one of each level is a music break.' }),
    suddenDeath('max_questions', 'Most questions in a game', 5, 200, { hint: 'The game ends here even with strikes left.' }),
  ],
  speed: [
    speed('clock_sec', 'Time on the clock', 30, 600, { unit: 'seconds' }),
    speed('wrong_penalty_sec', 'Time lost per wrong answer', 0, 30, { unit: 'seconds' }),
  ],
  straight: [
    straight('per_section', 'Questions per section', 1, 10),
    straight('max_questions', 'Most questions in a quiz', 3, 100),
    straight('pass_mark', 'Pass mark', 1, 100, { unit: '% correct' }),
  ],
}

const LIFELINES: NumberSpec[] = [
  lifeline('fifty', '50/50', 'Takes away two wrong answers.'),
  lifeline('peek', 'Peek', 'Shows the part of the report the answer is in.'),
  lifeline('hint', 'Hint', 'The host gives a clue.'),
]

const READY_THRESHOLD: NumberSpec = {
  id: 'ready_threshold',
  label: 'Share of sections to clear',
  min: 1,
  max: 100,
  unit: '%',
  get: (rules) => rules.ready_threshold,
  set: (rules, value) => ({ ...rules, ready_threshold: value }),
}

const LIVE_NUMBERS: NumberSpec[] = [
  live('questions', 'Questions', 3, 50),
  live('timer_sec', 'Time per question', 5, 120, { unit: 'seconds', hint: 'Picture questions get 10 seconds more.' }),
  live('music_rounds', 'Music breaks', 0, 5, { hint: 'Only when music is switched on above.' }),
  live('head_start', 'Head-start points', 0, 5000, {
    step: 50,
    hint: 'Given to people who are already “ready” when they join, as a reward for doing the pre-read.',
  }),
]

const PICTURE_MODES: { value: PictureMode; label: string; description: string }[] = [
  {
    value: 'auto',
    label: 'Auto (recommended)',
    description:
      'Questions about reading a detail off the picture (a label, a number) are asked from memory: the picture is blurred, then revealed with the answer. Questions about interpreting it (a trend, a comparison) show the picture while players answer.',
  },
  { value: 'show', label: 'Always show the picture', description: 'Players can look at the picture while they answer.' },
  { value: 'memory', label: 'Always from memory', description: 'The picture is blurred until the answer is revealed.' },
]

// ---- helpers --------------------------------------------------------------------------

const SOUNDTRACK_VOLUMES: { value: SoundtrackVolume; label: string }[] = [
  { value: 'quiet', label: 'Quiet' },
  { value: 'medium', label: 'Medium' },
  { value: 'loud', label: 'Loud' },
]

const clone = (rules: GameRules): GameRules => structuredClone(rules)

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

/** What differs between two settings trees, nested the same way. Lists are compared and sent whole. */
function difference(before: unknown, after: unknown): unknown {
  if (isRecord(before) && isRecord(after)) {
    const changed: Record<string, unknown> = {}
    for (const key of Object.keys(after)) {
      const inner = difference(before[key], after[key])
      if (inner !== undefined) changed[key] = inner
    }
    return Object.keys(changed).length > 0 ? changed : undefined
  }
  if (Array.isArray(before) || Array.isArray(after)) return JSON.stringify(before) === JSON.stringify(after) ? undefined : after
  return Object.is(before, after) ? undefined : after
}

/** The saved numbers of one style, put back into a draft (used when a style is switched off). */
function withSavedStyle(draft: GameRules, saved: GameRules, mode: ModeId): GameRules {
  switch (mode) {
    case 'show':
      return { ...draft, show: saved.show }
    case 'sudden_death':
      return { ...draft, sudden_death: saved.sudden_death }
    case 'speed':
      return { ...draft, speed: saved.speed }
    case 'straight':
      return { ...draft, straight: saved.straight }
  }
}

// ---- the tab --------------------------------------------------------------------------

export default function PlayStylesTab() {
  const { detail, meta, save } = useQuiz()
  const notify = useNotify()
  const { rules, quiz } = detail
  const [draft, setDraft] = useState<GameRules>(() => clone(rules))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const defaultName = useId()

  const change = (update: (current: GameRules) => GameRules) => {
    setDraft(update)
    setError(null)
  }

  const enabled = new Set<ModeId>(draft.modes)
  const settings = difference(rules, draft) as DeepPartial<GameRules> | undefined
  const dirty = settings !== undefined

  // Every number on screen must be valid before anything is saved. Styles that are switched
  // off are not on screen, and are reset to their saved numbers, so they are not checked.
  const onScreen = [
    ...meta.modes.filter((mode) => enabled.has(mode.id)).flatMap((mode) => MODE_NUMBERS[mode.id]),
    ...(enabled.has('show') ? LIFELINES : []),
    READY_THRESHOLD,
    ...LIVE_NUMBERS,
  ]
  const badNumbers = onScreen.filter((spec) => numberError(spec.get(draft), spec.min, spec.max) !== null)
  const clockTooShort = enabled.has('sudden_death') && draft.sudden_death.timer_min_sec > draft.sudden_death.timer_start_sec
  const noDecades = draft.music_enabled && draft.decades.length === 0

  const toggleMode = (mode: ModeId, on: boolean) =>
    change((current) => {
      const next = new Set<ModeId>(current.modes)
      if (on) next.add(mode)
      else next.delete(mode)
      if (next.size === 0) return current // at least one style must stay on
      const modes = meta.modes.map((known) => known.id).filter((id) => next.has(id))
      const updated: GameRules = { ...current, modes, default_mode: next.has(current.default_mode) ? current.default_mode : modes[0] }
      return on ? updated : withSavedStyle(updated, rules, mode)
    })

  const toggleDecade = (decade: string, on: boolean) =>
    change((current) => {
      const next = new Set(current.decades)
      if (on) next.add(decade)
      else next.delete(decade)
      return { ...current, decades: meta.decades.filter((known) => next.has(known)) }
    })

  const numberField = (spec: NumberSpec) => (
    <NumberField
      key={spec.id}
      label={spec.label}
      value={spec.get(draft)}
      onChange={(value) => change((current) => spec.set(current, value))}
      min={spec.min}
      max={spec.max}
      step={spec.step}
      unit={spec.unit}
      hint={spec.hint}
    />
  )

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || !settings) return
    if (badNumbers.length > 0) {
      setError(`Fix the number marked in red first: ${badNumbers[0].label}${badNumbers.length > 1 ? ` (and ${badNumbers.length - 1} more)` : ''}.`)
      return
    }
    if (clockTooShort) {
      setError('Sudden Death: the shortest time per question cannot be longer than the time at the start.')
      return
    }
    if (noDecades) {
      setError('Music is on, so choose at least one decade.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      // Only what changed is sent; the server merges it into this quiz's settings and
      // answers with the rules that result, which become the new starting point.
      const saved = await save({ settings })
      setDraft(clone(saved.rules))
      notify('Play styles saved.')
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    // A flex column, not a grid, so the save bar (position: sticky) can follow the screen.
    <form onSubmit={submit} className="flex flex-col gap-5">
      <Panel
        title="Styles of play"
        description="Switch on the styles players can choose from. Each one asks the same questions in a different way."
      >
        <ul className="grid gap-3">
          {meta.modes.map((mode) => {
            const on = enabled.has(mode.id)
            const only = on && enabled.size === 1
            return (
              <li key={mode.id} className="rounded-xl border border-line p-3 sm:p-4">
                <Switch
                  checked={on}
                  onChange={(checked) => toggleMode(mode.id, checked)}
                  label={mode.name}
                  description={mode.tagline}
                  disabled={only}
                />
                {only && <p className="text-xs text-muted">This is the only style switched on, so it cannot be switched off.</p>}
                {on && (
                  <div className="mt-2 border-t border-line pt-3">
                    {enabled.size > 1 && (
                      <label className="ad-check text-sm">
                        <input
                          type="radio"
                          name={defaultName}
                          checked={draft.default_mode === mode.id}
                          onChange={() => change((current) => ({ ...current, default_mode: mode.id }))}
                        />
                        <span>
                          Offer this style first <span className="text-muted">(the one a player gets unless they pick another)</span>
                        </span>
                      </label>
                    )}
                    <div className="mt-1 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">{MODE_NUMBERS[mode.id].map(numberField)}</div>
                    {mode.id === 'show' && (
                      <>
                        <div className="mt-4">
                          <Switch
                            checked={draft.show.final_wager}
                            onChange={(checked) => change((current) => ({ ...current, show: { ...current.show, final_wager: checked } }))}
                            label="Final wager"
                            description="Players bet some of their points on the last question. When off, the last question is simply worth double."
                          />
                        </div>
                        <h3 className="mt-3 text-sm font-bold">Lifelines</h3>
                        <p className="text-xs text-muted">How many times a player may use each one in a game. Using one lowers the points for that question. 0 switches it off.</p>
                        <div className="mt-2 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">{LIFELINES.map(numberField)}</div>
                      </>
                    )}
                    {mode.id === 'sudden_death' && clockTooShort && (
                      <p className="mt-2 text-xs font-semibold text-bad">The shortest time cannot be longer than the time at the start.</p>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
        <p className="mt-3 text-xs text-muted">
          Players see the same one-line descriptions, quoting this quiz's own numbers: change the strikes or the clock below and their wording
          follows.
        </p>
      </Panel>

      <Panel title="Music" description="Name-that-tune breaks between questions, in Game Show, Sudden Death and live games.">
        <Switch
          checked={draft.music_enabled}
          onChange={(checked) => change((current) => ({ ...current, music_enabled: checked }))}
          label="Music breaks"
          description="A 30-second clip plays and players name the artist or the song."
        />
        {draft.music_enabled && (
          <Switch
            checked={draft.soundtrack ?? true}
            onChange={(checked) => change((current) => ({ ...current, soundtrack: checked }))}
            label="Music behind the questions"
            description="Hits from the game's decade keep playing through the whole game: low while a question is being read, up in the gaps, down under the host, and silent for a name-that-tune clip. Players can switch it off for themselves."
          />
        )}
        {draft.music_enabled && (draft.soundtrack ?? true) && (
          <div className="mb-3 ml-1">
            <span className="label">How loud it plays behind the questions</span>
            <Segmented
              label="How loud the music plays behind the questions"
              options={SOUNDTRACK_VOLUMES}
              value={draft.soundtrack_volume ?? 'medium'}
              onChange={(volume) => change((current) => ({ ...current, soundtrack_volume: volume }))}
            />
          </div>
        )}
        {draft.music_enabled && (
          <Switch
            checked={draft.after_party}
            onChange={(checked) => change((current) => ({ ...current, after_party: checked }))}
            label="Music on the results screen"
            description="When a game ends, hits from its decade play behind the results, one fading into the next, with the song's name on screen. Players can pause or skip it."
          />
        )}
        {draft.music_enabled && (
          <fieldset className="mt-2">
            <legend className="label">Decades players can choose from</legend>
            <div className="flex flex-wrap gap-x-5">
              {meta.decades.map((decade) => (
                <label key={decade} className="ad-check text-sm">
                  <input type="checkbox" checked={draft.decades.includes(decade)} onChange={(event) => toggleDecade(decade, event.target.checked)} />
                  <span>{decadeLabel(decade)}</span>
                </label>
              ))}
            </div>
            {noDecades && <p className="text-xs font-semibold text-bad">Choose at least one decade.</p>}
            <p className="mt-1 text-xs text-muted">
              <Link className="ad-link" to={paths.music}>
                See how many tracks each decade has
              </Link>
            </p>
          </fieldset>
        )}
      </Panel>

      <Panel title="Pictures" description="How questions about the report's charts and pictures are asked.">
        <ChoiceGroup
          label="Picture mode"
          options={PICTURE_MODES}
          value={draft.picture_mode}
          onChange={(mode) => change((current) => ({ ...current, picture_mode: mode }))}
        />
      </Panel>

      <Panel title="Readiness and leaderboard">
        <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
          <div>
            {numberField(READY_THRESHOLD)}
            <p className="mt-1 text-xs text-muted">
              Someone counts as <Badge tone="good">Ready</Badge> once they have cleared this share of the report's sections. A section is cleared by
              answering one of its questions correctly.
            </p>
          </div>
          <Switch
            checked={draft.leaderboard}
            onChange={(checked) => change((current) => ({ ...current, leaderboard: checked }))}
            label="Show the leaderboard to players"
            description="When off, players only see their own results. You still see everything on the Results tab."
          />
        </div>
      </Panel>

      <Panel title="Live games" description="For games run on a big screen in the meeting, started from the Live tab.">
        <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">{LIVE_NUMBERS.map(numberField)}</div>
      </Panel>

      {quiz.status === 'open' && dirty && (
        <Note tone="info">This quiz is open. Changes take effect as soon as you save, and can affect games that are already under way.</Note>
      )}

      <SaveBar dirty={dirty} saving={saving} error={error} onDiscard={() => change(() => clone(rules))} />
    </form>
  )
}
