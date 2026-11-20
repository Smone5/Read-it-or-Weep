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

/**
 * Where the game is: reading a question ('think'), between things ('gap'), waiting for Next after the host ('wait',
 * easing back to 'linger' if the player stays there), a streak ('swell'), a round's title card ('title'), the show's
 * opening ('intro'), the results screen ('party'), or a "name that tune" clip ('out').
 */
export type JukeboxLevel = 'think' | 'gap' | 'wait' | 'linger' | 'swell' | 'title' | 'intro' | 'party' | 'out'

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
// Out of the way while a question is on screen, so players can concentrate; back up between questions; a swell
// for the big moments: the opening, each round's title card, a streak. (The organiser's calls, 2026-11-20: first
// "quieter, sort of background unless they want to turn it up"; then "still a little loud in the question" and
// "come up later, sort of between questions or key parts of the game".) "think" now sits barely above the level
// the music drops to under the host's voice.
// "intro" is the show's first seconds, before the host speaks: loud, on its own, like a theme. (The organiser,
// 2026-11-20: "start with loud music, then get quiet as the host speaks. The music can be louder in the background
// when the host is talking or between questions." Until then the first song faded in and reached its peak just as
// the host started, so there was no loud start at all.) It plays well above the level every song is evened out to -
// "music still not loud enough at beginning of the game before host" - about 5 dB above it, where most modern songs
// sit by themselves; a limiter (see LIMIT_DB) keeps the loudest moments from breaking up.
// After an answer: "gap" while the result is shown and the host reacts, then "wait" - up - once he has finished
// and the player is deciding when to tap Next. (The organiser, 2026-11-20: "make the music what is now at the end
// of the question, but raise it while the user is waiting to push next.") If the player stays on the answer, the
// music eases back down to the "gap" level ("linger": "...then fade away, but be abrupt when they click next").
// A round's title card is the loudest moment after the opening: louder than the wait for Next (a game-show producer's
// review, 2026-11-20: "a round's title should be louder than the wait between questions"). A streak swells a little less.
const LEVELS: Record<JukeboxVolume, { think: number; gap: number; wait: number; swell: number; title: number; intro: number }> = {
  quiet: { think: 0.05, gap: 0.26, wait: 0.5, swell: 0.48, title: 0.6, intro: 1 },
  medium: { think: 0.08, gap: 0.42, wait: 0.8, swell: 0.7, title: 0.85, intro: 1.5 },
  loud: { think: 0.13, gap: 0.6, wait: 1, swell: 0.92, title: 1, intro: 2 },
}
const PARTY = 0.45 // a bed to read your results by, not a performance
// Under the host's voice the music dips, but stays audible: the organiser asked for it "a little louder ... not so
// loud you can't understand the host" (2026-11-20). Measured in a game, voice against music: at 0.06 (before) his
// voice was about 20 dB above the music; at 0.14, 13 dB; 0.17 leaves about 10 dB - plainly there, still clear. The
// songs have singers in them, which compete with speech far more than an instrumental would: keep 10 dB.
// Since 2026-11-20 the music is also *carved* under him rather than only turned down (see CARVE below), which leaves
// room for it to sit higher. Measured at 0.26 with the carve: his voice a median 9 dB above the music, 12 dB in the
// 1-4 kHz band where speech is understood. The organiser still found it "too quiet at times" (2026-11-20) - on a
// laptop's speakers the carve takes away much of what is heard of a song - so it sits at 0.34: about 2.3 dB louder,
// with his voice still about 10 dB clear where speech is understood.
const UNDER_HOST = 0.34 // (0.17 left his voice 10 dB above the music; the organiser wanted the music up a little more)
// The big moments keep the music up under the host, so the show's opening and a round's title card sound like a
// broadcast. (The organiser: "music needs to be louder when the game starts and the host is speaking, but then get
// quieter for the question.") Measured in a game: at 0.12 it sat only 1.5 dB above the question level; at 0.22 it is
// clearly up with his voice about 8 dB above it; at 0.28 his voice was only 6 dB above it - too close for the opening,
// where he is explaining the rules over songs with singers in them.
const UNDER_HOST_BIG = 0.34
// Under the show's opening the music glides down (see ride) as the host fades in: the theme hands over to him. ("Need
// the music to fade out and the host to fade in more in the intro.") It settles where it sits under him for the rest
// of the game: at 0.14 with the carve it was 16 dB under his voice and, on a laptop's speakers, simply gone for the
// twenty-odd seconds of the opening ("music doesn't play when the game starts", 2026-11-20).
const UNDER_HOST_INTRO = 0.34
// Turning the music up with ♫ raises it under the host too - as far as ♫ at 75 (7 dB); beyond that only the rest of
// the game gets louder, so he never disappears under it. (A cap of 2 dB, tried first, meant a player who turned the
// music up still found it "too quiet at times": while the host talks, which is much of a game.)
const UNDER_HOST_PLAYER_MAX = 2.25
// The carve: while his voice sounds, the music loses some of the band where speech is understood (consonants,
// 1.5-4 kHz - also where a phone speaker is loudest and where the songs' singers sit). That buys more clarity than
// turning the whole song down would, so the song can stay fuller underneath him.
const CARVE_HZ = 2500
const CARVE_Q = 0.8
const CARVE_DB = -8
const TARGET_RMS = 0.16 // every song is brought to about this loudness, so an old recording is not lost behind a new one
const PEAK_ROOM = 0.98 // ...but never so far that its loudest moment would clip
// The music's last stage: a limiter that holds its peaks just under full scale, so the opening can play above the
// evened-out level without the loudest moments breaking up. Below LIMIT_DB it changes nothing - the trim in front of
// it takes back the make-up gain a compressor adds by itself (the Web Audio rule: the full-range gain to the 0.6).
const LIMIT_DB = -3
const LIMIT_RATIO = 20
const LIMIT_TRIM = (10 ** ((LIMIT_DB * (1 - 1 / LIMIT_RATIO)) / 20)) ** 0.6
const MAX_GAIN = 2.5 // the highest the music's own gain goes (the opening at "loud", or ♫ turned up)
// After his last word the music waits a moment before coming back up: whatever the game does next (a question,
// silence for a clip, the title's button) decides where it goes, so it never starts up only to be pulled down again.
const RELEASE_HOLD_MS = 150

