import { useEffect, useState, type ReactNode } from 'react'
import { onSoundChange, setSoundOn, soundOn } from '../lib/sound'
import type { Branding } from '../lib/types'

export function Logo({ branding, size = 40 }: { branding: Branding | null; size?: number }) {
  if (branding?.logoUrl) {
    return <img src={branding.logoUrl} alt={branding.companyName} style={{ height: size, maxWidth: size * 4 }} className="object-contain" />
  }
  return (
    <span
      aria-hidden
      className="display grid place-items-center rounded-xl font-extrabold text-white"
      style={{ width: size, height: size, background: 'var(--brand)', fontSize: size * 0.5 }}
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
    <p role="alert" className="rounded-xl border px-3.5 py-2.5 text-sm font-semibold" style={{ borderColor: 'var(--bad)', background: 'color-mix(in oklab, var(--bad) 16%, transparent)' }}>
      {children}
    </p>
  )
}

export function Modal({ open, onClose, children, wide }: { open: boolean; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 grid place-items-end sm:place-items-center" role="dialog" aria-modal="true">
      <button aria-label="Close" className="absolute inset-0 cursor-default bg-black/60" onClick={onClose} />
      <div className={`card rise relative max-h-[92dvh] w-full overflow-y-auto rounded-b-none p-5 sm:rounded-b-[18px] ${wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'}`}>
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
    <button className="btn btn-ghost btn-sm" aria-pressed={on} aria-label={on ? 'Turn sound off' : 'Turn sound on'} onClick={() => setOn(!on)} title={on ? 'Sound on' : 'Sound off'}>
      <span aria-hidden>{on ? '🔊' : '🔇'}</span>
    </button>
  )
}

export function Meter({ value, max, tone = 'var(--brand)' }: { value: number; max: number; tone?: string }) {
  const percent = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full" style={{ background: 'color-mix(in oklab, var(--ink) 12%, transparent)' }} role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full transition-[width] duration-700" style={{ width: `${percent}%`, background: tone }} />
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
