// Music for the results screen.
//
// When a game ends, hits from its decade play quietly behind the results – opening with the songs
// from the game's own music breaks – each 30-second clip crossfading into the next, with the
// song's name on screen. (This is how the BHS trivia game ends, carried over.)
//
// It keeps out of the way of everything else that makes sound: it starts only once the host has
// signed off, swells in gently, dips under him if he speaks again (the player can still talk to
// him), and fades out when the player leaves.

import { musicDucked, onDuck, partyDecks, volumeAdjustable } from './music'
import { onSoundChange, soundOn } from './sound'
import { trace } from './trace'
import type { PartyTrack } from './types'


export type PartyState = {
  /** The song being heard, or null when no playlist is running. */
  track: PartyTrack | null
  playing: boolean
  /** The browser refused to start without a tap. */
  needsTap: boolean
}

const LEVEL = 0.35 // a bed to read your results by, not a performance
const UNDER_HOST = 0.07
const CROSSFADE_SEC = 3 // the next song comes in over the last three seconds of this one
const SKIP_FADE_SEC = 0.7
const SWELL_PER_SEC = 0.22 // silence to full in about a second and a half: it arrives, it does not pounce
const DIP_PER_SEC = 2.5
const LEAVE_PER_SEC = 1.2
const LOAD_TIMEOUT_MS = 8000
const TICK_MS = 40

let tracks: PartyTrack[] = []
let index = -1
let active: HTMLAudioElement | null = null // the song being heard
let incoming: HTMLAudioElement | null = null // the next one, while the two overlap
let cross = 1 // 0 → 1 as the incoming song takes over
let crossSec = CROSSFADE_SEC
let master = 0 // the level everything is scaled by
let on = false
let paused = false
let leaving = false
let loading = false
let needsTap = false
let fades = true // false where the device ignores volume changes (iPhones): no fades, clean cuts instead
let timer = 0
let session = 0
let stopListening: (() => void)[] = []
const listeners = new Set<(state: PartyState) => void>()

function snapshot(): PartyState {
  const track = on && index >= 0 ? tracks[index] : null
  const heard = Boolean(active && !active.paused) || Boolean(incoming && !incoming.paused)
  return { track, playing: on && heard && !paused, needsTap: on && needsTap }
}

function announce() {
  const state = snapshot()
  listeners.forEach((listener) => listener(state))
}

export function onPartyChange(listener: (state: PartyState) => void): () => void {
  listeners.add(listener)
  listener(snapshot())
  return () => listeners.delete(listener)
}

function silence(deck: HTMLAudioElement) {
  deck.pause()
  deck.removeAttribute('src')
  deck.load()
}

/** Load a song into a deck; resolves false if it cannot be played. */
function load(deck: HTMLAudioElement, src: string): Promise<boolean> {
  return new Promise((resolve) => {
    const done = (ok: boolean) => {
      window.clearTimeout(limit)
      deck.removeEventListener('canplay', ready)
      deck.removeEventListener('error', failed)
      resolve(ok)
    }
    const ready = () => done(true)
    const failed = () => done(false)
    const limit = window.setTimeout(failed, LOAD_TIMEOUT_MS)
    deck.addEventListener('canplay', ready)
    deck.addEventListener('error', failed)
    deck.src = src
    deck.load()
  })
}

/** Bring in the next song (wrapping round), skipping any that will not load. */
async function advance(fadeSec: number) {
  if (loading || !on || leaving) return
  loading = true
  const mine = session
  try {
    for (let attempt = 0; attempt < tracks.length; attempt += 1) {
      index = (index + 1) % tracks.length
      const [a, b] = partyDecks()
      const deck = active === a ? b : a
      const ok = await load(deck, tracks[index].previewUrl)
      if (mine !== session || !on) return
      if (!ok) continue
      deck.volume = fades ? 0 : 1
      if (!fades && active) silence(active) // no crossfade possible: cut cleanly rather than overlap at full volume
      trace('party.song', { title: tracks[index].title })
      if (!paused && !(musicDucked() && !fades)) {
        try {
          await deck.play()
          needsTap = false
        } catch (error) {
          if ((error as Error)?.name === 'NotAllowedError') needsTap = true
          else continue
        }
        if (mine !== session || !on) {
          deck.pause()
          return
        }
      }
      if (!fades && active) active = null
      incoming = deck
      cross = active ? 0 : 1
      crossSec = fadeSec
      if (!active) {
        active = deck
        incoming = null
      }
      announce()
      return
    }
    stopAfterParty(0) // nothing in the list would play
  } finally {
    loading = false
  }
}

