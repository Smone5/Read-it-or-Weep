import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import {
  musicVolume,
  onJukeboxChange,
  onMusicVolumeChange,
  setMusicOn,
  setMusicVolume,
  type JukeboxState,
} from '../lib/jukebox'
import { onSoundChange, setSoundOn, soundOn, unlockAudio } from '../lib/sound'
import type { Branding } from '../lib/types'
import { IconClose, IconExternal, IconSoundOff, IconSoundOn } from './Icons'

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

export function Modal({
  open,
  onClose,
  children,
  wide,
  label = 'Dialog',
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
  wide?: boolean
  label?: string
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const pressedOutside = useRef(false)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open) {
      if (!dialog.open) dialog.showModal()
    } else if (dialog.open) {
      dialog.close()
    }
  }, [open])

  if (!open) return null

  const isOutsidePanel = (clientX: number, clientY: number) => {
    const panel = panelRef.current
    if (!panel) return false
    const r = panel.getBoundingClientRect()
    return clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom
  }

  return (
    <dialog
      ref={dialogRef}
      aria-label={label}
      {...({ closedby: 'any' } as Record<string, string>)}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClose={() => {
        if (open) onClose()
      }}
      onPointerDown={(event) => {
        pressedOutside.current = isOutsidePanel(event.clientX, event.clientY)
      }}
      onPointerUp={(event) => {
        // Light-dismiss fallback for browsers without native closedby="any" (e.g. Safari):
        // require both press and release outside the panel so drag-selections never dismiss.
        if (pressedOutside.current && isOutsidePanel(event.clientX, event.clientY)) {
          onClose()
        }
        pressedOutside.current = false
      }}
      className="modal-dialog fixed inset-0 z-50 m-0 h-dvh max-h-none w-screen max-w-none bg-transparent p-0 text-ink backdrop:bg-black/65"
    >
      <div className="grid h-full w-full place-items-end sm:place-items-center sm:p-4">
        <div
          ref={panelRef}
          className={`card rise relative max-h-[92dvh] w-full overflow-y-auto rounded-b-none p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] sm:rounded-b-[var(--radius)] sm:pb-5 ${wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'}`}
        >
          {children}
        </div>
      </div>
    </dialog>
  )
}

export function useSoundSetting(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(soundOn())
  useEffect(() => onSoundChange(setOn), [])
  return [on, setSoundOn]
}

export function SoundToggle({ showSong = true }: { showSong?: boolean }) {
  const [on, setOn] = useSoundSetting()
  const [level, setLevel] = useState(musicVolume)
  const [jukebox, setJukebox] = useState<JukeboxState>({ active: false, track: null, playing: false, needsTap: false })
  const [open, setOpen] = useState(false)
  const [openUp, setOpenUp] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)
  const sliderId = useId()

  useEffect(() => onMusicVolumeChange(setLevel), [])
  useEffect(() => onJukeboxChange(setJukebox), [])

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const musicActive = on && level > 0
  const words = !on ? 'muted' : level === 0 ? 'music off' : level < 35 ? 'quiet' : level <= 65 ? 'normal' : 'loud'
  const track = jukebox.track

  const toggleOpen = () => {
    if (!open && box.current) {
      const rect = box.current.getBoundingClientRect()
      const spaceBelow = window.innerHeight - rect.bottom
      setOpenUp(spaceBelow < 290 && rect.top > spaceBelow)
    }
    setOpen((prev) => !prev)
  }

  return (
    <div ref={box} className="relative z-[55] flex-none">
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Sound and volume: ${words}`}
        title={`Sound & volume (${words})`}
        onClick={toggleOpen}
      >
        {on ? <IconSoundOn size={16} /> : <IconSoundOff size={16} className="opacity-60" />}
      </button>
      {open && (
        <div
          className={`card rise absolute right-0 z-[60] max-h-[min(78dvh,22rem)] w-64 max-w-[calc(100vw-1.5rem)] overflow-y-auto p-3.5 text-left ${openUp ? 'bottom-full mb-2' : 'top-full mt-2'}`}
          role="dialog"
          aria-label="Sound and music volume"
        >
          {showSong && musicActive && jukebox.active && jukebox.playing && track && (
            <div className="mb-3.5 flex items-center gap-2.5 border-b border-line pb-3">
              {track.artworkUrl && (
                track.trackUrl ? (
                  <a
                    className="relative flex-none"
                    href={track.trackUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="Listen on Apple Music"
                    aria-label={`Listen to ${track.title} on Apple Music`}
                  >
                    <img src={track.artworkUrl} alt="" width={44} height={44} decoding="async" className="h-11 w-11 rounded-sm border-2 border-black object-cover" />
                    <span className="absolute -bottom-1 -right-1 grid h-4 w-4 place-items-center rounded-sm bg-black text-white" aria-hidden>
                      <IconExternal size={10} />
                    </span>
                  </a>
                ) : (
                  <img src={track.artworkUrl} alt="" width={44} height={44} decoding="async" className="h-11 w-11 flex-none rounded-sm border-2 border-black object-cover" />
                )
              )}
              <div className="min-w-0">
                <p className="pixel text-[10px] text-muted">Now playing</p>
                <p className="mt-0.5 truncate text-[15px] font-bold">{track.title}</p>
                <p className="truncate text-[13px] text-muted">{track.artist}</p>
                {track.because ? (
                  <p className="truncate text-[12px]" style={{ color: 'var(--accent-text)' }}>
                    Because you like {track.because}
                  </p>
                ) : track.cueReason ? (
                  <p className="truncate text-[12px]" style={{ color: 'var(--accent-text)' }}>
                    {track.cueReason}
                  </p>
                ) : null}
              </div>
            </div>
          )}

          <label className="pixel block text-[10px] text-muted" htmlFor={sliderId}>
            Music volume
          </label>
          <input
            id={sliderId}
            type="range"
            min={0}
            max={100}
            step={5}
            value={on ? level : 0}
            onChange={(event) => {
              const next = Number(event.target.value)
              if (next > 0) unlockAudio()
              if (!on && next > 0) setOn(true)
              setMusicVolume(next)
            }}
            aria-valuetext={words}
            className="mt-1 min-h-[44px] w-full cursor-pointer"
            style={{ accentColor: 'var(--accent)' }}
          />
          <div className="-mt-1 flex justify-between text-[12px] text-muted" aria-hidden>
            <span>Off</span>
            <span>Loud</span>
          </div>

          <div className="mt-3 grid gap-2">
            {on && (
              <button
                type="button"
                className="btn btn-ghost btn-sm w-full"
                onClick={() => {
                  if (level === 0) unlockAudio()
                  setMusicOn(level === 0)
                }}
              >
                {level > 0 ? 'Turn music off' : 'Turn music on'}
              </button>
            )}
            <button
              type="button"
              className={`btn btn-sm w-full ${on ? 'btn-ghost' : 'btn-primary'}`}
              onClick={() => {
                if (!on) unlockAudio()
                setOn(!on)
              }}
            >
              {on ? 'Mute all sound' : 'Unmute sound'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export function Meter({ value, max, tone = 'var(--brand)', label = 'Progress' }: { value: number; max: number; tone?: string; label?: string }) {
  const fraction = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  const percent = Math.round(fraction * 100)
  return (
    <div
      className="h-2.5 w-full overflow-hidden rounded-[3px] border"
      style={{ background: 'color-mix(in oklab, var(--ink) 10%, transparent)', borderColor: 'var(--line)' }}
      role="progressbar"
      aria-label={label}
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full w-full origin-left transition-transform duration-700 motion-reduce:transition-none"
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