const CROSSFADE_SEC = 3 // the next song comes in over the last three seconds of this one
const SKIP_FADE_SEC = 0.7
const SWELL_SEC = 1.6 // from silence: it arrives, it does not pounce
const INTRO_SEC = 0.3 // ...except the show's opening and a round's title, which hit
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
let carve: BiquadFilterNode | null = null
let carved = false
let voiceWas = false
let voiceEndedAt = -Infinity
let releaseTimer = 0
let wakeWatched = false
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

// --------------------------------------------------------------------------- the player's own volume
// Each player can turn the background music up or down for themselves, remembered on their device. It
// scales the organiser's setting rather than replacing it: 50 is the music exactly as the organiser set it,
// 0 is off, 100 is about four times as loud (never past full scale). The host and the sound effects are
// not affected, and the music still dips under the host and steps aside for a "name that tune" clip.

const KEY = 'quiz.music' // before there was a volume: 'on' | 'off'
const VOLUME_KEY = 'quiz.music.volume'
const AS_SET = 50
const volumeListeners = new Set<(level: number) => void>()

const clampLevel = (value: number) => Math.max(0, Math.min(100, Math.round(Number.isFinite(value) ? value : AS_SET)))

let musicLevel = (() => {
  try {
    const saved = localStorage.getItem(VOLUME_KEY)
    if (saved !== null) return clampLevel(Number(saved))
    return localStorage.getItem(KEY) === 'off' ? 0 : AS_SET
  } catch {
    return AS_SET
  }
})()
let lastHeard = musicLevel || AS_SET

/** The player's music volume, 0 (off) to 100; 50 is as the organiser set it. */
export function musicVolume(): number {
  return musicLevel
}

export function setMusicVolume(value: number) {
  musicLevel = clampLevel(value)
  if (musicLevel > 0) lastHeard = musicLevel
  try {
    localStorage.setItem(VOLUME_KEY, String(musicLevel))
  } catch {
    /* ignore */
  }
  volumeListeners.forEach((listener) => listener(musicLevel))
  trace('jukebox.volume', { level: musicLevel })
  sync()
}

