// One shared <audio> element for music-round clips ("name that tune").
//
// Phones (iOS especially) only let a page start audio from inside a tap. By "unlocking"
// this single element during the Start tap, every later clip can begin on its own.
//
// (The music that plays behind the questions and the results is the jukebox's, in jukebox.ts.
// It listens to this module only to know when the host has the floor.)

import { audioContext, onSoundChange, soundOn } from './sound'
import { trace } from './trace'

let element: HTMLAudioElement | null = null
// The clip plays through the game's Web Audio mixer, and its level is this gain. iPhones ignore a media element's
// own volume - while still reporting the value they were given - so a clip "ducked" that way stayed at full
// volume under the host and into the next question on phones (user, 2026-11-21). Measured in iPhone Safari: this
// gain does turn it down. (Without Web Audio the element's volume is the fallback; see volumeAdjustable.)
let clipGain: GainNode | null = null
let clipLevel = 1
let silentUrl: string | null = null
let playEpoch = 0
let soundSubscribed = false

function silence(): string {
  if (silentUrl) return silentUrl
  const samples = 800 // 0.1 s at 8 kHz, 8-bit mono
  const buffer = new ArrayBuffer(44 + samples)
  const view = new DataView(buffer)
  const text = (offset: number, value: string) => [...value].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)))
  text(0, 'RIFF')
  view.setUint32(4, 36 + samples, true)
  text(8, 'WAVEfmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, 8000, true)
  view.setUint32(28, 8000, true)
  view.setUint16(32, 1, true)
  view.setUint16(34, 8, true)
  text(36, 'data')
  view.setUint32(40, samples, true)
  new Uint8Array(buffer, 44).fill(128)
  silentUrl = URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }))
  return silentUrl
}

export function musicElement(): HTMLAudioElement {
  if (!soundSubscribed) {
    soundSubscribed = true
    onSoundChange((on) => {
      if (!on) stopMusic()
    })
  }
  if (!element) {
    element = new Audio()
    element.crossOrigin = 'anonymous' // Web Audio only hears a clip from another site when it is fetched with CORS
    element.preload = 'auto'
    try {
      const context = audioContext()
      clipGain = context.createGain()
      context.createMediaElementSource(element).connect(clipGain).connect(context.destination)
    } catch {
      clipGain = null
    }
  }
  return element
}

function level(audio: HTMLAudioElement): number {
  return clipGain ? clipLevel : audio.volume
}

function setLevel(audio: HTMLAudioElement, value: number) {
  if (!clipGain) {
    audio.volume = value
    return
  }
  clipLevel = value
  const now = clipGain.context.currentTime
  clipGain.gain.cancelScheduledValues(now)
  clipGain.gain.setTargetAtTime(value, now, 0.015)
}

function unlock(audio: HTMLAudioElement) {
  if (audio.dataset.unlocked || audio.dataset.clip) return
  const epoch = ++playEpoch
  audio.muted = true
  audio.src = silence()
  audio
    .play()
    .then(() => {
      if (epoch === playEpoch && !audio.dataset.clip) audio.pause()
      audio.dataset.unlocked = 'true'
    })
    .catch(() => undefined)
    .finally(() => {
      audio.muted = false
    })
}

/** Call from a tap handler (e.g. the Start button): the clip player becomes free to start by itself. */
export function unlockMusic() {
  unlock(musicElement())
}

// --------------------------------------------------------------------------- riding the volume
// The host and the music never compete. The host introduces a music break *before* its clip
// starts, so a clip normally swells straight up to full. If he speaks while one is playing – his
// reaction to the answer, or a reply to the player – the clip dips to a bed under him as his voice
// starts (not while his line is still being written: the song is the answer's moment) and climbs
// slowly back once he has finished. Moving on fades it out rather than cutting it dead.

const FULL = 0.9
const BED = 0.14
const SWELL_PER_SECOND = 1.2 // a new clip: silence to full in under a second
const RETURN_PER_SECOND = 0.5 // after the host: the slow rise back up
const FALL_PER_SECOND = 6 // dip almost at once when the host starts

let ducked = false
let fadingOut = false
let fadePerSecond = 3
let risePerSecond = SWELL_PER_SECOND
let heldForHost = false // fixed-volume devices: paused (or not yet started) because the host is talking
let timer = 0
let lastTick = 0
let adjustable: boolean | null = null

// The jukebox (the music behind the questions) follows the same signal as the clip above: it dips only
// when a voice is actually sounding - his, or the player's on the microphone.

let voiceOver = false
const voiceListeners = new Set<() => void>()

/** A voice has started or stopped sounding over the music. */
export function setVoiceOver(on: boolean) {
  if (voiceOver === on) return
  voiceOver = on
  voiceListeners.forEach((listener) => listener())
}

export function voiceIsOver(): boolean {
  return voiceOver
}

