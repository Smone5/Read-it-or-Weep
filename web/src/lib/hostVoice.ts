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
import { audioMode, duckEffects, hostAudioContext, soundOn } from './sound'
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
  /** Some of it was actually played out loud. */
  heard: boolean
  /** No longer wanted: ignore whatever else arrives for it. */
  dropped: boolean
  readTimer?: number
  /** For the timing trace: seconds of speech received, and how often playback ran dry waiting for more. */
  seconds: number
  gaps: number
}

type Incoming = { type: string; id?: number; cue?: string; delta?: string; voice?: boolean; talk?: boolean; sampleRate?: number }

const START_TIMEOUT_MS = 5000 // a line that has not begun by now would arrive too late to be wanted
const CONNECT_TIMEOUT_MS = 8000 // ...with more patience for the very first line, while the host is still arriving
const FADE_SEC = 0.12 // cutting the host off: a quick fade, not a click
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
  canTalk = false

  constructor(target: Target, listener: HostListener) {
    this.target = target
    this.listener = listener
  }

  connect() {
    if (this.closed) return
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

  /** Ask the host to react. Only ids are sent; the server looks up what actually happened. */
  cue(cue: string, itemId?: string) {
    this.request({ type: 'cue', cue, itemId }, cue)
  }

  /** The game stopped waiting for a line that had not begun. Counts against the connection's health. */
  tooLate() {
    this.skip()
    this.missed()
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
    this.listener.onTalk?.('listening', true)
    const mic = new Mic()
    this.mic = mic
    try {
      await mic.start((chunk) => {
        if (this.talking && this.socket?.readyState === WebSocket.OPEN) this.socket.send(chunk)
      })
      if (!this.talking) await mic.stop() // they let go before the microphone was ready
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
      this.voice = Boolean(message.voice)
      this.sampleRate = message.sampleRate ?? 24000
      this.canTalk = Boolean(message.talk) && micSupported()
      this.listener.onStatus?.('ready')
      this.listener.onTalk?.('idle', this.canTalk)
    } else if (message.type === 'line.start') {
      this.noLongerAwaited(id)
      if (id === this.replyId) this.listener.onTalk?.('idle', this.canTalk)
      const line: Line = { id, text: '', gain: null, sources: new Set(), nextStart: 0, complete: false, heard: false, dropped: !this.wanted(id) || this.talking, seconds: 0, gaps: 0 }
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
        this.setLine(line.text)
      }
    } else if (message.type === 'line.end') {
      const line = this.arriving
      if (!line || line.id !== id) return
      this.arriving = null
      if (line.dropped) return
      line.complete = true
      if (line.sources.size === 0) {
        if (line.heard) this.finish(line)
        // Nothing audible (sound off, or subtitles only): give the words a moment to be read.
        else line.readTimer = window.setTimeout(() => this.finish(line), Math.min(2400, 600 + line.text.length * 22))
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
    if (context.state !== 'running') return // not unlocked by a tap yet: subtitles still show
    const samples = new Int16Array(data, 0, Math.floor(data.byteLength / 2))
    if (!samples.length) return
    const buffer = context.createBuffer(1, samples.length, this.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768
    if (!line.gain) {
      line.gain = context.createGain()
      line.gain.connect(context.destination)
    }
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(line.gain)
    // Start a new line a beat late so brief network hiccups never leave a gap mid-sentence;
    // after that each chunk begins exactly where the previous one ends.
    const onTime = line.nextStart > context.currentTime
    const startAt = onTime ? line.nextStart : context.currentTime + 0.12
    if (!onTime && line.heard) line.gaps += 1
    source.start(startAt)
    line.nextStart = startAt + buffer.duration
    line.seconds += buffer.duration
    line.sources.add(source)
    if (!line.heard) {
      line.heard = true
      this.listener.onSpeaking(true)
      duckEffects(true)
      trace('host.audio', { id: line.id })
      this.mix() // his voice is about to sound: the soundtrack dips now, not before
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
    trace('host.done', { id: line.id, words: line.text.split(/\s+/).filter(Boolean).length, seconds: Math.round(line.seconds * 10) / 10, gaps: line.gaps, text: line.text })
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
    duckEffects(false)
    this.settled()
  }

  private drop(line: Line) {
    line.dropped = true
    window.clearTimeout(line.readTimer)
    const gain = line.gain
    if (!gain) return
    const now = gain.context.currentTime
    gain.gain.cancelScheduledValues(now)
    gain.gain.setValueAtTime(gain.gain.value, now)
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
   * A "name that tune" clip stays down for as long as the host (or the player, on the microphone) has
   * the floor, including while his line is on its way. The soundtrack is what fills that wait, so it
   * dips only while a voice can actually be heard.
   */
  private mix() {
    duckMusic(this.busy)
    let heard = this.talking
    for (const line of this.sounding) if (line.heard && !line.dropped) heard = true
    setVoiceOver(heard)
  }

  private setLine(text: string) {
    this.listener.onLine(text)
  }
}
