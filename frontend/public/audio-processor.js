// AudioWorklet processor — buffers samples to BUFFER_SIZE before posting.
// Matching CHUNK_SIZE = 4096 in useMicrophone.ts keeps VAD math identical.
const BUFFER_SIZE = 4096

class AudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buf    = new Float32Array(BUFFER_SIZE)
    this._filled = 0
  }

  process(inputs) {
    const channel = inputs[0]?.[0]
    if (!channel) return true

    let offset = 0
    while (offset < channel.length) {
      const toCopy = Math.min(channel.length - offset, BUFFER_SIZE - this._filled)
      this._buf.set(channel.subarray(offset, offset + toCopy), this._filled)
      this._filled += toCopy
      offset       += toCopy

      if (this._filled === BUFFER_SIZE) {
        this.port.postMessage(this._buf.slice())  // .slice() copies the buffer
        this._filled = 0
      }
    }
    return true  // keep processor alive
  }
}

registerProcessor('audio-processor', AudioProcessor)
