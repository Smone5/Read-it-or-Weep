// The talking host: one WebSocket for the whole game.
//
// Out: tiny cues ("result for item X") and, while the player holds the talk button, their
// microphone. In: the host's speech as raw 24 kHz audio plus the words as they are spoken.
// Audio chunks are scheduled back-to-back on the Web Audio clock, so playback starts with the
// first chunk (about half a second after the cue) instead of waiting for a finished recording.
//
// Sequencing. Every line asked for gets a number. That lets the game do two things it needs to
// stay smooth: wait until the host has finished (`whenQuiet`) before putting a question on
// screen, and stop him cleanly (`skip`) the moment the player moves on – the line fades out,
// anything still on its way is discarded, and the server is told not to bother with the rest.

import { Mic, micSupported } from './mic'
import { duckMusic, setVoiceOver } from './music'
import { audioMode, blip, duckEffects, hostAudioContext, onSoundChange, soundOn } from './sound'
import { trace } from './trace'

export type TalkState = 'idle' | 'listening' | 'thinking'

export type HostListener = {
  /** The line being spoken so far ('' when the host has nothing on screen). */
  onLine(text: string): void
  onSpeaking(speaking: boolean): void
  onStatus?(status: 'connecting' | 'ready' | 'offline'): void
  /** Hold-to-talk became available / changed state. */
  onTalk?(state: TalkState, available: boolean): void
  onTalkProblem?(message: string): void
}

type Target = { runId: string; token: string; hostId?: string | null } | { liveCode: string; hostKey: string }

/** One line of the host's, from its first piece arriving until its last sound has played. */
type Line = {
  id: number
  text: string
  gain: GainNode | null
  sources: Set<AudioBufferSourceNode>
  nextStart: number
  /** The server has sent all of it. */
  complete: boolean
  /** Some of it was actually played out loud (or is scheduled to be). */
  heard: boolean
  /** His voice has actually started: the words appear, and the music is down, from this moment. */
  live: boolean
  /** The opening only: his voice is about to start, and the music has begun its glide down to make room. */
  leadIn: boolean
  leadTimer?: number
  /** Not to be heard before this time (ms since 1970): the show's opening lets the music play first. */
  notBefore?: number
  speakTimer?: number
  /** No longer wanted: ignore whatever else arrives for it. */
  dropped: boolean
  readTimer?: number
  /** For the timing trace: seconds of speech received, and how often playback ran dry waiting for more. */
  seconds: number
  gaps: number
  /** Pieces that arrived while the phone had his audio asleep: played as soon as it wakes. */
  waiting?: ArrayBuffer[]
  /** Decoded buffers queued during a future hold so holdUntil(0) can release them immediately. */
  heldBuffers?: { buffer: AudioBuffer; envelope: Float32Array }[]
  /** Scheduled PCM amplitude envelopes belonging to this line (removed if the line is cut off). */
  envelopes?: ScheduledEnvelope[]
  /** Progressive caption reveal timer for ElevenLabs full-line text chunks. */
  revealTimer?: number
}

type Incoming = { type: string; id?: number; cue?: string; delta?: string; voice?: boolean; talk?: boolean; sampleRate?: number }

const START_TIMEOUT_MS = 9000 // generous enough for cold LLM + ElevenLabs synthesis without false revivals
const CONNECT_TIMEOUT_MS = 10000 // ...with more patience for the very first line, while the host is still arriving
const FADE_SEC = 0.12 // cutting the host off: a quick fade, not a click
// The opening's handover from the theme to the host. The music starts down LEAD_IN_SEC before his first word, so it
// is nearly out of the way when he speaks, and his voice comes up from about half volume: a crossfade you can hear,
// without a first word buried under the music. (Reviewed 2026-11-20: starting his voice at 15% while the music was
// still coming down left his first two words about 12 dB under it.)
const LEAD_IN_SEC = 1.2
const VOICE_FADE_FROM = 0.45
const VOICE_FADE_SEC = 0.7
// His voice goes through a gentle leveller, so a quiet aside (often the punchline) is not lost under the music: it
// lifts the quiet beats about 4 dB and leaves the loud ones where they were (the trim takes back the make-up gain
// a compressor adds by itself). A phone's small speaker gets him a little louder still.
const TOUCH = typeof window !== 'undefined' && window.matchMedia?.('(hover: none) and (pointer: coarse)').matches
const LEVELLER_TRIM = TOUCH ? 1 : 0.7
const voiceBuses = new WeakMap<AudioContext, AudioNode>()
let activeAnalyser: AnalyserNode | null = null
let analyserData: Uint8Array<ArrayBuffer> | null = null