export function onMusicVolumeChange(listener: (level: number) => void): () => void {
  volumeListeners.add(listener)
  return () => volumeListeners.delete(listener)
}

/** How much the player's volume scales the organiser's: squared, so the slider feels even to the ear. */
function playerScale(): number {
  return (musicLevel / AS_SET) ** 2
}

/** Background music can be switched off by itself, leaving the host and the sound effects. */
export function musicOn(): boolean {
  return musicLevel > 0
}

export function setMusicOn(value: boolean) {
  setMusicVolume(value ? lastHeard : 0)
}

export function onMusicChange(listener: (on: boolean) => void): () => void {
  return onMusicVolumeChange((level) => listener(level > 0))
}

// --------------------------------------------------------------------------- state

function wanted(): boolean {
  return on && level !== 'out' && !paused && soundOn() && musicLevel > 0 && queue.length > 0
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
  const levels = LEVELS[volume]
  const base =
    level === 'party'
      ? PARTY
      : level === 'think' || level === 'wait' || level === 'swell' || level === 'title' || level === 'intro'
        ? levels[level]
        : levels.gap // 'gap' and 'linger'
  const scale = playerScale()
  if (!voiceIsOver()) return Math.min(MAX_GAIN, base * scale)
  const duck = level === 'intro' ? UNDER_HOST_INTRO : level === 'swell' || level === 'title' ? UNDER_HOST_BIG : UNDER_HOST
  return Math.min(MAX_GAIN, base * scale, duck * Math.min(scale, UNDER_HOST_PLAYER_MAX))
}

/** Seconds to cover about two thirds of the way to a new level. Each kind of move has its own speed. */
function settleFor(target: number): number {
  // Into a music break the song fades away (about a second) as the clip comes in; anything else fades a little faster.
  if (target === 0) return level === 'out' ? 0.45 : 0.3
  if (target < riding) {
    if (voiceIsOver()) return level === 'intro' ? 0.35 : 0.07 // the opening hands over to the host in a glide; a duck is quick
    if (level === 'linger') return 1.5 // the player is lingering on the answer: the music eases away
    if (level === 'party') return 0.5 // from the finale down to the after-party bed
    return 0.07 // a question arriving, or Next: out of the way at once
  }
  if (level === 'intro') return 0.08 // the theme hits
  if (level === 'title') return 0.12 // ...and so does a round's title, after its sting
  return 0.5 // otherwise the music comes up gently
}

/** The carve under the host's voice: in as he starts, out again (more slowly) once he has finished. */
function carveRide() {
  if (!carve) return
  const want = voiceIsOver()
  if (want === carved) return
  carved = want
  const now = audioContext().currentTime
  carve.gain.cancelScheduledValues(now)
  carve.gain.setTargetAtTime(want ? CARVE_DB : 0, now, want ? (level === 'intro' ? 0.35 : 0.07) : 0.4)
}

/** Send the volume towards where it should be now. */
function ride() {
  if (!master) return
  carveRide()
  const context = audioContext()
  const target = goal()
  if (target === riding) return
  if (target > riding) {
    const wait = RELEASE_HOLD_MS - (performance.now() - voiceEndedAt)
    if (wait > 0) {
      window.clearTimeout(releaseTimer)
      releaseTimer = window.setTimeout(ride, wait)
      return
    }
  }
  const settle = settleFor(target)
  master.gain.cancelScheduledValues(context.currentTime)
  master.gain.setTargetAtTime(target, context.currentTime, settle)
  riding = target
  // The exact level set, and how fast (for checks without ears).
  trace('jukebox.gain', { level, target: Math.round(target * 1000) / 1000, settle, under: voiceIsOver() })
}

// --------------------------------------------------------------------------- songs

