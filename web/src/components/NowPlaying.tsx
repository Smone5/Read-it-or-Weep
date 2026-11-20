// What the player sees of the jukebox: the "now playing" strip on the results screen (what the
// song is, and a way to pause or skip it), and the little switch that turns the music behind the
// questions off without silencing the host. Both show nothing at all when there is no music.

import { useEffect, useState } from 'react'
import { musicOn, nextJukeboxSong, onJukeboxChange, onMusicChange, setMusicOn, toggleJukebox, type JukeboxState } from '../lib/jukebox'
import { DECADE_LABEL } from '../lib/theme'
import type { PartyTrack } from '../lib/types'

export function useJukebox(): JukeboxState {
  const [state, setState] = useState<JukeboxState>({ active: false, track: null, playing: false, needsTap: false })
  useEffect(() => onJukeboxChange(setState), [])
  return state
}

/** Background music on or off, for this player, remembered. The host and the sound effects are not affected. */
export function MusicToggle() {
  const { active } = useJukebox()
  const [on, setOn] = useState(musicOn)
  useEffect(() => onMusicChange(setOn), [])
  if (!active) return null
  return (
    <button
      className="btn btn-ghost btn-sm"
      aria-pressed={on}
      aria-label={on ? 'Turn the background music off' : 'Turn the background music on'}
      title={on ? 'Background music on' : 'Background music off'}
      onClick={() => setMusicOn(!on)}
    >
      <span aria-hidden style={on ? undefined : { opacity: 0.45, textDecoration: 'line-through' }}>♫</span>
    </button>
  )
}

/** The record sleeve. On a player's own screen it is also the way to the full song. */
function Cover({ track, large }: { track: PartyTrack; large?: boolean }) {
  const image = <img src={track.artworkUrl ?? ''} alt="" className={`rounded-sm border-2 border-black ${large ? 'h-14 w-14 lg:h-16 lg:w-16' : 'h-12 w-12'}`} />
  if (!track.trackUrl || large) return <span className="flex-none">{image}</span>
  return (
    <a className="relative flex-none" href={track.trackUrl} target="_blank" rel="noreferrer" title="Listen on Apple Music" aria-label={`Listen to ${track.title} on Apple Music`}>
      {image}
      <span className="absolute -bottom-1 -right-1 grid h-4 w-4 place-items-center rounded-sm bg-black text-[10px] leading-none text-white" aria-hidden>↗</span>
    </a>
  )
}

export function NowPlaying({ decade, large }: { decade?: string | null; large?: boolean }) {
  const { track, playing, needsTap } = useJukebox()
  if (!track) return null
  const label = needsTap ? 'Tap play for music' : decade ? `${DECADE_LABEL[decade] ?? decade} hits` : 'Now playing'
  return (
    <section className={`card card-accent rise flex items-center gap-3 ${large ? 'p-4 lg:gap-4' : 'p-3'}`} aria-label="Music">
      {track.artworkUrl && <Cover track={track} large={large} />}
      <div className="min-w-0 flex-1">
        <p className={`pixel neon-accent flex items-center gap-2 ${large ? 'text-[10px] lg:text-xs' : 'text-[9px]'}`}>
          <span className="eq eq-mini" data-on={playing} aria-hidden><i /><i /><i /><i /><i /></span>
          <span className="truncate">{label}</span>
        </p>
        <p className={`mt-1 truncate font-bold ${large ? 'text-lg lg:text-xl' : ''}`} aria-live="polite">{track.title}</p>
        <p className={`truncate text-muted ${large ? 'lg:text-lg' : 'text-[15px]'}`}>{track.artist}</p>
      </div>
      <button className="btn btn-ghost btn-sm flex-none" onClick={toggleJukebox} aria-label={playing ? 'Pause the music' : 'Play the music'}>
        {playing ? '❚❚' : '▶'}
      </button>
      <button className="btn btn-ghost btn-sm flex-none" onClick={nextJukeboxSong} aria-label="Next song">▶▶</button>
    </section>
  )
}