const ENVELOPE_BIN_SEC = 0.025 // 40 Hz RMS envelope computed directly from PCM samples (immune to iOS WebKit AnalyserNode quirks)
type ScheduledEnvelope = { context: AudioContext; startAt: number; endAt: number; bins: Float32Array }
const activeEnvelopes = new Set<ScheduledEnvelope>()

function computeEnvelope(samples: Int16Array, sampleRate: number): Float32Array {
  const step = Math.max(1, Math.round(sampleRate * ENVELOPE_BIN_SEC))
  const count = Math.max(1, Math.ceil(samples.length / step))
  const bins = new Float32Array(count)
  for (let b = 0; b < count; b += 1) {
    const start = b * step
    const end = Math.min(samples.length, start + step)
    let sum = 0
    for (let i = start; i < end; i += 1) {
      const v = samples[i] / 32768
      sum += v * v
    }
    const rms = Math.sqrt(sum / Math.max(1, end - start))
    bins[b] = rms < 0.006 ? 0 : Math.min(1, rms * 3.8)
  }
  return bins
}

/** Live 0..1 amplitude of the host's voice for animating the CRT vocal meter. */
export function getHostVoiceLevel(): number {
  let pcmLevel = 0
  for (const entry of activeEnvelopes) {
    if (entry.context.state === 'closed') {
      activeEnvelopes.delete(entry)
      continue
    }
    const now = entry.context.currentTime
    if (now > entry.endAt + 0.15) {
      activeEnvelopes.delete(entry)
      continue
    }
    if (entry.context.state === 'running' && now >= entry.startAt && now <= entry.endAt) {
      const idx = Math.min(entry.bins.length - 1, Math.max(0, Math.floor((now - entry.startAt) / ENVELOPE_BIN_SEC)))
      if (entry.bins[idx] > pcmLevel) pcmLevel = entry.bins[idx]
    }
  }

  let analyserLevel = 0
  if (activeAnalyser && analyserData) {
    activeAnalyser.getByteTimeDomainData(analyserData)
    let sum = 0
    for (let i = 0; i < analyserData.length; i += 1) {
      const v = (analyserData[i] - 128) / 128
      sum += v * v
    }
    const rms = Math.sqrt(sum / analyserData.length)
    analyserLevel = rms < 0.006 ? 0 : Math.min(1, rms * 4.2)
  }

  return Math.max(pcmLevel, analyserLevel)
}

function voiceBus(context: AudioContext): AudioNode {
  const known = voiceBuses.get(context)
  if (known) return known
  const lowCut = context.createBiquadFilter() // rumble a phone speaker cannot play anyway
  lowCut.type = 'highpass'
  lowCut.frequency.value = 90
  const leveller = context.createDynamicsCompressor()
  leveller.threshold.value = -24
  leveller.knee.value = 8
  leveller.ratio.value = 2
  leveller.attack.value = 0.005
  leveller.release.value = 0.2
  const trim = context.createGain()
  trim.gain.value = LEVELLER_TRIM
  lowCut.connect(leveller)
  leveller.connect(trim)
  trim.connect(context.destination)
  if (typeof context.createAnalyser === 'function') {
    const analyser = context.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.65
    trim.connect(analyser) // side-branch tap so AnalyserNode never sits in series with speaker output on iOS
    activeAnalyser = analyser
    analyserData = new Uint8Array(new ArrayBuffer(analyser.fftSize))
  }
  voiceBuses.set(context, lowCut)
  return lowCut
}
// If the connection drops mid-game the host comes back by himself. The game never waits for him
// meanwhile: with no connection there is nobody to ask, so title cards simply move on.
const RECONNECT_WAITS_MS = [1000, 2000, 4000, 8000, 15000]
const STEADY_MS = 20000 // a connection that lasted this long was a good one: start counting failures afresh
const REFUSED = 4401 // the server will not have us (wrong game, signed out): do not keep knocking
// A host who is connected but says nothing is worse than no host: every title card would wait for
// him. Two missed lines in a row and his connection is replaced; if that keeps happening he is
// left out of the rest of the game, which carries on without waiting for anyone.
const MISSES_BEFORE_REVIVAL = 2
const REVIVALS_BEFORE_REST = 2

