// The game's jukebox: hits from one decade, playing from the first question to the results screen.
//
// It does two jobs that are really one stream of music:
//   * the soundtrack – quietly behind the questions, coming up in the gaps between them, dipping
//     under the host whenever he speaks, and stepping aside completely for a "name that tune" clip;
//   * the after-party – the same music carrying on behind the results, with the song's name on
//     screen and a way to pause or skip it. (This is how the BHS trivia game ends, carried over.)
//
// Each song is a 30-second preview. It is fetched and decoded, then played through Web Audio rather
// than an <audio> element, for two reasons: the volume moves all the time and an iPhone ignores
// volume changes on media elements, and Web Audio fades one song into the next exactly, on the
// audio clock, even while the page is busy or in the background.

import { onVoiceOver, voiceIsOver } from './music'
import { audioContext, onSoundChange, soundOn } from './sound'
import { trace } from './trace'
import type { PartyTrack } from './types'

/** Where the game is: reading a question, between questions, on the results screen, or silent. */
export type JukeboxLevel = 'think' | 'gap' | 'party' | 'out'

/** How loud the organiser wants the music behind the questions. */
export type JukeboxVolume = 'quiet' | 'medium' | 'loud'

export type JukeboxState = {
  /** True from the moment a game (or a results screen) has music, whether or not it is sounding. */
  active: boolean
  /** The song being heard – or the one that will be, while paused or waiting for a tap. */
  track: PartyTrack | null
  playing: boolean
  /** The browser refused to start without a tap. */
  needsTap: boolean
}

// Levels are the gain on songs that have each been brought to the same loudness first.
const LEVELS: Record<JukeboxVolume, { think: number; gap: number }> = {
  quiet: { think: 0.16, gap: 0.34 },
  medium: { think: 0.28, gap: 0.5 },
  loud: { think: 0.42, gap: 0.7 },
}
const PARTY = 0.45 // a bed to read your results by, not a performance
const UNDER_HOST = 0.06 // measured: keeps the music about 12 dB under a host voice, which is where speech stays easy to follow
const TARGET_RMS = 0.16 // every song is brought to about this loudness, so an old recording is not lost behind a new one

const CROSSFADE_SEC = 3 // the next song comes in over the last three seconds of this one
const SKIP_FADE_SEC = 0.7
const SWELL_SEC = 1.6 // from silence: it arrives, it does not pounce
const LOOKAHEAD_SEC = 1.5 // the next song is lined up this long before it is due
const TICK_MS = 250
const REST_AFTER_MS = 1500 // faded right down, a song is stopped rather than left running unheard
const LOAD_TIMEOUT_MS = 12000
const GIVE_UP_AFTER = 5 // songs in a row that would not load: no music, and the game carries on without it
const KEPT_DECODED = 3 // a decoded song is about ten megabytes

type Decoded = { buffer: AudioBuffer; gain: number }
type Voice = { source: AudioBufferSourceNode; gain: GainNode; track: PartyTrack; level: number; startsAt: number; fadeIn: number; endsAt: number }

let queue: PartyTrack[] = []
let index = -1
let level: JukeboxLevel = 'out'
let volume: JukeboxVolume = 'medium'
let on = false
let paused = false
let needsTap = false
let master: GainNode | null = null
let riding = 0 // the level the master gain was last sent towards
let current: Voice | null = null
let leaving: Voice[] = [] // songs fading out under the one that replaced them
let shown: PartyTrack | null = null
let busy = false // a song is being fetched
let token = 0
let session = 0
let timer = 0
let restTimer = 0
let failures = 0
let stopListening: (() => void)[] = []
const heard = new Set<string>()
const decoded = new Map<string, Promise<Decoded | null>>()
const listeners = new Set<(state: JukeboxState) => void>()

// --------------------------------------------------------------------------- the player's own switch

const KEY = 'quiz.music'
const musicListeners = new Set<(on: boolean) => void>()

let musicWanted = (() => {
  try {
    return localStorage.getItem(KEY) !== 'off'
  } catch {
    return true
  }
})()

/** Background music can be switched off by itself, leaving the host and the sound effects. */
export function musicOn(): boolean {
  return musicWanted
}

export function setMusicOn(value: boolean) {
  musicWanted = value
  try {
    localStorage.setItem(KEY, value ? 'on' : 'off')
  } catch {
    /* ignore */
  }
  musicListeners.forEach((listener) => listener(value))
  sync()
}

export function onMusicChange(listener: (on: boolean) => void): () => void {
  musicListeners.add(listener)
  return () => musicListeners.delete(listener)
}

// --------------------------------------------------------------------------- state

function wanted(): boolean {
  return on && level !== 'out' && !paused && soundOn() && musicWanted && queue.length > 0
}

function snapshot(): JukeboxState {
  return { active: on, track: on ? (current?.track ?? shown) : null, playing: Boolean(current) && wanted(), needsTap: on && needsTap }
}

function announce() {
  const state = snapshot()
  listeners.forEach((listener) => listener(state))
}

