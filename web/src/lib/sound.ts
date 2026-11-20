// Sound effects, the shared "is sound on?" preference, and the audio contexts everything plays through.
//
// Effects go through Web Audio rather than <audio> elements for three reasons: they start on
// the instant, their volume can be set on every device (an iPhone ignores an element's volume),
// and they can drop to a bed the moment the host starts speaking instead of fighting him.

import { trace } from './trace'

export type Sfx = 'correct' | 'wrong' | 'cheer' | 'gameover' | 'hint' | 'levelup' | 'tick' | 'rimshot' | 'click' | 'fasttick'

// The level-up sting is a quiet recording (it peaks at -16 dBFS where the others reach -6 to -1): it is played louder
// so that a round's title card opens on a sting you can hear. "Correct" is a little quieter than "wrong" as recorded.
const VOLUME: Record<Sfx, number> = {
  correct: 0.7, wrong: 0.5, cheer: 0.5, gameover: 0.5, hint: 0.45, levelup: 2,
  tick: 0.35, rimshot: 0.5, click: 0.4, fasttick: 0.3,
}

/** Some recordings open with silence; start them where the sound is. */
const LEAD_IN_SEC: Partial<Record<Sfx, number>> = { cheer: 1.0, rimshot: 0.1, tick: 0.08 }

/** How loud effects stay while the host is speaking (applause becomes a bed under him). */
const UNDER_HOST = 0.2

const KEY = 'quiz.sound'
const listeners = new Set<(on: boolean) => void>()

let enabled = (() => {
  try {
    return localStorage.getItem(KEY) !== 'off'
  } catch {
    return true
  }
})()

export function soundOn(): boolean {
  return enabled
}

export function setSoundOn(on: boolean) {
  enabled = on
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off')
  } catch {
    /* ignore */
  }
  if (!on) playing.forEach((source) => stop(source))
  listeners.forEach((listener) => listener(on))
}

export function onSoundChange(listener: (on: boolean) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

// --------------------------------------------------------------------------- audio contexts

type ContextCtor = typeof AudioContext

function contextCtor(): ContextCtor {
  return window.AudioContext ?? (window as unknown as { webkitAudioContext: ContextCtor }).webkitAudioContext
}

let context: AudioContext | null = null

/** The AudioContext sound effects play through. Must first be touched inside a tap handler. */
export function audioContext(): AudioContext {
  if (!context) context = new (contextCtor())()
  return context
}

let voiceContext: AudioContext | null = null

/**
 * A second AudioContext running at the host voice's own sample rate (24 kHz).
 *
 * Speech arrives as many small chunks. In an ordinary 44.1/48 kHz context the browser would
 * resample every chunk separately, which leaves a tiny discontinuity at each join and makes the
 * voice sound rough. At the native rate the chunks butt together sample-for-sample.
 */
export function hostAudioContext(sampleRate = 24000): AudioContext {
  if (!voiceContext || voiceContext.state === 'closed') {
    try {
      voiceContext = new (contextCtor())({ sampleRate, latencyHint: 'interactive' })
    } catch {
      voiceContext = new (contextCtor())() // very old browsers: fall back to the device rate
    }
  }
  return voiceContext
}

type AudioSession = { type: string }

/**
 * Tell the phone what kind of sound this is. On an iPhone, Web Audio is otherwise silenced by the
 * ring/silent switch while <audio> elements are not – the music would play and the host would not.
 */
export function audioMode(mode: 'playback' | 'talk') {
  const session = (navigator as Navigator & { audioSession?: AudioSession }).audioSession
  if (!session) return
  try {
    session.type = mode === 'talk' ? 'play-and-record' : 'playback'
  } catch {
    /* not supported here */
  }
}

/** Call from a tap/click so the browser allows audio for the rest of the session. */
export function unlockAudio() {
  audioMode('playback')
  for (const ctx of [audioContext(), hostAudioContext()]) {
    // Not only 'suspended': Safari also has 'interrupted' (another app or tab took the sound), which only a tap undoes.
    if (ctx.state !== 'running' && ctx.state !== 'closed') void ctx.resume().catch(() => undefined)
  }
  preloadSounds()
  keepAwake()
}

let watching = false

/**
 * Phones put audio to sleep when a call comes in or the page is left for a while. Once the game
 * has sound, the next tap anywhere (or coming back to the page) wakes it again – the player
 * never has to find a button for it.
 */
function keepAwake() {
  if (watching) return
  watching = true
  const wake = () => {
    for (const ctx of [context, voiceContext]) {
      if (ctx && ctx.state !== 'running' && ctx.state !== 'closed') void ctx.resume().catch(() => undefined)
    }
  }
  window.addEventListener('pointerdown', wake, { passive: true, capture: true })
  document.addEventListener('visibilitychange', () => !document.hidden && wake())
}

// --------------------------------------------------------------------------- sound effects

const buffers = new Map<Sfx, AudioBuffer>()
const loading = new Map<Sfx, Promise<AudioBuffer | null>>()
const playing = new Map<Sfx, AudioBufferSourceNode>()
let bus: GainNode | null = null
let ducked = false

function effectsBus(ctx: AudioContext): GainNode {
  if (!bus) {
    bus = ctx.createGain()
    bus.gain.value = ducked ? UNDER_HOST : 1
    bus.connect(ctx.destination)
  }
  return bus
}

function load(name: Sfx): Promise<AudioBuffer | null> {
  let pending = loading.get(name)
  if (!pending) {
    pending = fetch(`/sfx/${name}.mp3`)
      .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error('missing'))))
      .then((data) => audioContext().decodeAudioData(data))
      .then((buffer) => {
        buffers.set(name, buffer)
        return buffer
      })
      .catch(() => {
        loading.delete(name) // try again next time
        return null
      })
    loading.set(name, pending)
  }
  return pending
}