export class HostVoice {
  private socket: WebSocket | null = null
  private listener: HostListener
  private target: Target
  private sampleRate = 24000
  private voice = false
  private issued = 0 // numbers handed out to requested lines
  private voidThrough = 0 // every line up to this number is no longer wanted
  private abandoned = new Set<number>() // single lines given up on
  private awaited = new Map<number, number>() // asked for, not started yet -> its give-up timer
  private queue: { id: number; text: string }[] = [] // asked for while still connecting
  private holds = new Map<number, number>() // line id -> not to be heard before this time (see cue)
  private arriving: Line | null = null // the line whose pieces are coming in now
  private sounding = new Set<Line>() // lines that have started and not finished playing
  private quiet: (() => void)[] = []
  private clearTimer: number | undefined
  private closed = false
  private ready = false
  private readyAt = 0
  private failures = 0
  private reconnectTimer: number | undefined
  private misses = 0 // lines in a row that never started
  private revivals = 0 // connections replaced because of that
  private resting = false // given up on for this game
  private mic: Mic | null = null
  private talking = false
  private replyId = 0
  private waking = false
  private stopSoundListener: (() => void) | null = null
  private serverVoiceSupported = false
  private serverTalkSupported = false
  private onVisibility = () => {
    // Switching to another tab (e.g. an external YouTube clip) should immediately hush the host.
    if (document.hidden && this.busy) this.skip()
  }
  canTalk = false

  constructor(target: Target, listener: HostListener) {
    this.target = target
    this.listener = listener
  }

  connect() {
    if (this.closed) return
    document.removeEventListener('visibilitychange', this.onVisibility)
    document.addEventListener('visibilitychange', this.onVisibility)
    this.stopSoundListener?.()
    this.stopSoundListener = onSoundChange((on) => {
      if (this.closed) return
      this.voice = on && this.serverVoiceSupported
      this.canTalk = on && this.serverTalkSupported && micSupported()
      this.listener.onTalk?.('idle', this.canTalk)
      this.send({ type: 'voice', enabled: on })
      if (!on) {
        // Immediately silence active/scheduled audio buffers while letting the game continue smoothly.
        this.silence()
      } else {
        const ctx = hostAudioContext(this.sampleRate)
        if (ctx.state !== 'running' && ctx.state !== 'closed') void ctx.resume().catch(() => undefined)
      }
    })
    this.ready = false
    this.listener.onStatus?.('connecting')
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    // Who we are goes in the first message, not in the address: addresses are written to server logs.
    const hello =
      'runId' in this.target
        ? { type: 'hello', run: this.target.runId, token: this.target.token, hostId: this.target.hostId ?? undefined, voice: soundOn() }
        : { type: 'hello', liveCode: this.target.liveCode, key: this.target.hostKey, voice: soundOn() }
    const socket = new WebSocket(`${scheme}://${location.host}/api/host/ws`)
    socket.binaryType = 'arraybuffer'
    this.socket = socket
    socket.onopen = () => {
      socket.send(JSON.stringify(hello))
      this.queue.splice(0).forEach((queued) => this.wanted(queued.id) && socket.send(queued.text))
    }
    socket.onmessage = (event) => {
      if (typeof event.data === 'string') this.onMessage(JSON.parse(event.data) as Incoming)
      else this.onAudio(event.data as ArrayBuffer)
    }
    socket.onclose = (event) => {
      if (this.socket !== socket) return // an old connection finishing after a newer one took over
      this.socket = null
      this.canTalk = false
      if (!this.closed) this.listener.onStatus?.('offline')
      this.listener.onTalk?.('idle', false)
      if (this.talking) {
        this.talking = false
        void this.mic?.stop()
        this.mic = null
        audioMode('playback')
      }
      this.forgetRequests()
      this.silence()
      if (!this.closed && !this.resting && event.code !== REFUSED) this.comeBack()
    }
    socket.onerror = () => socket.close()
  }