export function onVoiceOver(listener: () => void): () => void {
  voiceListeners.add(listener)
  return () => voiceListeners.delete(listener)
}

/** Whether the clip's level can be moved: always through the mixer; without it, not on an iPhone or iPad. */
export function volumeAdjustable(audio: HTMLAudioElement): boolean {
  if (adjustable === null) {
    // (iOS reports a new volume back without applying it, so asking the element is not enough.)
    const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
    const before = audio.volume
    audio.volume = 0.5
    adjustable = Boolean(clipGain) || (!apple && Math.abs(audio.volume - 0.5) < 0.01)
    audio.volume = before
  }
  return adjustable
}

// The volume is moved by a timer, not by animation frames: frames stop when the page is not being
// drawn (a background tab, a covered window) while the music plays on, and a fade must still finish.
function ride() {
  const audio = element
  if (!audio || audio.paused || !adjustable) {
    stopRiding()
    return
  }
  const now = performance.now()
  const elapsed = Math.min(0.25, lastTick ? (now - lastTick) / 1000 : 0.03)
  lastTick = now
  const goal = fadingOut ? 0 : ducked ? BED : FULL
  const gap = goal - level(audio)
  if (Math.abs(gap) < 0.004) {
    setLevel(audio, goal)
    stopRiding()
    if (fadingOut) stopMusic()
    return
  }
  const rate = fadingOut ? fadePerSecond : gap < 0 ? FALL_PER_SECOND : risePerSecond
  setLevel(audio, Math.max(0, Math.min(1, level(audio) + Math.sign(gap) * Math.min(Math.abs(gap), rate * elapsed))))
}

function startRiding() {
  if (timer) return
  lastTick = 0
  timer = window.setInterval(ride, 30)
}

function stopRiding() {
  window.clearInterval(timer)
  timer = 0
  lastTick = 0
}

/** The host (or the player, on the microphone) has started or stopped talking. */
export function duckMusic(on: boolean) {
  if (ducked === on) return
  ducked = on
  const audio = element
  if (!audio) return
  if (adjustable === false) {
    // No volume control on this device: the clip steps aside completely while the host talks.
    if (on && !audio.paused) {
      audio.pause()
      heldForHost = true
    } else if (!on && heldForHost) {
      heldForHost = false
      void audio.play().catch(() => undefined)
    }
    return
  }
  if (!on) risePerSecond = RETURN_PER_SECOND
  startRiding()
}

function setClip(audio: HTMLAudioElement, src: string): boolean {
  fadingOut = false
  heldForHost = false
  if (audio.dataset.clip === src) return false
  audio.pause()
  audio.src = src
  audio.dataset.clip = src
  return true
}

/** Start fetching a clip while its round is being introduced, so it can begin the moment it is wanted. */
export function preloadClip(src: string) {
  const audio = musicElement()
  if (setClip(audio, src)) audio.load()
}

/** Play a music clip, swelling in rather than slamming in. Rejects if the browser wants a tap first. */
export async function playClip(src: string): Promise<void> {
  if (!soundOn()) {
    stopMusic()
    return
  }
  playEpoch += 1
  const audio = musicElement()
  audio.muted = false
  setClip(audio, src)
  try {
    audio.currentTime = 0
  } catch {
    /* not seekable yet */
  }
  trace('clip.play', { ducked })
  // The clip is heard through the mixer, so it needs the mixer awake (a phone may have put it to sleep).
  if (clipGain && clipGain.context.state !== 'running') void (clipGain.context as AudioContext).resume().catch(() => undefined)
  if (volumeAdjustable(audio)) {
    setLevel(audio, 0.05)
    risePerSecond = SWELL_PER_SECOND
    await audio.play()
    startRiding()
    return
  }
  if (ducked) {
    heldForHost = true // starts as soon as the host has finished his line
    return
  }
  await audio.play()
}

export function resumeClip(): Promise<void> {
  if (!soundOn()) return Promise.resolve()
  const audio = musicElement()
  if (clipGain && clipGain.context.state !== 'running') void (clipGain.context as AudioContext).resume().catch(() => undefined)
  heldForHost = false
  startRiding()
  return audio.play()
}

/** True while a clip is on its way out; whoever asked for the fade also owns the stop. */
export function musicFading(): boolean {
  return fadingOut
}

/** Bring the clip down and stop it (the player is moving on). */
export function fadeOutMusic(ms = 350) {
  const audio = element
  if (!audio || !audio.dataset.clip) return
  if (audio.paused || !adjustable) {
    stopMusic()
    return
  }
  fadingOut = true
  fadePerSecond = Math.max(level(audio), 0.1) / (ms / 1000)
  startRiding()
}

export function stopMusic() {
  fadingOut = false
  heldForHost = false
  stopRiding()
  if (!element) return
  element.pause()
  if (element.dataset.clip) trace('clip.stop')
  delete element.dataset.clip
  element.removeAttribute('src')
  element.load()
}
