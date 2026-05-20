import type { SummaryData } from '../types'

interface Props {
  data: SummaryData
  onRestart: () => void
}

export function SummaryPanel({ data, onRestart }: Props) {
  const { mistakes, stats } = data

  const byAyah = mistakes.reduce<Record<number, typeof mistakes>>((acc, m) => {
    ;(acc[m.ayah] = acc[m.ayah] ?? []).push(m)
    return acc
  }, {})

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="rounded-2xl bg-gradient-to-br from-indigo-600 to-purple-700 p-6 text-center text-white">
        <p className="text-sm font-medium text-indigo-200 mb-1">Recitation Complete</p>
        <h2 className="text-2xl font-bold">Al-Fatihah</h2>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'Accuracy', value: `${stats.accuracy}%`, color: 'text-indigo-600' },
          { label: 'Correct',  value: stats.correct,        color: 'text-green-600' },
          { label: 'Missed',   value: stats.missed,         color: 'text-orange-500' },
          { label: 'Incorrect',value: stats.incorrect,      color: 'text-red-500' },
        ].map((s) => (
          <div
            key={s.label}
            className="rounded-xl bg-white p-3 text-center shadow-sm ring-1 ring-gray-100"
          >
            <div className={`text-xl font-bold ${s.color}`}>{s.value}</div>
            <div className="mt-0.5 text-[11px] text-gray-400">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Mistakes */}
      {mistakes.length === 0 ? (
        <div className="rounded-2xl bg-green-50 ring-1 ring-green-200 p-6 text-center">
          <div className="text-3xl mb-2">🌟</div>
          <p className="text-green-700 font-semibold">Perfect — no mistakes!</p>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs font-bold uppercase tracking-wider text-gray-400">
            Mistakes by Ayah
          </p>
          {Object.entries(byAyah)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([ayahNum, ayahMistakes]) => {
              const missed   = ayahMistakes.filter((m) => m.type === 'missed')
              const incorrect = ayahMistakes.filter((m) => m.type === 'incorrect')
              const partial   = ayahMistakes.filter((m) => m.type === 'partial_match')
              return (
                <div
                  key={ayahNum}
                  className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-gray-100 space-y-3"
                >
                  <div className="text-xs font-bold uppercase tracking-wider text-indigo-500">
                    Ayah {ayahNum}
                  </div>

                  {missed.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-semibold text-orange-500">Missed words</p>
                      <div
                        dir="rtl"
                        className="flex flex-wrap gap-2"
                        style={{ fontFamily: "'Amiri', serif" }}
                      >
                        {missed.map((m, i) => (
                          <span
                            key={i}
                            className="rounded-lg bg-orange-50 px-3 py-1 text-xl text-orange-600 line-through opacity-75"
                          >
                            {m.expected}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {partial.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-semibold text-amber-600">Partial matches</p>
                      <div className="space-y-2">
                        {partial.map((m, i) => (
                          <div
                            key={i}
                            dir="rtl"
                            className="flex items-center gap-3 text-xl"
                            style={{ fontFamily: "'Amiri', serif" }}
                          >
                            <span className="rounded-lg bg-amber-50 px-3 py-1 text-amber-700">
                              {m.spoken}
                            </span>
                            <span className="text-sm text-gray-300">←</span>
                            <span className="rounded-lg bg-green-50 px-3 py-1 text-green-700">
                              {m.expected}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {incorrect.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-semibold text-red-500">Incorrect words</p>
                      <div className="space-y-2">
                        {incorrect.map((m, i) => (
                          <div
                            key={i}
                            dir="rtl"
                            className="flex items-center gap-3 text-xl"
                            style={{ fontFamily: "'Amiri', serif" }}
                          >
                            <span className="rounded-lg bg-red-50 px-3 py-1 text-red-600">
                              {m.spoken}
                            </span>
                            <span className="text-sm text-gray-300">←</span>
                            <span className="rounded-lg bg-green-50 px-3 py-1 text-green-700">
                              {m.expected}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
        </div>
      )}

      {/* Restart */}
      <button
        onClick={onRestart}
        className="w-full rounded-full bg-indigo-600 py-3 text-sm font-semibold text-white hover:bg-indigo-700 active:scale-95 transition-all shadow-md"
      >
        Recite Again
      </button>
    </div>
  )
}