  /** True while the host has the floor: a line is on its way or being said, or the player is talking to him. */
  get busy(): boolean {
    return this.talking || this.awaited.size > 0 || this.sounding.size > 0
  }

  /** True when a line asked for now could be heard: connected, with his voice and the game's sound on. */
  get canBeHeard(): boolean {
    return this.ready && !this.closed && !this.resting && this.socket?.readyState === WebSocket.OPEN && this.audible()
  }

  /** True while a line has been asked for but nothing of it has been heard (or shown) yet. */
  get stalled(): boolean {
    if (this.talking) return false
    if (this.awaited.size > 0) return true
    for (const line of this.sounding) if (!line.heard && !line.text) return true
    return false
  }

  /** Resolves once the host has nothing left to say (at once, if he was not going to say anything). */
  whenQuiet(): Promise<void> {
    if (!this.busy) return Promise.resolve()
    return new Promise((resolve) => this.quiet.push(resolve))
  }

  /**
   * Ask the host to react. Only ids are sent; the server looks up what actually happened. With `notBefore`
   * the line is written at once but not heard before that moment (the opening: the music plays first).
   */
  cue(cue: string, itemId?: string, options: { notBefore?: number } = {}): number {
    const id = this.request({ type: 'cue', cue, itemId }, cue)
    if (id && options.notBefore) this.holds.set(id, options.notBefore)
    return id
  }

  /**
   * Move a held line's moment (see `cue`): later, when the opening music started late; or 0 to let it go as soon as
   * it is ready, when there is no music to wait for. It only counts until the line's first sound has been scheduled.
   */
  holdUntil(at: number) {
    for (const id of this.holds.keys()) this.holds.set(id, at)
    const line = this.arriving
    if (line && !line.live && line.notBefore) {
      line.notBefore = at
      // If buffers were already scheduled far in the future and music was cancelled (at === 0), reschedule right away!
      if (at === 0 && line.heard && line.heldBuffers?.length) {
        window.clearTimeout(line.leadTimer)
        window.clearTimeout(line.speakTimer)
        line.sources.forEach((source) => {
          source.onended = null
          try {
            source.stop()
          } catch {
            /* already stopped */
          }
        })
        line.sources.clear()
        line.envelopes?.forEach((env) => activeEnvelopes.delete(env))
        line.envelopes = undefined
        const buffers = line.heldBuffers.splice(0)
        line.nextStart = 0
        line.heard = false
        line.seconds = 0
        const context = hostAudioContext(this.sampleRate)
        if (context.state === 'running') {
          buffers.forEach(({ buffer, envelope }) => this.scheduleBuffer(line, buffer, context, envelope))
        }
      }
    }
    trace('host.hold', at ? { inMs: Math.round(at - Date.now()) } : { released: true })
  }

  /** The game stopped waiting for a slow line. Skip just this turn without killing the connection or wiping history. */
  tooLate() {
    this.skip()
  }

  private missed() {
    this.misses += 1
    if (this.misses < MISSES_BEFORE_REVIVAL || this.closed || this.resting) return
    this.misses = 0
    if (this.revivals >= REVIVALS_BEFORE_REST) {
      this.resting = true
      trace('host.resting')
      this.socket?.close()
      return
    }
    this.revivals += 1
    trace('host.revive', { attempt: this.revivals })
    this.socket?.close() // closing brings a fresh connection (see comeBack)
  }

