import { useRef, useCallback } from 'react'
import type { AyahData, WordResult, Pointer, SummaryData } from '../types'

export interface WSCallbacks {
  onSessionStarted: (surahName: string, ayahs: AyahData[], pointer: Pointer) => void
  onTranscript: (text: string) => void
  onValidationResult: (results: WordResult[], pointer: Pointer) => void
  onPointerUpdate: (pointer: Pointer, expectedWord: string) => void
  onSummary: (data: SummaryData) => void
  onError: (message: string) => void
}

export function useRecitationWS(callbacks: WSCallbacks) {
  const wsRef = useRef<WebSocket | null>(null)
  // Keep callbacks in a ref so the stable connect/send/disconnect functions
  // always call the latest callbacks without needing to re-create themselves.
  const cbRef = useRef(callbacks)
  cbRef.current = callbacks

  const connect = useCallback((surahNumber: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/ws/recitation?surah=${surahNumber}`,
    )
    wsRef.current = ws

    ws.onmessage = (event: MessageEvent) => {
      const msg = JSON.parse(event.data as string)
      const { type, ...rest } = msg as { type: string } & Record<string, unknown>

      switch (type) {
        case 'session_started':
          cbRef.current.onSessionStarted(
            rest.surah_name as string,
            rest.ayahs as AyahData[],
            rest.current_pointer as Pointer,
          )
          break
        case 'transcript':
          cbRef.current.onTranscript(rest.text as string)
          break
        case 'validation_result':
          cbRef.current.onValidationResult(
            rest.results as WordResult[],
            rest.current_pointer as Pointer,
          )
          break
        case 'pointer_update':
          cbRef.current.onPointerUpdate(
            { ayah: rest.ayah as number, word_index: rest.word_index as number },
            rest.expected_word as string,
          )
          break
        case 'summary':
          cbRef.current.onSummary(rest as unknown as SummaryData)
          // Close the socket cleanly — session is done whether complete or early-stopped
          wsRef.current?.close()
          wsRef.current = null
          break
        case 'error':
          cbRef.current.onError(rest.message as string)
          break
      }
    }

    ws.onerror = () => cbRef.current.onError('WebSocket connection error')
  }, [])

  const sendAudioChunk = useCallback((data: Float32Array) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'audio_chunk', data: Array.from(data) }))
  }, [])

  const requestStop = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }))
    }
  }, [])

  const disconnect = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  return { connect, sendAudioChunk, requestStop, disconnect }
}
