import { useState, useCallback, useEffect } from 'react'
import type { AyahData, WordResult, Pointer, SummaryData, WordStatus, SurahInfo } from './types'
import { useRecitationWS } from './hooks/useRecitationWS'
import { useMicrophone } from './hooks/useMicrophone'
import { QuranText } from './components/QuranText'
import { LiveStatus } from './components/LiveStatus'
import { SummaryPanel } from './components/SummaryPanel'
import { SurahSelector } from './components/SurahSelector'

type Phase = 'idle' | 'recording' | 'finished'

export default function App() {
  const [phase, setPhase]               = useState<Phase>('idle')
  const [surahs, setSurahs]             = useState<SurahInfo[]>([])
  const [selectedSurah, setSelectedSurah] = useState(1)
  const [surahName, setSurahName]       = useState('')
  const [ayahs, setAyahs]               = useState<AyahData[]>([])
  const [wordStatuses, setWordStatuses] = useState<Map<string, WordStatus>>(new Map())
  const [currentPointer, setPointer]    = useState<Pointer | null>(null)
  const [transcript, setTranscript]     = useState('')
  const [lastResults, setLastResults]   = useState<WordResult[]>([])
  const [summary, setSummary]           = useState<SummaryData | null>(null)
  const [errorMsg, setErrorMsg]         = useState('')

  // Backend is the single source of truth for word positions.
  // applyResults uses the ayah/word_index already attached by the backend.
  const applyResults = useCallback((results: WordResult[], newPointer: Pointer) => {
    setWordStatuses((prev) => {
      const next = new Map(prev)
      for (const r of results) {
        const s = r.status
        if (
          (s === 'correct' || s === 'incorrect' || s === 'missed' || s === 'partial_match') &&
          r.ayah != null && r.word_index != null
        ) {
          next.set(
            `${r.ayah}-${r.word_index}`,
            (s === 'partial_match' ? 'incorrect' : s) as WordStatus,
          )
        } else if (s === 'jump') {
          for (const pos of r.missed_positions ?? []) {
            next.set(`${pos.ayah}-${pos.word_index}`, 'missed')
          }
        }
        // extra / noise / empty: no expected position to colour
      }
      return next
    })
    setPointer(newPointer)
  }, [])

  const ws = useRecitationWS({
    onSessionStarted: (name, ayahList, pointer) => {
      setSurahName(name)
      setAyahs(ayahList)
      setPointer(pointer)
    },
    onTranscript: setTranscript,
    onValidationResult: (results, pointer) => {
      setLastResults(results)
      applyResults(results, pointer)
    },
    onPointerUpdate: (pointer) => setPointer(pointer),
    onSummary: (data) => {
      setSummary(data)
      setPhase('finished')
    },
    onError: setErrorMsg,
  })

  const mic = useMicrophone(ws.sendAudioChunk)

  // Load the surah catalogue once for the dropdown.
  useEffect(() => {
    fetch('/api/surahs')
      .then((res) => res.json())
      .then((data: SurahInfo[]) => setSurahs(data))
      .catch(() => setErrorMsg('Failed to load surah list.'))
  }, [])

  const handleStart = useCallback(async () => {
    setPhase('recording')
    setWordStatuses(new Map())
    setTranscript('')
    setLastResults([])
    setSummary(null)
    setErrorMsg('')
    ws.connect(selectedSurah)
    try {
      await mic.start()
    } catch {
      setErrorMsg('Microphone access denied. Please allow mic permissions and try again.')
      setPhase('idle')
      ws.disconnect()
    }
  }, [ws, mic, selectedSurah])

  const handleStop = useCallback(() => {
    mic.stop()
    ws.requestStop()  // backend emits partial summary → onSummary sets phase to 'finished'
  }, [mic, ws])

  const handleRestart = useCallback(() => {
    mic.stop()
    ws.disconnect()
    setPhase('idle')
    setSurahName('')
    setAyahs([])
    setWordStatuses(new Map())
    setPointer(null)
    setTranscript('')
    setLastResults([])
    setSummary(null)
    setErrorMsg('')
  }, [mic, ws])

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-50 via-white to-purple-50 py-10 px-4">
      <div className="mx-auto max-w-xl space-y-6">
        {/* Header */}
        <div className="text-center">
          <h1 className="text-3xl font-bold text-indigo-900">
            {surahName ||
              surahs.find((s) => s.surah === selectedSurah)?.name ||
              'Quran Recitation'}
          </h1>
          {currentPointer && phase !== 'finished' && (
            <p className="mt-1 text-sm text-indigo-400">
              Ayah {currentPointer.ayah} · Word {currentPointer.word_index + 1}
            </p>
          )}
        </div>

        {/* Error banner */}
        {errorMsg && (
          <div className="rounded-xl bg-red-50 ring-1 ring-red-200 px-4 py-3 text-sm text-red-700">
            {errorMsg}
          </div>
        )}

        {/* Main content */}
        {phase === 'finished' && summary ? (
          <SummaryPanel data={summary} onRestart={handleRestart} />
        ) : (
          <>
            {phase === 'idle' && surahs.length > 0 && (
              <SurahSelector
                surahs={surahs}
                value={selectedSurah}
                onChange={setSelectedSurah}
                disabled={phase !== 'idle'}
              />
            )}
            {ayahs.length > 0 && (
              <QuranText
                ayahs={ayahs}
                wordStatuses={wordStatuses}
                currentPointer={currentPointer}
              />
            )}
            <LiveStatus
              phase={phase}
              transcript={transcript}
              lastResults={lastResults}
              onStart={handleStart}
              onStop={handleStop}
            />
          </>
        )}
      </div>
    </div>
  )
}
