import { useRef, useCallback } from 'react'

// Match recorder.py constants exactly
const SAMPLE_RATE       = 16000
const CHUNK_SIZE        = 4096          // ScriptProcessor buffer — ~256ms at 16kHz
const SILENCE_THRESHOLD = 0.01
const SILENCE_DURATION  = 1.2          // seconds of silence to end a phrase
const MAX_DURATION      = 10.0         // hard cap per phrase

const CHUNKS_PER_SEC    = SAMPLE_RATE / CHUNK_SIZE
const REQUIRED_SILENT   = Math.ceil(SILENCE_DURATION * CHUNKS_PER_SEC)  // ~5 chunks
const MAX_CHUNKS        = Math.ceil(MAX_DURATION      * CHUNKS_PER_SEC)  // ~39 chunks
const MIN_SPEECH_CHUNKS = Math.ceil(0.3              * CHUNKS_PER_SEC)  // ~2 chunks

function rms(frame: Float32Array): number {
  let sum = 0
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i]
  return Math.sqrt(sum / frame.length)
}

// Mirror of recorder.py _trim_silence — keeps one frame of lead-in and tail
function trimSilence(audio: Float32Array, threshold: number, frameSize: number): Float32Array {
  const totalFrames = Math.floor(audio.length / frameSize)
  let startFrame = 0
  let endFrame = totalFrames

  for (let i = 0; i < totalFrames; i++) {
    if (rms(audio.subarray(i * frameSize, (i + 1) * frameSize)) > threshold) {
      startFrame = Math.max(0, i - 1)
      break
    }
  }
  for (let i = totalFrames - 1; i >= 0; i--) {
    if (rms(audio.subarray(i * frameSize, (i + 1) * frameSize)) > threshold) {
      endFrame = Math.min(totalFrames, i + 2)
      break
    }
  }

  return audio.subarray(startFrame * frameSize, endFrame * frameSize)
}

function mergeFrames(frames: Float32Array[]): Float32Array {
  const total = frames.reduce((n, f) => n + f.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const f of frames) { out.set(f, offset); offset += f.length }
  return out
}

export function useMicrophone(onPhrase: (data: Float32Array) => void) {
  const contextRef  = useRef<AudioContext | null>(null)
  const workletRef  = useRef<AudioWorkletNode | null>(null)
  const streamRef   = useRef<MediaStream | null>(null)
  const onPhraseRef = useRef(onPhrase)
  onPhraseRef.current = onPhrase

  // VAD state — mutated inside the worklet message handler via refs (no re-renders)
  const framesRef        = useRef<Float32Array[]>([])
  const silentChunksRef  = useRef(0)
  const speechStartedRef = useRef(false)
  const speechChunksRef  = useRef(0)

  const resetVAD = useCallback(() => {
    framesRef.current        = []
    silentChunksRef.current  = 0
    speechStartedRef.current = false
    speechChunksRef.current  = 0
  }, [])

  // Flush accumulated frames as a trimmed phrase to the WebSocket
  const flushPhrase = useCallback(() => {
    if (speechChunksRef.current >= MIN_SPEECH_CHUNKS) {
      const merged  = mergeFrames(framesRef.current)
      const trimmed = trimSilence(merged, SILENCE_THRESHOLD, CHUNK_SIZE)
      onPhraseRef.current(trimmed)
    }
    resetVAD()
  }, [resetVAD])

  const start = useCallback(async () => {
    resetVAD()

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl:  false,
        channelCount:     1,
      },
      video: false,
    })
    streamRef.current = stream

    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
    contextRef.current = ctx

    // Load worklet processor (served from /public/audio-processor.js)
    await ctx.audioWorklet.addModule('/audio-processor.js')

    const source  = ctx.createMediaStreamSource(stream)
    const worklet = new AudioWorkletNode(ctx, 'audio-processor')
    workletRef.current = worklet

    // Worklet posts 4096-sample Float32Array chunks — same as ScriptProcessorNode did
    worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      const chunk  = new Float32Array(e.data)
      const energy = rms(chunk)

      framesRef.current.push(chunk)

      if (energy > SILENCE_THRESHOLD) {
        speechStartedRef.current = true
        speechChunksRef.current++
        silentChunksRef.current = 0
      } else if (speechStartedRef.current) {
        silentChunksRef.current++
        if (silentChunksRef.current >= REQUIRED_SILENT) {
          flushPhrase()
          return
        }
      }

      // Hard max — flush regardless to avoid unbounded buffer
      if (framesRef.current.length >= MAX_CHUNKS) {
        flushPhrase()
      }
    }

    source.connect(worklet)
    worklet.connect(ctx.destination)
  }, [resetVAD, flushPhrase])

  const stop = useCallback(() => {
    workletRef.current?.disconnect()
    workletRef.current?.port.close()
    contextRef.current?.close()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    workletRef.current = null
    contextRef.current = null
    streamRef.current  = null
    resetVAD()
  }, [resetVAD])

  return { start, stop }
}
