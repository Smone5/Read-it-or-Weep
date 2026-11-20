import { useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { ErrorNote } from '../components/ui'
import { call, errorText } from './api'
import { ConfirmDialog, ErrorBlock, Field, LoadingBlock, Note, PageHeader, Panel, SaveBar, Segmented } from './components'
import { useChrome, useNotify, usePageTitle } from './context'
import { useLoad } from './hooks'
import type { BrandingSettings, SettingsRead, SettingsWritten } from './types'

export default function BrandingPage() {
  usePageTitle('Branding')
  const { data, error, loading, reload } = useLoad<SettingsRead<BrandingSettings>>('/settings/branding')
  return (
    <>
      <PageHeader
        title="Branding"
        description="Make the game look like yours: your name, your colours, your logo. This changes what players see, not these admin screens."
      />
      {!data && loading && <LoadingBlock label="Loading your branding" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}
      {data && <BrandingForm initial={data.value} defaults={data.defaults} />}
    </>
  )
}

// ---- the form -------------------------------------------------------------------------

type Form = {
  company_name: string
  game_title: string
  tagline: string
  primary_color: string
  accent_color: string
  theme: BrandingSettings['theme']
  support_email: string
}

const formOf = (value: BrandingSettings): Form => ({
  company_name: value.company_name,
  game_title: value.game_title,
  tagline: value.tagline,
  primary_color: value.primary_color,
  accent_color: value.accent_color,
  theme: value.theme,
  support_email: value.support_email ?? '',
})

const HEX = /^#[0-9a-fA-F]{6}$/
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const LOGO_TYPES = ['.png', '.jpg', '.jpeg', '.svg']
const LOGO_MAX_BYTES = 2 * 1024 * 1024 // the server's limit (MAX_LOGO_BYTES in admin.py)

const THEMES = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
] as const

/**
 * Only the fields that differ from what is saved. An empty support email is sent as an
 * empty text, because the server ignores an empty (null) value rather than clearing it.
 */
function changesFrom(saved: BrandingSettings, form: Form): Partial<Form> {
  const before = formOf(saved)
  const changes: Partial<Form> = {}
  if (form.company_name.trim() !== before.company_name) changes.company_name = form.company_name.trim()
  if (form.game_title.trim() !== before.game_title) changes.game_title = form.game_title.trim()
  if (form.tagline.trim() !== before.tagline) changes.tagline = form.tagline.trim()
  if (form.primary_color.toLowerCase() !== before.primary_color.toLowerCase()) changes.primary_color = form.primary_color
  if (form.accent_color.toLowerCase() !== before.accent_color.toLowerCase()) changes.accent_color = form.accent_color
  if (form.theme !== before.theme) changes.theme = form.theme
  if (form.support_email.trim() !== before.support_email) changes.support_email = form.support_email.trim()
  return changes
}

function problemWith(form: Form): string | null {
  if (!form.company_name.trim()) return "Enter your company's name."
  if (!form.game_title.trim()) return 'Give the game a title.'
  if (!HEX.test(form.primary_color) || !HEX.test(form.accent_color)) return 'Colours must be six-digit codes such as #6D5EF8.'
  if (form.support_email.trim() && !EMAIL.test(form.support_email.trim())) return 'That support email does not look right.'
  return null
}