/** Fetch and decode every effect ahead of time, so the first "correct!" is as prompt as the tenth. */
export function preloadSounds() {
  ;(Object.keys(VOLUME) as Sfx[]).forEach((name) => void load(name))
}

function stop(source: AudioBufferSourceNode) {
  try {
    source.stop()
  } catch {
    /* already stopped */
  }
}

function start(ctx: AudioContext, name: Sfx, buffer: AudioBuffer) {
  const previous = playing.get(name)
  if (previous) stop(previous) // the same effect never piles up on itself
  const source = ctx.createBufferSource()
  source.buffer = buffer
  const gain = ctx.createGain()
  gain.gain.value = VOLUME[name]
  source.connect(gain)
  gain.connect(effectsBus(ctx))
  source.onended = () => {
    if (playing.get(name) === source) playing.delete(name)
  }
  playing.set(name, source)
  source.start(0, LEAD_IN_SEC[name] ?? 0)
  trace('sfx', { name })
}

export function play(name: Sfx) {
  if (!enabled) return
  const ctx = audioContext()
  const asked = performance.now()
  const go = (buffer: AudioBuffer | null) => {
    // An effect that could not start promptly (audio still locked, file still loading) is stale: skip it.
    if (buffer && enabled && ctx.state === 'running' && performance.now() - asked < 400) start(ctx, name, buffer)
  }
  const ready = ctx.state === 'running' ? Promise.resolve() : ctx.resume().catch(() => undefined) // browsers refuse sound before the first tap; that is fine
  const buffer = buffers.get(name)
  void ready.then(() => (buffer ? go(buffer) : load(name).then(go)))
}

// --------------------------------------------------------------------------- arcade blips
// Tiny synthesised sounds for the moments between the big ones – a question arriving, a streak
// building. They are made on the spot rather than loaded, so they are never late, and they go
// through the same bus as the recorded effects, so they too drop under the host.

export type Blip = 'question' | 'streak' | 'bigstreak'

const BLIPS: Record<Blip, { notes: number[]; stepSec: number; lengthSec: number; volume: number }> = {
  question: { notes: [523.25, 783.99], stepSec: 0.07, lengthSec: 0.09, volume: 0.1 }, // C5 G5: "here it comes"
  streak: { notes: [523.25, 659.25, 783.99, 1046.5], stepSec: 0.075, lengthSec: 0.12, volume: 0.12 }, // a rising chord
  bigstreak: { notes: [523.25, 659.25, 783.99, 1046.5, 1318.51, 1567.98], stepSec: 0.07, lengthSec: 0.15, volume: 0.13 },
}

export function blip(name: Blip) {
  if (!enabled) return
  const ctx = audioContext()
  if (ctx.state !== 'running') return // never queue a sound for later: a late blip is worse than none
  const { notes, stepSec, lengthSec, volume } = BLIPS[name]
  const start = ctx.currentTime + 0.01
  notes.forEach((frequency, i) => {
    const at = start + i * stepSec
    const tone = ctx.createOscillator()
    tone.type = 'square'
    tone.frequency.value = frequency
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0, at)
    gain.gain.linearRampToValueAtTime(volume, at + 0.008)
    gain.gain.exponentialRampToValueAtTime(0.0001, at + lengthSec)
    tone.connect(gain)
    gain.connect(effectsBus(ctx))
    tone.onended = () => gain.disconnect()
    tone.start(at)
    tone.stop(at + lengthSec + 0.02)
  })
  trace('sfx', { name })
}

/** The host has started (or stopped) speaking: effects drop to a bed, then come back (at once if he was cut off). */
export function duckEffects(on: boolean, quick = false) {
  ducked = on
  if (!context || !bus) return
  const now = context.currentTime
  bus.gain.cancelScheduledValues(now)
  bus.gain.setTargetAtTime(on ? UNDER_HOST : 1, now, on ? 0.04 : quick ? 0.03 : 0.25)
}

/** A short buzz on phones that support it. */
export function buzz(pattern: number | number[]) {
  try {
    navigator.vibrate?.(pattern)
  } catch {
    /* ignore */
  }
}