export function onJukeboxChange(listener: (state: JukeboxState) => void): () => void {
  listeners.add(listener)
  listener(snapshot())
  return () => listeners.delete(listener)
}

// --------------------------------------------------------------------------- the volume

function goal(): number {
  if (!wanted()) return 0
  const base = level === 'party' ? PARTY : LEVELS[volume][level === 'think' ? 'think' : 'gap']
  return voiceIsOver() ? Math.min(base, UNDER_HOST) : base
}

/** Send the volume towards where it should be now: down quickly, back up slowly. */
function ride() {
  if (!master) return
  const context = audioContext()
  const target = goal()
  if (target === riding) return
  const settle = target === 0 ? 0.3 : target < riding ? 0.07 : 0.5 // seconds to cover about two thirds of the distance
  master.gain.cancelScheduledValues(context.currentTime)
  master.gain.setTargetAtTime(target, context.currentTime, settle)
  riding = target
}

// --------------------------------------------------------------------------- songs

function loudnessGain(buffer: AudioBuffer): number {
  const samples = buffer.getChannelData(0)
  let sum = 0
  let count = 0
  for (let i = 0; i < samples.length; i += 16) {
    sum += samples[i] * samples[i]
    count += 1
  }
  const rms = Math.sqrt(sum / Math.max(1, count))
  return rms > 0.001 ? Math.min(1.4, Math.max(0.4, TARGET_RMS / rms)) : 1
}

/** Fetch a song and decode it; resolves null if it cannot be played. */
function decode(url: string): Promise<Decoded | null> {
  let pending = decoded.get(url)
  if (pending) return pending
  const abort = new AbortController()
  const limit = window.setTimeout(() => abort.abort(), LOAD_TIMEOUT_MS)
  pending = fetch(url, { signal: abort.signal })
    .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error(`clip answered ${response.status}`))))
    .then((data) => new Promise<AudioBuffer>((resolve, reject) => void audioContext().decodeAudioData(data, resolve, reject)))
    .then((buffer) => ({ buffer, gain: loudnessGain(buffer) }))
    .catch(() => {
      decoded.delete(url) // try again if it comes round a second time
      return null
    })
    .finally(() => window.clearTimeout(limit))
  decoded.set(url, pending)
  for (const key of decoded.keys()) {
    if (decoded.size <= KEPT_DECODED) break
    if (key !== url && key !== current?.track.previewUrl) decoded.delete(key)
  }
  return pending
}

/** Browsers only let sound start after a tap; wait briefly for the audio to wake, and say whether it did. */
async function awake(): Promise<boolean> {
  const running = () => audioContext().state === 'running'
  if (running()) return true
  await Promise.race([audioContext().resume().catch(() => undefined), new Promise((resolve) => window.setTimeout(resolve, 400))])
  return running()
}

function cut(voice: Voice) {
  voice.source.onended = null
  try {
    voice.source.stop()
  } catch {
    /* never started, or already over */
  }
  voice.source.disconnect()
  voice.gain.disconnect()
}

/** Start a song, fading out whatever was playing. `notBefore` is when a crossfade is due. */
function begin(track: PartyTrack, song: Decoded, fadeSec: number, notBefore = 0) {
  const context = audioContext()
  if (!master) {
    master = context.createGain()
    master.gain.value = 0
    master.connect(context.destination)
    riding = 0
  }
  const at = Math.max(context.currentTime + 0.06, notBefore)
  const old = current
  const fade = old ? Math.max(0.3, Math.min(fadeSec, old.endsAt - at)) : fadeSec
  const source = context.createBufferSource()
  source.buffer = song.buffer
  const gain = context.createGain()
  gain.gain.setValueAtTime(0, at)
  gain.gain.linearRampToValueAtTime(song.gain, at + fade)
  source.connect(gain)
  gain.connect(master)
  const voice: Voice = { source, gain, track, level: song.gain, startsAt: at, fadeIn: fade, endsAt: at + song.buffer.duration }
  source.onended = () => {
    source.disconnect()
    gain.disconnect()
    if (current !== voice) return
    current = null // it ran out before a successor could be lined up
    sync()
  }
  source.start(at)
  if (old) {
    // Wherever the old song's own fade-in had got to, it leaves from there.
    const reached = old.level * Math.min(1, Math.max(0, (at - old.startsAt) / old.fadeIn))
    old.gain.gain.cancelScheduledValues(at)
    old.gain.gain.setValueAtTime(reached, at)
    old.gain.gain.linearRampToValueAtTime(0, at + fade)
    old.source.onended = () => {
      old.source.disconnect()
      old.gain.disconnect()
      leaving = leaving.filter((other) => other !== old)
    }
    try {
      old.source.stop(at + fade + 0.05)
    } catch {
      /* already over */
    }
    leaving.push(old)
  }
  current = voice
  shown = track
  heard.add(track.previewUrl)
  needsTap = false
  ride()
  trace('jukebox.song', { title: track.title, level })
  announce()
  if (queue.length > 1) void decode(queue[(index + 1) % queue.length].previewUrl) // line the next one up
}