  /** Stop the host now and drop anything he was about to say (the player has moved on). */
  skip() {
    if (this.issued > this.voidThrough) {
      this.voidThrough = this.issued
      this.send({ type: 'cancel', through: this.issued })
      trace('host.skip', { through: this.issued })
    }
    this.forgetRequests()
    this.silence()
  }

  /** The player pressed the talk button: open the microphone and start sending it. */
  async startTalking() {
    if (!this.canTalk || this.talking || this.socket?.readyState !== WebSocket.OPEN) return
    this.talking = true // the player has the floor
    this.skip()
    audioMode('talk')
    this.mix()
    this.send({ type: 'talk.start' })
    this.listener.onTalk?.('thinking', true) // show connecting state until mic stream is actually live
    const mic = new Mic()
    this.mic = mic
    try {
      await mic.start((chunk) => {
        if (this.talking && this.socket?.readyState === WebSocket.OPEN) this.socket.send(chunk)
      })
      if (!this.talking) {
        await mic.stop() // they let go before the microphone was ready
        return
      }
      blip('mic')
      this.listener.onTalk?.('listening', true)
    } catch (error) {
      this.talking = false
      this.mic = null
      audioMode('playback')
      this.send({ type: 'talk.cancel' })
      this.listener.onTalk?.('idle', true)
      const denied = (error as Error)?.name === 'NotAllowedError'
      this.listener.onTalkProblem?.(
        denied
          ? 'The microphone is blocked here. Allow it for this site – or, inside another app, open the game in Chrome or Safari.'
          : 'No microphone could be found on this device.',
      )
      this.settled()
    }
  }

