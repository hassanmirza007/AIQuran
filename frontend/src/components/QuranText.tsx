import type { AyahData, WordStatus, Pointer } from '../types'

interface Props {
  ayahs: AyahData[]
  wordStatuses: Map<string, WordStatus>
  currentPointer: Pointer | null
}

const STATUS_CLASSES: Record<WordStatus, string> = {
  idle:      'text-gray-800 hover:text-gray-600',
  active:    'bg-amber-100 border-b-2 border-amber-400 rounded px-1 animate-pulse',
  correct:   'text-green-700 bg-green-50 rounded px-1',
  incorrect: 'text-red-700 bg-red-100 rounded px-1',
  missed:    'text-orange-400 line-through opacity-70',
  extra:     'text-gray-400 line-through opacity-50',
}

function resolveStatus(
  ayahNum: number,
  wordIdx: number,
  wordStatuses: Map<string, WordStatus>,
  currentPointer: Pointer | null,
): WordStatus {
  const stored = wordStatuses.get(`${ayahNum}-${wordIdx}`)
  if (stored) return stored
  if (currentPointer?.ayah === ayahNum && currentPointer?.word_index === wordIdx) {
    return 'active'
  }
  return 'idle'
}

export function QuranText({ ayahs, wordStatuses, currentPointer }: Props) {
  if (ayahs.length === 0) return null

  return (
    <div className="space-y-4">
      {ayahs.map((ayah) => (
        <div
          key={ayah.ayah_number}
          className="rounded-2xl bg-white px-6 py-5 shadow-sm ring-1 ring-gray-100"
        >
          <div className="mb-3 text-[10px] font-bold uppercase tracking-widest text-indigo-300">
            Ayah {ayah.ayah_number}
          </div>
          <div
            dir="rtl"
            className="flex flex-wrap gap-x-3 gap-y-2 text-3xl leading-loose"
            style={{ fontFamily: "'Amiri', serif" }}
          >
            {ayah.words.map((word, idx) => {
              const status = resolveStatus(ayah.ayah_number, idx, wordStatuses, currentPointer)
              return (
                <span
                  key={idx}
                  className={`cursor-default select-none transition-all duration-300 ${STATUS_CLASSES[status]}`}
                >
                  {word}
                </span>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
