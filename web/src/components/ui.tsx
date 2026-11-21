import { useEffect, useState, type ReactNode } from 'react'
import { onSoundChange, setSoundOn, soundOn } from '../lib/sound'
import type { Branding } from '../lib/types'
import { IconClose, IconSoundOff, IconSoundOn } from './Icons'

export function Logo({ branding, size = 40 }: { branding: Branding | null; size?: number }) {
  if (branding?.logoUrl) {
    return <img src={branding.logoUrl} alt={branding.companyName} style={{ height: size, maxWidth: size * 4 }} className="object-contain" />
  }
  return (
    <span
      aria-hidden
      className="display grid place-items-center rounded-[5px] border-2 font-extrabold"
      style={{
        width: size,
        height: size,
        background: 'var(--brand)',
        color: 'var(--brand-ink)',
        borderColor: 'color-mix(in oklab, var(--brand) 55%, white)',
        boxShadow: '3px 3px 0 #000, 0 0 14px -2px var(--brand)',
        fontSize: Math.round(size * 0.44),
        lineHeight: 1,
      }}
    >
      {(branding?.companyName ?? 'Q').trim().charAt(0).toUpperCase()}
    </span>
  )
}

/** Full-page arcade backdrop for the game and live screens: neon horizon, grid floor, scanlines. */
export function Stage({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`arcade stage safe ${className}`}>
      <div className="grid-floor" aria-hidden />
      <div className="scanlines" aria-hidden />
      {children}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-3 text-muted">
      <span className="spin-slow inline-block h-5 w-5 rounded-full border-2 border-current border-t-transparent" style={{ animationDuration: '0.8s' }} />
      <span>{label}…</span>
    </div>
  )
}

export function FullScreenMessage({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <Stage className="grid place-items-center">
      <div className="card rise w-full max-w-md p-7 text-center">
        <h1 className="neon text-base sm:text-lg">{title}</h1>
        {children && <div className="mt-3 text-muted">{children}</div>}
        {action && <div className="mt-6">{action}</div>}
      </div>
    </Stage>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <p
      role="alert"
      className="flex items-start gap-2.5 rounded-[4px] border-2 px-3.5 py-2.5 text-sm font-semibold"
      style={{ borderColor: 'var(--bad)', background: 'color-mix(in oklab, var(--bad) 16%, transparent)' }}
    >
      <IconClose className="mt-0.5 text-base" style={{ color: 'var(--bad)' }} />
      <span className="min-w-0 flex-1">{children}</span>
    </p>
  )
}

export function Modal({ open, onClose, children, wide }: { open: boolean; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const previousOverflow = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.documentElement.style.overflow = previousOverflow
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 grid place-items-end sm:place-items-center" role="dialog" aria-modal="true">
      <button aria-label="Close" className="absolute inset-0 cursor-default bg-black/65" onClick={onClose} />
      <div
        className={`card rise relative max-h-[92dvh] w-full overflow-y-auto rounded-b-none p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] sm:rounded-b-[var(--radius)] sm:pb-5 ${wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'}`}
      >
        {children}
      </div>
    </div>
  )
}

export function useSoundSetting(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(soundOn())
  useEffect(() => onSoundChange(setOn), [])
  return [on, setSoundOn]
}

export function SoundToggle() {
  const [on, setOn] = useSoundSetting()
  return (
    <button
      className="btn btn-ghost btn-sm"
      aria-pressed={on}
      aria-label={on ? 'Turn sound off' : 'Turn sound on'}
      onClick={() => setOn(!on)}
      title={on ? 'Sound on' : 'Sound off'}
    >
      {on ? <IconSoundOn size={16} /> : <IconSoundOff size={16} className="opacity-60" />}
    </button>
  )
}

export function Meter({ value, max, tone = 'var(--brand)' }: { value: number; max: number; tone?: string }) {
  const fraction = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  const percent = Math.round(fraction * 100)
  return (
    <div
      className="h-2.5 w-full overflow-hidden rounded-[3px] border"
      style={{ background: 'color-mix(in oklab, var(--ink) 10%, transparent)', borderColor: 'var(--line)' }}
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full w-full origin-left transition-transform duration-700"
        style={{ transform: `scaleX(${fraction})`, background: tone }}
      />
    </div>
  )
}

/** "in 2 days", "tomorrow", "in 3 hours" – for the meeting countdown. */
export function untilText(iso: string | null): string | null {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms < 0) return 'has started'
  const hours = ms / 3_600_000
  if (hours < 1) return `in ${Math.max(1, Math.round(ms / 60_000))} min`
  if (hours < 24) return `in ${Math.round(hours)} hour${Math.round(hours) === 1 ? '' : 's'}`
  const days = Math.round(hours / 24)
  return days === 1 ? 'tomorrow' : `in ${days} days`
}
