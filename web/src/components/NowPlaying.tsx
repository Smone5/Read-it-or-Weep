// The "now playing" strip for the results-screen playlist: what the song is, and a way to
// pause or skip it. It shows nothing at all until a playlist is actually running.

import { useEffect, useState } from 'react'
import { nextPartySong, onPartyChange, toggleAfterParty, type PartyState } from '../lib/afterParty'
import { DECADE_LABEL } from '../lib/theme'
import type { PartyTrack } from '../lib/types'

export function useAfterParty(): PartyState {
  const [state, setState] = useState<PartyState>({ track: null, playing: false, needsTap: false })
  useEffect(() => onPartyChange(setState), [])
  return state
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
  const { track, playing, needsTap } = useAfterParty()
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
      <button className="btn btn-ghost btn-sm flex-none" onClick={toggleAfterParty} aria-label={playing ? 'Pause the music' : 'Play the music'}>
        {playing ? '❚❚' : '▶'}
      </button>
      <button className="btn btn-ghost btn-sm flex-none" onClick={nextPartySong} aria-label="Next song">▶▶</button>
    </section>
  )
}
