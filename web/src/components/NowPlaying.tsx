// What the player sees of the jukebox: the "now playing" strip on the results screen (what the
// song is, and a way to pause or skip it), and the music control that sets how loud the music is for
// this player, or turns it off, without touching the host - and, during a game, says what is playing.
// Both show nothing at all when there is no music.

import { useEffect, useRef, useState } from 'react'
import { musicVolume, nextJukeboxSong, onJukeboxChange, onMusicVolumeChange, setMusicOn, setMusicVolume, toggleJukebox, type JukeboxState } from '../lib/jukebox'
import type { PartyTrack } from '../lib/types'
import { IconExternal, IconHeart, IconMusic, IconMusicOff, IconPause, IconPlay, IconSkip } from './Icons'

export function useJukebox(): JukeboxState {
  const [state, setState] = useState<JukeboxState>({ active: false, track: null, playing: false, needsTap: false })
  useEffect(() => onJukeboxChange(setState), [])
  return state
}

/**
 * The player's background-music volume: a music button that opens a slider. Remembered on this device; the host
 * and the sound effects are not affected. All the way down is off.
 *
 * Automatically flips upward when anchored near the bottom edge (e.g. HostScreen footer) and layers above
 * CRT scanlines and report pictures.
 */
export function MusicToggle({ showSong = true }: { showSong?: boolean }) {
  const { active, track, playing } = useJukebox()
  const [level, setLevel] = useState(musicVolume)
  const [open, setOpen] = useState(false)
  const [openUp, setOpenUp] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)
  useEffect(() => onMusicVolumeChange(setLevel), [])
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  if (!active) return null
  const on = level > 0
  const words = !on ? 'off' : level < 35 ? 'quiet' : level <= 65 ? 'normal' : 'loud'
  const toggleOpen = () => {
    if (!open && box.current) {
      const rect = box.current.getBoundingClientRect()
      setOpenUp(window.innerHeight - rect.bottom < 250 && rect.top > 250)
    }
    setOpen((prev) => !prev)
  }
  return (
    <div ref={box} className="relative z-[55] flex-none">
      <button
        className="btn btn-ghost btn-sm"
        aria-expanded={open}
        aria-label={`Background music volume: ${words}`}
        title={`Music volume: ${words}`}
        onClick={toggleOpen}
      >
        {on ? <IconMusic size={16} /> : <IconMusicOff size={16} className="opacity-55" />}
      </button>
      {open && (
        <div
          className={`card rise absolute right-0 z-[60] w-64 p-3.5 text-left ${openUp ? 'bottom-full mb-2' : 'top-full mt-2'}`}
          role="dialog"
          aria-label="Background music"
        >
          {showSong && on && playing && track && (
            <div className="mb-4 flex items-center gap-2.5" aria-live="polite">
              {track.artworkUrl && <Cover track={track} />}
              <div className="min-w-0">
                <p className="pixel text-[10px] text-muted">Now playing</p>
                <p className="mt-0.5 truncate text-[15px] font-bold">{track.title}</p>
                <p className="truncate text-[13px] text-muted">{track.artist}</p>
                {track.because && (
                  <p className="truncate text-[12px]" style={{ color: 'var(--accent-text)' }}>
                    Because you like {track.because}
                  </p>
                )}
              </div>
            </div>
          )}
          <label className="pixel block text-[10px] text-muted" htmlFor="music-volume">
            Music volume
          </label>
          <input
            id="music-volume"
            type="range"
            min={0}
            max={100}
            step={5}
            value={level}
            onChange={(event) => setMusicVolume(Number(event.target.value))}
            aria-valuetext={words}
            className="mt-3 w-full"
            style={{ accentColor: 'var(--accent)' }}
          />
          <div className="mt-1 flex justify-between text-[12px] text-muted" aria-hidden>
            <span>Off</span>
            <span>Loud</span>
          </div>
          <button className="btn btn-ghost btn-sm mt-3 w-full" onClick={() => setMusicOn(!on)}>
            {on ? 'Turn music off' : 'Turn music on'}
          </button>
        </div>
      )}
    </div>
  )
}

/** The record sleeve. On a player's own screen it is also the way to the full song. */
function Cover({ track, large }: { track: PartyTrack; large?: boolean }) {
  const px = large ? 64 : 48
  const image = (
    <img
      src={track.artworkUrl ?? ''}
      alt=""
      width={px}
      height={px}
      decoding="async"
      className={`rounded-sm border-2 border-black object-cover ${large ? 'h-14 w-14 lg:h-16 lg:w-16' : 'h-12 w-12'}`}
    />
  )
  if (!track.trackUrl || large) return <span className="flex-none">{image}</span>
  return (
    <a className="relative flex-none" href={track.trackUrl} target="_blank" rel="noreferrer" title="Listen on Apple Music" aria-label={`Listen to ${track.title} on Apple Music`}>
      {image}
      <span className="absolute -bottom-1 -right-1 grid h-5 w-5 place-items-center rounded-sm bg-black text-[10px] leading-none text-white" aria-hidden>
        <IconExternal size={11} />
      </span>
    </a>
  )
}

export function NowPlaying({ decade, large }: { decade?: string | null; large?: boolean }) {
  const { track, playing, needsTap } = useJukebox()
  if (!track) return null
  const label = needsTap
    ? 'Tap play for music'
    : track.because
      ? track.because
      : decade
        ? `${decade} hits`
        : 'Now playing'
  return (
    <section className={`card card-accent rise np-shell relative z-[46] ${large ? 'p-4' : 'p-3'}`} aria-label="Music">
      <div className={`np-grid ${large ? 'lg:gap-4' : ''}`}>
        {track.artworkUrl && <Cover track={track} large={large} />}
        <div className="min-w-0 flex-1">
          <p className={`pixel neon-accent flex items-center gap-2 ${large ? 'text-[10px] lg:text-xs' : 'text-[10px]'}`}>
            <span className="eq eq-mini" data-on={playing} aria-hidden><i /><i /><i /><i /><i /></span>
            <span className="inline-flex min-w-0 items-center gap-1.5 truncate" title={track.because ? `Because you like ${track.because}` : undefined}>
              {track.because && <IconHeart size={11} />}
              <span className="truncate">{track.because ? <span aria-label={`Because you like ${track.because}`}>{label}</span> : label}</span>
            </span>
          </p>
          <p className={`mt-1 truncate font-bold ${large ? 'text-lg lg:text-xl' : ''}`} aria-live="polite">{track.title}</p>
          <p className={`truncate text-muted ${large ? 'lg:text-lg' : 'text-[15px]'}`}>{track.artist}</p>
        </div>
        <div className="np-actions flex flex-none items-center gap-1.5">
          <button className="btn btn-ghost btn-sm flex-none" onClick={toggleJukebox} aria-label={playing ? 'Pause the music' : 'Play the music'}>
            {playing ? <IconPause size={14} /> : <IconPlay size={14} />}
          </button>
          <button className="btn btn-ghost btn-sm flex-none" onClick={nextJukeboxSong} aria-label="Next song">
            <IconSkip size={14} />
          </button>
          <MusicToggle showSong={false} />
        </div>
      </div>
    </section>
  )
}