function BrandingForm({ initial, defaults }: { initial: BrandingSettings; defaults: BrandingSettings }) {
  const notify = useNotify()
  const { setProductName } = useChrome()
  const [saved, setSaved] = useState(initial)
  const [form, setForm] = useState<Form>(() => formOf(initial))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [logoBusy, setLogoBusy] = useState(false)
  const [logoError, setLogoError] = useState<string | null>(null)
  const [removingLogo, setRemovingLogo] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const set = (patch: Partial<Form>) => {
    setForm((current) => ({ ...current, ...patch }))
    setError(null)
  }

  const changes = changesFrom(saved, form)
  const dirty = Object.keys(changes).length > 0
  // The logo is served from one fixed address; the key on the end makes the browser fetch a replaced logo afresh.
  const logoUrl = saved.logo_key ? `/api/media/logo?v=${encodeURIComponent(saved.logo_key)}` : null
  const standardColours =
    form.primary_color.toLowerCase() === defaults.primary_color.toLowerCase() &&
    form.accent_color.toLowerCase() === defaults.accent_color.toLowerCase() &&
    form.theme === defaults.theme

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (saving || !dirty) return
    const problem = problemWith(form)
    if (problem) {
      setError(problem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await call<SettingsWritten<BrandingSettings>>('/settings/branding', { method: 'PUT', body: changes })
      setSaved(result.value)
      setForm(formOf(result.value))
      setProductName(result.value.game_title)
      notify('Branding saved. Players see it the next time they open the game.')
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  const uploadLogo = async (files: FileList | null) => {
    const file = files?.[0]
    if (!file || logoBusy) return
    const dot = file.name.lastIndexOf('.')
    const extension = dot < 0 ? '' : file.name.slice(dot).toLowerCase()
    if (!LOGO_TYPES.includes(extension)) {
      setLogoError('Upload the logo as a PNG, JPG or SVG file.')
      return
    }
    if (file.size > LOGO_MAX_BYTES) {
      setLogoError('That logo is larger than 2 MB.')
      return
    }
    setLogoBusy(true)
    setLogoError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await call<SettingsWritten<BrandingSettings>>('/settings/branding/logo', { method: 'POST', body })
      setSaved(result.value) // the logo is saved straight away; anything typed in the form stays as it is
      notify('Logo uploaded.')
    } catch (reason) {
      setLogoError(errorText(reason))
    } finally {
      setLogoBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const removeLogo = async () => {
    setLogoBusy(true)
    setLogoError(null)
    try {
      const result = await call<SettingsWritten<BrandingSettings>>('/settings/branding/logo', { method: 'DELETE' })
      setSaved(result.value)
      setRemovingLogo(false)
      notify('Logo removed.')
    } catch (reason) {
      setLogoError(errorText(reason))
    } finally {
      setLogoBusy(false)
    }
  }

  return (
    <>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* A flex column, not a grid, so the save bar (position: sticky) can follow the screen. */}
        <form onSubmit={submit} className="flex flex-col gap-5">
          <Panel title="Names and wording">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Company name">
                {(props) => (
                  <input className="field" {...props} value={form.company_name} onChange={(event) => set({ company_name: event.target.value })} maxLength={80} required />
                )}
              </Field>
              <Field label="Game title" hint="The name of the game, shown on its start screen and on the browser tab.">
                {(props) => (
                  <input className="field" {...props} value={form.game_title} onChange={(event) => set({ game_title: event.target.value })} maxLength={60} required />
                )}
              </Field>
              <Field label="Tagline" hint="One line under the title. Leave it empty to use the standard one." className="md:col-span-2">
                {(props) => <input className="field" {...props} value={form.tagline} onChange={(event) => set({ tagline: event.target.value })} maxLength={140} />}
              </Field>
              <Field label="Support email (optional)" hint="Shown to players who get stuck, so they know who to ask.">
                {(props) => (
                  <input
                    className="field"
                    type="email"
                    {...props}
                    value={form.support_email}
                    onChange={(event) => set({ support_email: event.target.value })}
                    maxLength={200}
                    autoComplete="off"
                  />
                )}
              </Field>
            </div>
          </Panel>

          <Panel
            title="Colours and theme"
            actions={
              !standardColours && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => set({ primary_color: defaults.primary_color, accent_color: defaults.accent_color, theme: defaults.theme })}
                >
                  Use the standard colours
                </button>
              )
            }
          >
            <div className="grid gap-4 md:grid-cols-2">
              <ColourField
                label="Main colour"
                hint="Buttons, titles and the glow around panels."
                value={form.primary_color}
                fallback={saved.primary_color}
                onChange={(value) => set({ primary_color: value })}
              />
              <ColourField
                label="Accent colour"
                hint="Highlights: the timer, tags and the host's speech box."
                value={form.accent_color}
                fallback={saved.accent_color}
                onChange={(value) => set({ accent_color: value })}
              />
              <div className="md:col-span-2">
                <span className="label">Theme</span>
                <Segmented label="Theme" options={THEMES} value={form.theme} onChange={(theme) => set({ theme })} />
                <p className="mt-1 text-xs text-muted">Dark is the classic arcade look. Light suits a bright room or a projector.</p>
              </div>
            </div>
            <ContrastNote form={form} />
          </Panel>

          <Panel title="Logo" description="Shown on the game's start screen. PNG, JPG or SVG, up to 2 MB. A logo with a transparent background looks best.">
            <div className="flex flex-wrap items-center gap-4">
              <div
                className="grid h-20 w-44 flex-none place-items-center rounded-lg border border-line p-2"
                style={{ background: form.theme === 'dark' ? '#0d0b1f' : '#f4f2fb' }}
              >
                {logoUrl ? (
                  <img src={logoUrl} alt="Your current logo" className="max-h-full max-w-full object-contain" />
                ) : (
                  <span className="text-xs" style={{ color: form.theme === 'dark' ? '#a9a4cc' : '#5d5880' }}>
                    No logo yet
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn btn-ghost" onClick={() => fileInput.current?.click()} disabled={logoBusy}>
                  {logoBusy && !removingLogo ? 'Uploading…' : logoUrl ? 'Replace logo' : 'Upload a logo'}
                </button>
                {logoUrl && (
                  <button
                    type="button"
                    className="btn btn-danger"
                    onClick={() => {
                      setLogoError(null)
                      setRemovingLogo(true)
                    }}
                    disabled={logoBusy}
                  >
                    Remove
                  </button>
                )}
                <input
                  ref={fileInput}
                  type="file"
                  hidden
                  accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
                  aria-label="Logo file"
                  onChange={(event) => void uploadLogo(event.target.files)}
                />
              </div>
            </div>
            <p className="mt-2 text-xs text-muted">The logo is saved as soon as you upload it; it does not wait for “Save changes”.</p>
            {logoError && !removingLogo && (
              <div className="mt-3">
                <ErrorNote>{logoError}</ErrorNote>
              </div>
            )}
          </Panel>

          <SaveBar dirty={dirty} saving={saving} error={error} onDiscard={() => set(formOf(saved))} />
        </form>

        <aside className="lg:sticky lg:top-20" aria-label="Preview">
          <Panel title="Preview" description="How the game's start screen will look. It updates as you type.">
            <GamePreview form={form} fallback={saved} logoUrl={logoUrl} />
            <p className="mt-2 text-xs text-muted">{dirty ? 'Not saved yet: players still see the old look.' : 'This is what players see now.'}</p>
          </Panel>
        </aside>
      </div>

      {/* Declared outside the <form> so confirming it cannot submit the form. */}
      <ConfirmDialog
        open={removingLogo}
        title="Remove the logo?"
        confirmLabel="Remove logo"
        busyLabel="Removing…"
        busy={logoBusy}
        error={logoError}
        onConfirm={removeLogo}
        onCancel={() => {
          setRemovingLogo(false)
          setLogoError(null)
        }}
      >
        <p>The game will show the first letter of your company name instead. You can upload a logo again at any time.</p>
      </ConfirmDialog>
    </>
  )
}

function ColourField({
  label,
  hint,
  value,
  fallback,
  onChange,
}: {
  label: string
  hint: string
  value: string
  /** What the colour picker shows while the typed code is not (yet) a complete colour. */
  fallback: string
  onChange: (value: string) => void
}) {
  const valid = HEX.test(value)
  return (
    <Field label={label} hint={hint} error={valid ? null : 'Use a six-digit code such as #6D5EF8.'}>
      {(props) => (
        <div className="flex items-center gap-2">
          <input
            type="color"
            className="h-10 w-12 flex-none cursor-pointer rounded-lg border border-line bg-surface p-1"
            value={(valid ? value : fallback).toLowerCase()}
            onChange={(event) => onChange(event.target.value.toUpperCase())}
            aria-label={`${label}: choose from a palette`}
          />
          <div className="w-32 flex-none">
            <input
              className="field font-mono uppercase"
              {...props}
              value={value}
              onChange={(event) => {
                const typed = event.target.value.trim()
                onChange(typed && !typed.startsWith('#') ? `#${typed}` : typed)
              }}
              maxLength={7}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        </div>
      )}
    </Field>
  )
}

// ---- preview --------------------------------------------------------------------------

// The game's own palettes for its two themes (styles.css) and its rule for text on a
// brand-coloured button (lib/theme.ts), repeated here so the preview matches the real thing.
const PALETTE = {
  dark: { page: '#0d0b1f', surface: '#191634', surface2: '#231f47', ink: '#f6f4ff', muted: '#a9a4cc', line: 'rgb(255 255 255 / 0.11)' },
  light: { page: '#f4f2fb', surface: '#ffffff', surface2: '#ece9f8', ink: '#17132f', muted: '#5d5880', line: 'rgb(20 16 60 / 0.12)' },
}

function luminance(hex: string): number {
  const value = hex.replace('#', '')
  const channel = (index: number) => {
    const c = parseInt(value.slice(index, index + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
}

function contrast(first: string, second: string): number {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** A gentle warning when the main colour would all but disappear against the theme's background. */
function ContrastNote({ form }: { form: Form }) {
  if (!HEX.test(form.primary_color)) return null
  if (contrast(form.primary_color, PALETTE[form.theme].page) >= 1.7) return null
  return (
    <div className="mt-4">
      <Note tone="warn" role="status">
        The main colour is very close to the {form.theme} theme's background, so buttons and titles will be hard to see. Try a{' '}
        {form.theme === 'dark' ? 'brighter' : 'deeper'} colour, or the other theme.
      </Note>
    </div>
  )
}

/**
 * A mock of the game's start screen. The `arcade` class gives it the game's real styles, and
 * the colours are set on this element only, so nothing outside the box changes.
 */
function GamePreview({ form, fallback, logoUrl }: { form: Form; fallback: BrandingSettings; logoUrl: string | null }) {
  const primary = HEX.test(form.primary_color) ? form.primary_color : fallback.primary_color
  const accent = HEX.test(form.accent_color) ? form.accent_color : fallback.accent_color
  const palette = PALETTE[form.theme]
  const company = form.company_name.trim() || fallback.company_name
  const style = {
    '--brand': primary,
    '--accent': accent,
    '--brand-ink': luminance(primary) > 0.55 ? '#14112b' : '#ffffff',
    '--page': palette.page,
    '--surface': palette.surface,
    '--surface-2': palette.surface2,
    '--ink': palette.ink,
    '--muted': palette.muted,
    '--line': palette.line,
    '--vibe-1': primary,
    '--vibe-2': accent,
    background: 'var(--page)',
    color: 'var(--ink)',
    colorScheme: form.theme,
  } as CSSProperties

  return (
    <div className="arcade brand-preview" data-theme={form.theme} style={style} aria-hidden>
      <div className="card mx-auto max-w-xs p-5 text-center">
        <div className="flex justify-center">
          {logoUrl ? (
            <img src={logoUrl} alt="" className="object-contain" style={{ height: 44, maxWidth: 176 }} />
          ) : (
            <span className="display grid place-items-center rounded-xl text-white" style={{ width: 44, height: 44, background: 'var(--brand)', fontSize: 22 }}>
              {company.charAt(0).toUpperCase()}
            </span>
          )}
        </div>
        <div className="mt-3">
          <span className="chip chip-accent">{company}</span>
        </div>
        <h2 className="neon mt-3 text-sm [overflow-wrap:anywhere]">{form.game_title.trim() || fallback.game_title}</h2>
        <p className="mt-2 text-sm [overflow-wrap:anywhere]" style={{ color: 'var(--muted)' }}>
          {form.tagline.trim() || fallback.tagline}
        </p>
        <div className="mt-4">
          <span className="btn btn-primary">Press Start</span>
        </div>
      </div>
    </div>
  )
}
