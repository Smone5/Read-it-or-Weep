// Small building blocks shared by the admin screens.
//
// A note on styling: the shared classes (.btn .card .field .chip .label) and the .ad-*
// classes in admin.css are not in a cascade layer, so a Tailwind utility cannot override a
// property they set (display on .btn, width on .field, background on .card, and so on).
// Put such utilities on a wrapper element instead.

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ErrorNote, Modal, Spinner } from '../components/ui'
import { useChrome } from './context'
import type { Tone } from './format'

// ---- page furniture -----------------------------------------------------------------

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = '',
}: {
  title?: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card p-4 sm:p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-base font-bold">{title}</h2>}
            {description && <p className="mt-0.5 max-w-2xl text-sm text-muted">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className="chip" data-tone={tone} title={title}>
      {children}
    </span>
  )
}

export function Note({ tone = 'neutral', role, children }: { tone?: Tone; role?: 'status' | 'alert'; children: ReactNode }) {
  return (
    <div className="ad-note" data-tone={tone} role={role}>
      {children}
    </div>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  )
}

/** A labelled progress bar. `tone` is any CSS colour; leave it out for the admin's blue. */
export function Bar({
  value,
  max = 100,
  tone,
  label,
  indeterminate = false,
}: {
  value: number
  max?: number
  tone?: string
  label: string
  indeterminate?: boolean
}) {
  const percent = max > 0 ? Math.max(0, Math.min(100, Math.round((value / max) * 100))) : 0
  if (indeterminate) {
    return (
      <div className="ad-bar" role="progressbar" aria-label={label}>
        <span className="animate-pulse" style={{ width: '100%', background: tone, opacity: 0.55 }} />
      </div>
    )
  }
  return (
    <div className="ad-bar" role="progressbar" aria-label={label} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${percent}%`, background: tone }} />
    </div>
  )
}

// ---- loading, empty and error states -------------------------------------------------

export function LoadingBlock({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="grid place-items-center py-14">
      <Spinner label={label} />
    </div>
  )
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="grid justify-items-start gap-3 py-2">
      <ErrorNote>{message}</ErrorNote>
      {onRetry && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-10 text-center">
      <h3 className="text-base font-bold">{title}</h3>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

// ---- dialogs ---------------------------------------------------------------------------

/**
 * The shared Modal, drawn at the top of the admin rather than where it is declared, with a
 * title bar, and with keyboard focus moved in on opening and handed back on closing.
 *
 * Because it is drawn elsewhere, React still bubbles its events to the component that
 * declared it: never declare a Dialog inside a <form> or a clickable row.
 */
export function Dialog({
  open,
  onClose,
  title,
  wide,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  wide?: boolean
  children: ReactNode
}) {
  const { portal } = useChrome()
  const body = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || !portal) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const find = (selector: string) => body.current?.querySelector<HTMLElement>(selector) ?? null
    // The element a dialog marks, else its first field, else its close button.
    const target = find('[data-autofocus]') ?? find('input:not([type="hidden"]), textarea, select') ?? find('button')
    target?.focus()
    return () => opener?.focus()
  }, [open, portal])

  if (!open || !portal) return null
  return createPortal(
    <Modal open onClose={onClose} wide={wide}>
      <div ref={body}>
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-bold">{title}</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <span aria-hidden>✕</span>
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </Modal>,
    portal,
  )
}

/** The row of buttons at the bottom of a dialog. */
export function DialogActions({ children }: { children: ReactNode }) {
  return <div className="mt-5 flex flex-wrap items-center justify-end gap-2">{children}</div>
}

/** "Are you sure?" for anything that cannot be undone. `typeToConfirm` makes the person type a word first. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busyLabel = 'Working…',
  danger = true,
  typeToConfirm,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  children?: ReactNode
  confirmLabel: string
  busyLabel?: string
  danger?: boolean
  typeToConfirm?: string
  busy?: boolean
  error?: string | null
  onConfirm: () => void
  onCancel: () => void
}) {
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (open) setTyped('')
  }, [open])

  const matches = !typeToConfirm || typed.trim().toLowerCase() === typeToConfirm.trim().toLowerCase()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (matches && !busy) onConfirm()
  }

  return (
    <Dialog open={open} onClose={busy ? () => {} : onCancel} title={title}>
      <form onSubmit={submit}>
        {children && <div className="grid gap-3 text-sm">{children}</div>}
        {typeToConfirm && (
          <Field label={`Type “${typeToConfirm}” to confirm`} className="mt-4">
            {(props) => (
              <input className="field" {...props} value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" data-autofocus />
            )}
          </Field>
        )}
        {error && (
          <div className="mt-4">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy} data-autofocus={typeToConfirm ? undefined : true}>
            Cancel
          </button>
          <button type="submit" className={`btn ${danger ? 'btn-danger-solid' : 'btn-primary'}`} disabled={!matches || busy}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}

// ---- form controls ---------------------------------------------------------------------

type ControlProps = { id: string; 'aria-describedby'?: string; 'aria-invalid'?: true }

/** A label, the control it belongs to, and an optional hint and error, all wired together for screen readers. */
export function Field({
  label,
  hint,
  error,
  className,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  className?: string
  children: (props: ControlProps) => ReactNode
}) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
  return (
    <div className={className}>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': error ? true : undefined })}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="mt-1 text-xs font-semibold text-bad">
          {error}
        </p>
      )}
    </div>
  )
}

export function numberError(value: number, min: number, max: number): string | null {
  if (!Number.isInteger(value)) return `Enter a whole number from ${min} to ${max}.`
  if (value < min || value > max) return `Must be from ${min} to ${max}.`
  return null
}

/** A whole-number input. While the box is empty or half-typed the value is NaN, which `numberError` reports. */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  hint,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number
  unit?: string
  hint?: ReactNode
}) {
  return (
    <Field label={label} hint={hint} error={numberError(value, min, max)}>
      {(props) => (
        <div className="flex items-center gap-2">
          <div className="w-24 flex-none">
            <input
              className="field"
              type="number"
              inputMode="numeric"
              min={min}
              max={max}
              step={step}
              {...props}
              value={Number.isFinite(value) ? value : ''}
              onChange={(event) => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))}
            />
          </div>
          {unit && <span className="text-sm text-muted">{unit}</span>}
        </div>
      )}
    </Field>
  )
}

/** An on/off switch. The whole row is the button, so it is easy to hit on a phone. */
export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  description?: ReactNode
  disabled?: boolean
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} className="ad-switch" onClick={() => onChange(!checked)} disabled={disabled}>
      <span className="ad-switch-track" aria-hidden />
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{label}</span>
        {description && <span className="block text-sm text-muted">{description}</span>}
      </span>
    </button>
  )
}

/** Pick one of a few options, shown side by side. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled,
}: {
  label: string
  options: readonly { value: T; label: ReactNode }[]
  value: T
  onChange: (value: T) => void
  disabled?: boolean
}) {
  return (
    <div role="radiogroup" aria-label={label} className="ad-seg">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className="ad-seg-item"
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** Pick one of a few options, each with a line explaining what it means. */
export function ChoiceGroup<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled,
  className = 'grid gap-2',
}: {
  label: string
  options: readonly { value: T; label: ReactNode; description?: ReactNode }[]
  value: T
  onChange: (value: T) => void
  disabled?: boolean
  className?: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className={className}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className="ad-choice"
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          <span className="ad-choice-dot" aria-hidden />
          <span className="min-w-0">
            <span className="block text-sm font-semibold">{option.label}</span>
            {option.description && <span className="block text-sm text-muted">{option.description}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the older way */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const copied = document.execCommand('copy')
    area.remove()
    return copied
  } catch {
    return false
  }
}

export function CopyButton({ text, label = 'Copy', className = 'btn btn-ghost' }: { text: string; label?: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = async () => {
    const copied = await copyText(text)
    setState(copied ? 'copied' : 'failed')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setState('idle'), 2400)
  }

  return (
    <button type="button" className={className} onClick={copy}>
      <span aria-live="polite">{state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy' : label}</span>
    </button>
  )
}

/** Sits at the bottom of a form while it has unsaved changes. Place it inside the <form>; Save submits it. */
export function SaveBar({
  dirty,
  saving,
  error,
  onDiscard,
  saveLabel = 'Save changes',
}: {
  dirty: boolean
  saving: boolean
  error?: string | null
  onDiscard: () => void
  saveLabel?: string
}) {
  if (!dirty && !error) return null
  return (
    <div className="ad-sticky-bar">
      <div className="mr-auto min-w-0 text-sm font-semibold" role={error ? 'alert' : undefined}>
        {error ? <span className="text-bad">{error}</span> : 'You have unsaved changes.'}
      </div>
      <button type="button" className="btn btn-ghost" onClick={onDiscard} disabled={saving || !dirty}>
        Discard
      </button>
      <button type="submit" className="btn btn-primary" disabled={saving || !dirty}>
        {saving ? 'Saving…' : saveLabel}
      </button>
    </div>
  )
}