  /** The player let go: close the microphone and ask for a reply. */
  async stopTalking() {
    if (!this.talking) return
    const mic = this.mic
    this.mic = null
    await mic?.stop()
    this.talking = false
    audioMode('playback')
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.settled()
      return
    }
    this.replyId = this.request({ type: 'talk.end' }, 'talk')
    this.listener.onTalk?.('thinking', true)
    this.mix() // the microphone is closed: the soundtrack may come back up while he thinks
  }

  /** Try the connection again after a pause that grows with each failure; give up after a handful. */
  private comeBack() {
    if (this.readyAt && Date.now() - this.readyAt > STEADY_MS) this.failures = 0
    this.readyAt = 0
    if (this.failures >= RECONNECT_WAITS_MS.length) return
    const wait = RECONNECT_WAITS_MS[this.failures]
    this.failures += 1
    trace('host.reconnect', { attempt: this.failures, inMs: wait })
    window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = window.setTimeout(() => this.connect(), wait)
  }

  close() {
    this.closed = true
    document.removeEventListener('visibilitychange', this.onVisibility)
    this.stopSoundListener?.()
    this.stopSoundListener = null
    window.clearTimeout(this.reconnectTimer)
    this.talking = false
    void this.mic?.stop()
    this.mic = null
    this.forgetRequests()
    this.silence()
    this.socket?.close()
    this.socket = null
  }

  // ------------------------------------------------------------------------- requests

  private send(message: Record<string, unknown>) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message))
  }

  private wanted(id: number): boolean {
    return id > this.voidThrough && !this.abandoned.has(id)
  }

  /** Ask for a line and start waiting for it. Returns its number (0 when there is no host to ask). */
  private request(message: Record<string, unknown>, label: string): number {
    const socket = this.socket
    if (!socket || this.closed || this.resting || socket.readyState > WebSocket.OPEN) return 0
    const id = ++this.issued
    const text = JSON.stringify({ ...message, id })
    if (socket.readyState === WebSocket.OPEN) socket.send(text)
    else this.queue.push({ id, text })
    this.awaited.set(id, window.setTimeout(() => this.giveUp(id), this.ready ? START_TIMEOUT_MS : CONNECT_TIMEOUT_MS))
    trace('host.ask', { id, cue: label })
    this.mix()
    return id
  }

  /** The line never started: stop waiting, and make sure it cannot turn up late over a question. */
  private giveUp(id: number) {
    if (!this.awaited.delete(id)) return
    this.abandoned.add(id)
    this.send({ type: 'cancel', only: id })
    trace('host.gaveup', { id })
    if (id === this.replyId) this.listener.onTalk?.('idle', this.canTalk)
    this.settled()
    this.missed()
  }

  private noLongerAwaited(id: number) {
    const timer = this.awaited.get(id)
    if (timer === undefined) return
    window.clearTimeout(timer)
    this.awaited.delete(id)
  }

  private forgetRequests() {
    this.awaited.forEach((timer) => window.clearTimeout(timer))
    this.awaited.clear()
    this.queue = []
  }

  // ------------------------------------------------------------------------- incoming

  private onMessage(message: Incoming) {
    const id = message.id ?? 0
    if (message.type === 'ready') {
      this.ready = true
      this.readyAt = Date.now()
      this.serverVoiceSupported = Boolean(message.voice) || !soundOn()
      this.serverTalkSupported = Boolean(message.talk) || (!soundOn() && 'runId' in this.target)
      this.voice = Boolean(message.voice) && soundOn()
      this.sampleRate = message.sampleRate ?? 24000
      this.canTalk = Boolean(message.talk) && soundOn() && micSupported()
      this.listener.onStatus?.('ready')
      this.listener.onTalk?.('idle', this.canTalk)
    } else if (message.type === 'line.start') {
      this.noLongerAwaited(id)
      if (id === this.replyId) this.listener.onTalk?.('idle', this.canTalk)
      const line: Line = { id, text: '', gain: null, sources: new Set(), nextStart: 0, complete: false, heard: false, live: false, leadIn: false, notBefore: this.holds.get(id), dropped: !this.wanted(id) || this.talking, seconds: 0, gaps: 0 }
      this.holds.delete(id)
      this.arriving = line
      trace('host.start', { id, cue: message.cue, dropped: line.dropped })
      if (!line.dropped) {
        this.misses = 0 // he is with us
        this.revivals = 0
      }
      if (line.dropped) {
        this.settled()
        return
      }
      this.sounding.add(line)
      window.clearTimeout(this.clearTimer)
      this.setLine('')
      if (!this.audible()) this.listener.onSpeaking(true) // subtitles only
      this.mix()
    } else if (message.type === 'line.text') {
      const line = this.arriving
      if (line && !line.dropped && line.id === id) {
        line.text += message.delta ?? ''
        // Hold captions until audio starts playing so punchlines are never spoiled before the host speaks.
        if (line.live || !this.audible()) this.setLine(line.text)
      }
    } else if (message.type === 'line.end') {
      const line = this.arriving
      if (!line || line.id !== id) return
      this.arriving = null
      if (line.dropped) return
      line.complete = true
      if (line.sources.size === 0) {
        if (line.heard) this.finish(line)
        else {
          // Text-only fallback (sound off, subtitles only, or empty-mic notice): reveal text and let it be read.
          if (line.text) this.setLine(line.text)
          line.readTimer = window.setTimeout(() => this.finish(line), Math.min(2400, 600 + line.text.length * 22))
        }
      }
    } else if (message.type === 'line.skip') {
      this.noLongerAwaited(id)
      if (id === this.replyId) this.listener.onTalk?.('idle', this.canTalk)
      this.settled()
    } else if (message.type === 'error') {
      this.listener.onStatus?.('offline')
    }
  }

  private audible(): boolean {
    return this.voice && soundOn() && hostAudioContext(this.sampleRate).state === 'running'
  }

  private onAudio(data: ArrayBuffer) {
    const line = this.arriving
    if (!line || line.dropped || !soundOn() || this.talking) return
    const context = hostAudioContext(this.sampleRate)
    if (context.state !== 'running') {
      // A phone can put his audio to sleep (another sound took over, the screen, a call). His words are kept and his
      // audio asked to wake, so he is heard as soon as it does - rather than lost, as the show's opening was on a
      // phone (2026-11-20). The subtitles show meanwhile, and a tap anywhere wakes it too (sound.ts, keepAwake).
      ;(line.waiting ??= []).push(data)
      this.wake(context)
      return
    }
    this.play(line, data, context)
  }

  private wake(context: AudioContext) {
    if (this.waking) return
    this.waking = true
    trace('host.asleep', { state: context.state })
    void context.resume().catch(() => undefined)
    const onState = () => {
      if (context.state !== 'running') return
      context.removeEventListener('statechange', onState)
      this.waking = false
      trace('host.awake')
      for (const line of this.sounding) {
        const waiting = line.waiting
        line.waiting = undefined
        if (!waiting || line.dropped) continue
        window.clearTimeout(line.readTimer) // it is heard after all: it ends when its sound does
        waiting.forEach((chunk) => this.play(line, chunk, context))
      }
    }
    context.addEventListener('statechange', onState)
  }

  private revealLineText(line: Line) {
    window.clearInterval(line.revealTimer)
    const full = line.text
    const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const words = full.split(/\s+/).filter(Boolean)
    if (reduced || words.length <= 5 || !this.audible()) {
      this.setLine(full)
      return
    }
    // Reveal full-block ElevenLabs captions progressively at natural speech pace (~3.3 words/sec)
    // so punchlines land with the voice instead of spoiling upfront.
    let shown = Math.min(3, words.length)
    this.setLine(words.slice(0, shown).join(' '))
    const msPerWord = Math.max(110, Math.min(260, Math.round(((line.seconds || words.length / 3.4) * 900) / words.length)))
    line.revealTimer = window.setInterval(() => {
      if (line.dropped) {
        window.clearInterval(line.revealTimer)
        return
      }
      shown = Math.min(words.length, shown + 1)
      this.setLine(words.slice(0, shown).join(' '))
      if (shown >= words.length) window.clearInterval(line.revealTimer)
    }, msPerWord)
  }

  private play(line: Line, data: ArrayBuffer, context: AudioContext) {
    const samples = new Int16Array(data, 0, Math.floor(data.byteLength / 2))
    if (!samples.length) return
    const buffer = context.createBuffer(1, samples.length, this.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768
    const envelope = computeEnvelope(samples, this.sampleRate)
    this.scheduleBuffer(line, buffer, context, envelope)
  }

  private scheduleBuffer(line: Line, buffer: AudioBuffer, context: AudioContext, envelope: Float32Array) {
    if (!line.gain) {
      line.gain = context.createGain()
      line.gain.connect(voiceBus(context))
    }
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(line.gain)
    // Start a new line a beat late so brief network hiccups never leave a gap mid-sentence; after that each chunk
    // begins where the previous one ends. A held line (the opening) waits for its moment, and always leaves the
    // music time to get out of the way first.
    const onTime = line.nextStart > context.currentTime
    const held = !line.heard && Boolean(line.notBefore)
    const hold = held ? Math.max(LEAD_IN_SEC, ((line.notBefore ?? 0) - Date.now()) / 1000) : 0
    const startAt = onTime ? line.nextStart : context.currentTime + Math.max(0.12, hold)
    if (!onTime && line.heard) line.gaps += 1
    if (line.notBefore && !line.live) (line.heldBuffers ??= []).push({ buffer, envelope })
    const envEntry: ScheduledEnvelope = { context, startAt, endAt: startAt + buffer.duration, bins: envelope }
    activeEnvelopes.add(envEntry)
    ;(line.envelopes ??= []).push(envEntry)
    source.start(startAt)
    line.nextStart = startAt + buffer.duration
    line.seconds += buffer.duration
    line.sources.add(source)
    if (!line.heard) {
      line.heard = true
      if (held) {
        // The opening fades in over the music rather than starting at full: a handover.
        line.gain.gain.setValueAtTime(VOICE_FADE_FROM, startAt)
        line.gain.gain.linearRampToValueAtTime(1, startAt + VOICE_FADE_SEC)
      }
      const begin = () => {
        if (line.dropped) return
        line.live = true
        line.heldBuffers = undefined
        this.listener.onSpeaking(true)
        duckEffects(true)
        trace('host.audio', { id: line.id })
        this.revealLineText(line)
        this.mix() // his voice is about to sound: the soundtrack dips now, not before
      }
      if (held) {
        const ahead = (startAt - context.currentTime) * 1000
        line.leadTimer = window.setTimeout(() => {
          if (line.dropped) return
          line.leadIn = true
          trace('host.leadin', { id: line.id })
          this.mix() // the music starts its glide down now, so it is out of the way by his first word
        }, Math.max(0, ahead - LEAD_IN_SEC * 1000))
        line.speakTimer = window.setTimeout(begin, Math.max(0, ahead))
      } else begin()
    }
    source.onended = () => {
      line.sources.delete(source)
      if (line.sources.size === 0 && line.complete && !line.dropped) this.finish(line)
    }
  }

  // ------------------------------------------------------------------------- finishing

  /** A line has been said (or read) in full. */
  private finish(line: Line) {
    if (!this.sounding.delete(line)) return
    window.clearTimeout(line.readTimer)
    window.clearTimeout(line.speakTimer)
    window.clearTimeout(line.leadTimer)
    window.clearInterval(line.revealTimer)
    if (line.text) this.setLine(line.text)
    trace('host.done', { id: line.id, words: line.text.split(/\s+/).filter(Boolean).length, seconds: Math.round(line.seconds * 10) / 10, gaps: line.gaps, text: line.text })
    line.envelopes?.forEach((env) => activeEnvelopes.delete(env))
    line.envelopes = undefined
    if (this.sounding.size === 0) {
      this.listener.onSpeaking(false)
      duckEffects(false)
      window.clearTimeout(this.clearTimer)
      if (line.text) {
        // Leave the words up for a moment – longer when they were never heard.
        const linger = line.heard ? 1800 : Math.min(7000, 1500 + line.text.length * 45)
        this.clearTimer = window.setTimeout(() => this.setLine(''), linger)
      }
    }
    this.settled()
  }

  /** Cut off whatever is being said and clear the subtitles. */
  private silence() {
    this.sounding.forEach((line) => this.drop(line))
    this.sounding.clear()
    if (this.arriving) this.arriving.dropped = true
    window.clearTimeout(this.clearTimer)
    this.listener.onSpeaking(false)
    this.setLine('')
    duckEffects(false, true) // he was cut off: whatever plays next (the answer's sting) plays at full at once
    this.settled()
  }

  private drop(line: Line) {
    line.dropped = true
    line.heldBuffers = undefined
    line.envelopes?.forEach((env) => activeEnvelopes.delete(env))
    line.envelopes = undefined
    window.clearTimeout(line.readTimer)
    window.clearTimeout(line.speakTimer)
    window.clearTimeout(line.leadTimer)
    window.clearInterval(line.revealTimer)
    const gain = line.gain
    if (!gain) return
    const now = gain.context.currentTime
    const current = gain.gain.value
    gain.gain.cancelScheduledValues(now)
    gain.gain.setValueAtTime(current, now)
    gain.gain.linearRampToValueAtTime(0, now + FADE_SEC)
    line.sources.forEach((source) => {
      source.onended = null
      try {
        source.stop(now + FADE_SEC + 0.02)
      } catch {
        /* already stopped */
      }
    })
    line.sources.clear()
  }

  /** Something changed: set the music level, and release anyone waiting for silence. */
  private settled() {
    this.mix()
    if (!this.busy) this.quiet.splice(0).forEach((resolve) => resolve())
  }

  /**
   * The music - the soundtrack and a "name that tune" clip alike - dips only while a voice can actually be heard
   * (or, at the opening, is about to be). The second or two while his line is being written stays music.
   */
  private mix() {
    let heard = this.talking
    for (const line of this.sounding) if ((line.live || line.leadIn) && !line.dropped) heard = true
    duckMusic(heard)
    setVoiceOver(heard)
  }

  private setLine(text: string) {
    this.listener.onLine(text)
  }
}
