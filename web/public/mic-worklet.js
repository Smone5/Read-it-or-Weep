// Runs on the audio thread. Converts the microphone to 24 kHz 16-bit mono (what the voice
// model expects) and hands it to the page in 100 ms pieces. Loaded only when a player
// first holds the talk button.
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.step = sampleRate / 24000 // input samples consumed per output sample
    this.position = 0
    this.previous = 0
    this.out = new Int16Array(2400)
    this.filled = 0
    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.send()
        this.port.postMessage('flushed')
      }
    }
  }

  send() {
    if (!this.filled) return
    const piece = this.out.slice(0, this.filled)
    this.filled = 0
    this.port.postMessage(piece.buffer, [piece.buffer])
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0]
    if (!input || !input.length) return true
    while (this.position < input.length) {
      const index = Math.floor(this.position)
      const before = index === 0 ? this.previous : input[index - 1]
      const value = before + (input[index] - before) * (this.position - index)
      const clipped = Math.max(-1, Math.min(1, value))
      this.out[this.filled++] = clipped < 0 ? clipped * 32768 : clipped * 32767
      if (this.filled === this.out.length) this.send()
      this.position += this.step
    }
    this.position -= input.length
    this.previous = input[input.length - 1]
    return true
  }
}

registerProcessor('mic-capture', MicCapture)
