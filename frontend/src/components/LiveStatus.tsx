import type { WordResult } from '../types'

interface Props {
  phase: 'idle' | 'recording' | 'finished'
  transcript: string
  lastResults: WordResult[]
  onStart: () => void
  onStop: () => void
}

const BADGE: Record<string, string> = {
  correct:       'bg-green-100 text-green-700',
  incorrect:     'bg-red-100 text-red-700',
  missed:        'bg-orange-100 text-orange-700',
  extra:         'bg-gray-100 text-gray-500',
  partial_match: 'bg-yellow-100 text-yellow-700',
  jump:          'bg-purple-100 text-purple-700',
}

export function LiveStatus({ phase, transcript, lastResults, onStart, onStop }: Props) {
  return (
    <div className="space-y-4">
      {/* Microphone controls */}
      <div className="flex items-center gap-3">
        {phase === 'idle' && (
          <button
            onClick={onStart}
            className="rounded-full bg-indigo-600 px-7 py-2.5 text-sm font-semibold text-white shadow-md hover:bg-indigo-700 active:scale-95 transition-all"
          >
            Start Recitation
          </button>
        )}
        {phase === 'recording' && (
          <>
            <div className="flex items-center gap-2 rounded-full bg-red-50 px-4 py-2">
              <span className="h-2.5 w-2.5 rounded-full bg-red-500 animate-pulse" />
              <span className="text-sm font-medium text-red-700">Listening…</span>
            </div>
            <button
              onClick={onStop}
              className="rounded-full border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 transition-all"
            >
              Stop
            </button>
          </>
        )}
      </div>

      {/* Live transcript */}
      {transcript && (
        <div className="rounded-xl bg-indigo-50 px-4 py-3 text-sm">
          <span className="font-semibold text-indigo-400 mr-2">Heard</span>
          <span
            dir="rtl"
            className="text-indigo-800"
            style={{ fontFamily: "'Amiri', serif", fontSize: '1.1rem' }}
          >
            {transcript}
          </span>
        </div>
      )}

      {/* Validation results table */}
      {lastResults.length > 0 && (
        <div className="overflow-hidden rounded-xl ring-1 ring-gray-200 text-sm">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                <th className="px-4 py-2.5 text-left">Spoken</th>
                <th className="px-4 py-2.5 text-left">Expected</th>
                <th className="px-4 py-2.5 text-left">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {lastResults.map((r, i) => (
                <tr key={i}>
                  <td
                    className="px-4 py-2.5 text-base"
                    dir="rtl"
                    style={{ fontFamily: "'Amiri', serif" }}
                  >
                    {r.spoken === '-' ? <span className="text-gray-300">—</span> : r.spoken}
                  </td>
                  <td
                    className="px-4 py-2.5 text-base"
                    dir="rtl"
                    style={{ fontFamily: "'Amiri', serif" }}
                  >
                    {r.expected === '-' ? <span className="text-gray-300">—</span> : r.expected}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${BADGE[r.status] ?? 'bg-gray-100 text-gray-500'}`}
                    >
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