function tick() {
  const step = TICK_MS / 1000
  if (fades) {
    const goal = leaving || paused ? 0 : musicDucked() ? UNDER_HOST : LEVEL
    const rate = leaving ? LEAVE_PER_SEC : goal < master ? DIP_PER_SEC : SWELL_PER_SEC
    master += Math.sign(goal - master) * Math.min(Math.abs(goal - master), rate * step)
    if (incoming && active) {
      cross = Math.min(1, cross + step / crossSec)
      active.volume = master * (1 - cross)
      incoming.volume = master * cross
      if (cross >= 1) {
        silence(active)
        active = incoming
        incoming = null
      }
    } else if (active) active.volume = master
  }
  if (leaving && (!fades || master <= 0.005)) {
    shutDown()
    return
  }
  // Start the next song over the tail of this one.
  if (active && !incoming && !paused && active.duration && active.currentTime >= active.duration - (fades ? CROSSFADE_SEC : 0.25)) {
    void advance(CROSSFADE_SEC)
  }
}

function shutDown() {
  window.clearInterval(timer)
  timer = 0
  session += 1
  on = false
  leaving = false
  stopListening.forEach((stop) => stop())
  stopListening = []
  partyDecks().forEach(silence)
  active = null
  incoming = null
  index = -1
  master = 0
  trace('party.stop')
  announce()
}

function hold() {
  active?.pause()
  incoming?.pause()
  announce()
}

function release() {
  if (paused || !on) return
  const attempt = (deck: HTMLAudioElement | null) =>
    deck?.play().then(
      () => {
        needsTap = false
        announce()
      },
      (error: unknown) => {
        if ((error as Error)?.name === 'NotAllowedError') needsTap = true
        announce()
      },
    )
  void attempt(active)
  void attempt(incoming)
}

/** Start the playlist. Safe to call when one is already running (it carries on). */
export function startAfterParty(list: PartyTrack[]) {
  if (on || !list.length) return
  const [deck] = partyDecks()
  fades = volumeAdjustable(deck)
  tracks = list
  index = -1
  on = true
  leaving = false
  paused = !soundOn()
  needsTap = false
  master = 0
  session += 1
  trace('party.start', { songs: list.length })
  stopListening = [
    // Where the volume cannot be changed, the music steps aside completely while the host talks.
    onDuck((ducked) => {
      if (fades) return
      if (ducked) hold()
      else release()
    }),
    onSoundChange((sound) => {
      paused = !sound
      if (paused) hold()
      else release()
    }),
  ]
  // A song that ends before its successor was started (its length was unknown): move on now.
  partyDecks().forEach((player) => {
    const ended = () => {
      if (on && player === active && !incoming) void advance(CROSSFADE_SEC)
    }
    player.addEventListener('ended', ended)
    stopListening.push(() => player.removeEventListener('ended', ended))
  })
  timer = window.setInterval(tick, TICK_MS)
  void advance(CROSSFADE_SEC)
}

/** Pause or resume (the button on the "now playing" strip). */
export function toggleAfterParty() {
  if (!on) return
  if (needsTap) {
    release()
    return
  }
  paused = !paused
  if (paused) hold()
  else {
    if (fades) master = Math.min(master, 0.05) // come back gently
    release()
  }
}

/** Skip to the next song. */
export function nextPartySong() {
  if (!on) return
  if (incoming && active) {
    silence(active) // already changing: finish that change at once
    active = incoming
    incoming = null
  }
  void advance(SKIP_FADE_SEC)
}

/** Fade the playlist out and stop it (the player is leaving the results). */
export function stopAfterParty(fadeMs = 400) {
  if (!on) return
  if (!fadeMs || !fades || master <= 0.01) {
    shutDown()
    return
  }
  leaving = true
}
