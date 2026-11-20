// The microphone for hold-to-talk. It is opened when the player presses the button and fully
// released when they let go, so the browser's "microphone in use" indicator is only ever on
// while they are actually holding it.

import { audioContext } from './sound'

let workletReady: Promise<void> | null = null

export function micSupported(): boolean {
  return Boolean(navigator.mediaDevices?.getUserMedia) && typeof AudioWorkletNode !== 'undefined' && window.isSecureContext
}

export class Mic {
  private stream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private node: AudioWorkletNode | null = null

  /** Start capturing. `onChunk` receives 24 kHz 16-bit mono audio, about ten times a second. */
  async start(onChunk: (pcm: ArrayBuffer) => void): Promise<void> {
    const context = audioContext()
    if (context.state === 'suspended') await context.resume()
    workletReady ??= context.audioWorklet.addModule('/mic-worklet.js').catch((error) => {
      workletReady = null // a hiccup loading it must not break the microphone for the rest of the visit
      throw error
    })
    const [stream] = await Promise.all([
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }),
      workletReady,
    ])
    this.stream = stream
    this.source = context.createMediaStreamSource(stream)
    this.node = new AudioWorkletNode(context, 'mic-capture', { numberOfInputs: 1, numberOfOutputs: 0 })
    this.node.port.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) onChunk(event.data)
    }
    this.source.connect(this.node)
  }

  /** Stop, making sure the last fraction of a second has been delivered first. */
  async stop(): Promise<void> {
    const node = this.node
    if (node) {
      await new Promise<void>((resolve) => {
        const timeout = window.setTimeout(resolve, 120)
        const previous = node.port.onmessage
        node.port.onmessage = (event) => {
          if (event.data === 'flushed') {
            window.clearTimeout(timeout)
            resolve()
          } else previous?.call(node.port, event)
        }
        node.port.postMessage('flush')
      })
    }
    this.source?.disconnect()
    this.node?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = this.source = this.node = null
  }
}