function loudnessGain(buffer: AudioBuffer): number {
  // Both channels: a 1960s stereo mix can have whole instruments on one side only.
  let sum = 0
  let count = 0
  let peak = 0
  for (let channel = 0; channel < Math.min(2, buffer.numberOfChannels); channel += 1) {
    const samples = buffer.getChannelData(channel)
    for (let i = 0; i < samples.length; i += 4) {
      const value = samples[i]
      if (i % 16 === 0) {
        sum += value * value
        count += 1
      }
      const size = value < 0 ? -value : value
      if (size > peak) peak = size
    }
  }
  const rms = Math.sqrt(sum / Math.max(1, count))
  const gain = rms > 0.001 ? Math.min(1.4, Math.max(0.4, TARGET_RMS / rms)) : 1
  return peak > 0 ? Math.min(gain, PEAK_ROOM / peak) : gain
}

/**
 * An equal-power fade (a quarter sine), drawn as short straight segments: two songs crossing keep the
 * same loudness all the way through, where a straight-line crossfade dips by 3 dB in the middle.
 * (Segments rather than a value curve, because a curve cannot be cut short if the player skips.)
 */
function powerFade(param: AudioParam, from: number, to: number, at: number, seconds: number) {
  const steps = 8
  param.setValueAtTime(from, at)
  for (let step = 1; step <= steps; step += 1) {
    const x = step / steps
    // In along sin, out along cos: at the midpoint each is at 0.71 of its level, so their powers add up to one.
    const value = to > from ? from + (to - from) * Math.sin((x * Math.PI) / 2) : to + (from - to) * Math.cos((x * Math.PI) / 2)
    param.linearRampToValueAtTime(value, at + seconds * x)
  }
}

/** How far a song's own fade-in had got by a given moment (it rises along a quarter sine). */
function reachedAt(voice: Voice, at: number): number {
  const x = Math.min(1, Math.max(0, (at - voice.startsAt) / voice.fadeIn))
  return voice.level * Math.sin((x * Math.PI) / 2)
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
    carve = context.createBiquadFilter()
    carve.type = 'peaking'
    carve.frequency.value = CARVE_HZ
    carve.Q.value = CARVE_Q
    carve.gain.value = 0
    carved = false
    const trim = context.createGain()
    trim.gain.value = LIMIT_TRIM
    const limiter = context.createDynamicsCompressor()
    limiter.threshold.value = LIMIT_DB
    limiter.knee.value = 0
    limiter.ratio.value = LIMIT_RATIO
    limiter.attack.value = 0.001
    limiter.release.value = 0.08
    master.connect(carve)
    carve.connect(trim)
    trim.connect(limiter)
    limiter.connect(context.destination)
    riding = 0
  }
  const at = Math.max(context.currentTime + 0.06, notBefore)
  const old = current
  const fade = old ? Math.max(0.3, Math.min(fadeSec, old.endsAt - at)) : fadeSec
  const source = context.createBufferSource()
  source.buffer = song.buffer
  const gain = context.createGain()
  powerFade(gain.gain, 0, song.gain, at, fade)
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
    const reached = reachedAt(old, at)
    old.gain.gain.cancelScheduledValues(at)
    powerFade(old.gain.gain, reached, 0, at, fade)
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
          startWhenAwake()
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

/**
 * The browser had not woken its audio yet when the song was ready (it can take a moment, e.g. while headphones
 * connect). Start the music the moment it wakes, rather than leaving it silent until the game next changes scene.
 */
function startWhenAwake() {
  if (wakeWatched) return
  wakeWatched = true
  const context = audioContext()
  const onChange = () => {
    if (context.state !== 'running') return
    context.removeEventListener('statechange', onChange)
    wakeWatched = false
    trace('jukebox.awake', { waiting: needsTap })
    if (!needsTap) return
    needsTap = false
    if (wanted() && !current && !busy) void advance(level === 'intro' || level === 'title' ? INTRO_SEC : SWELL_SEC)
    else announce()
  }
  context.addEventListener('statechange', onChange)
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
    if (!current && !busy) void advance(level === 'intro' || level === 'title' ? INTRO_SEC : SWELL_SEC)
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

/** A voice started or stopped: note when it stopped (see RELEASE_HOLD_MS), then move the music. */
function onVoice() {
  const over = voiceIsOver()
  if (voiceWas && !over) voiceEndedAt = performance.now()
  voiceWas = over
  ride()
}

function listen() {
  if (timer) return
  stopListening = [onVoiceOver(onVoice), onSoundChange(sync)]
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