/** Bring in the next song (going round the list), skipping any that will not load. */
async function advance(fadeSec: number, notBefore = 0) {
  if (busy || !wanted()) return
  busy = true
  const mine = ++token
  try {
    const tries = Math.min(queue.length, GIVE_UP_AFTER)
    for (let attempt = 0; attempt < tries; attempt += 1) {
      index = (index + 1) % queue.length
      const track = queue[index]
      const song = await decode(track.previewUrl)
      if (mine !== token) return
      if (!song) {
        failures += 1
        continue
      }
      failures = 0
      const ready = wanted() && (await awake())
      if (mine !== token) return
      if (!ready) {
        index -= 1 // not played after all: it is still the next song
        if (wanted()) {
          needsTap = true
          shown = track
          announce()
        }
        return
      }
      begin(track, song, fadeSec, notBefore)
      return
    }
    if (failures >= tries) {
      trace('jukebox.unavailable')
      shutDown() // nothing would play on this device or connection: the game goes on without music
    }
  } finally {
    if (mine === token) busy = false
  }
}

/** Stop everything that is sounding, keeping the place in the list. */
function rest() {
  token += 1
  busy = false
  if (current) cut(current)
  leaving.forEach(cut)
  current = null
  leaving = []
  if (master) {
    master.gain.cancelScheduledValues(audioContext().currentTime)
    master.gain.value = 0
  }
  riding = 0
}

/** Make what is sounding match what should be: called whenever anything changes. */
function sync() {
  window.clearTimeout(restTimer)
  ride()
  if (wanted()) {
    if (!current && !busy) void advance(SWELL_SEC)
  } else if (current || leaving.length || busy) {
    restTimer = window.setTimeout(() => {
      if (wanted()) return
      rest()
      announce()
    }, REST_AFTER_MS)
  }
  announce()
}

function tick() {
  if (!current || busy || !wanted()) return
  const due = current.endsAt - CROSSFADE_SEC
  if (audioContext().currentTime >= due - LOOKAHEAD_SEC) void advance(CROSSFADE_SEC, due)
}

function listen() {
  if (timer) return
  stopListening = [onVoiceOver(ride), onSoundChange(sync)]
  timer = window.setInterval(tick, TICK_MS)
}

function shutDown() {
  window.clearInterval(timer)
  window.clearTimeout(restTimer)
  timer = 0
  stopListening.forEach((stop) => stop())
  stopListening = []
  on = false
  rest()
  queue = []
  index = -1
  shown = null
  paused = false
  needsTap = false
  failures = 0
  heard.clear()
  decoded.clear()
  trace('jukebox.stop')
  announce()
}

// --------------------------------------------------------------------------- what the game asks for

/** Tell the jukebox where the game is. Safe at any time, including before any music has been started. */
export function setJukeboxLevel(next: JukeboxLevel) {
  if (level === next) return
  level = next
  trace('jukebox.level', { level })
  if (on) sync()
}

/** Start the soundtrack for a game. The level is whatever the game last asked for. */
export function startJukebox(list: PartyTrack[], options: { volume?: JukeboxVolume } = {}) {
  if (!list.length) return
  session += 1
  listen()
  queue = list
  index = -1
  failures = 0
  heard.clear()
  volume = options.volume ?? 'medium'
  on = true
  paused = false
  trace('jukebox.start', { songs: list.length, volume })
  sync()
}

/**
 * The results screen: carry the music on (or start it) as a playlist with its songs named.
 * Songs already heard during the game go to the back of the queue.
 */
export function startParty(list: PartyTrack[]) {
  if (!list.length) return
  session += 1
  listen()
  queue = [...list.filter((track) => !heard.has(track.previewUrl)), ...list.filter((track) => heard.has(track.previewUrl))]
  index = -1
  failures = 0
  on = true
  paused = false
  level = 'party'
  trace('party.start', { songs: list.length, carriedOn: Boolean(current) })
  sync()
}

/** Pause or resume (the button on the "now playing" strip). Resuming begins the next song. */
export function toggleJukebox() {
  if (!on) return
  void audioContext().resume().catch(() => undefined) // inside the tap, where a browser will allow it
  if (needsTap) {
    needsTap = false // this call is the tap
    paused = false
  } else paused = !paused
  sync()
}

/** Skip to the next song. */
export function nextJukeboxSong() {
  if (!on || busy) return
  void audioContext().resume().catch(() => undefined)
  if (!wanted()) {
    paused = false
    needsTap = false
    sync()
    return
  }
  void advance(SKIP_FADE_SEC)
}

/** Fade the music out and stop it (the player is leaving, or the game has no music on its results screen). */
export function stopJukebox(fadeMs = 400) {
  if (!on) return
  const mine = ++session
  on = false
  window.clearTimeout(restTimer)
  if (!fadeMs || !current) {
    shutDown()
    return
  }
  if (master) {
    master.gain.cancelScheduledValues(audioContext().currentTime)
    master.gain.setTargetAtTime(0, audioContext().currentTime, fadeMs / 3000)
    riding = 0
  }
  announce()
  window.setTimeout(() => {
    if (mine === session && !on) shutDown()
  }, fadeMs)
}
