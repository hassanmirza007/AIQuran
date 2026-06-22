import type { SurahInfo } from '../types'

interface Props {
  surahs: SurahInfo[]
  value: number
  onChange: (surahNumber: number) => void
  disabled?: boolean
}

export function SurahSelector({ surahs, value, onChange, disabled }: Props) {
  return (
    <div className="rounded-2xl bg-white shadow-sm ring-1 ring-indigo-100 px-5 py-4">
      <label
        htmlFor="surah-select"
        className="block text-xs font-semibold uppercase tracking-wide text-indigo-400"
      >
        Select Surah
      </label>
      <select
        id="surah-select"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full rounded-xl border-0 bg-indigo-50 px-4 py-3 text-base text-indigo-900
                   ring-1 ring-inset ring-indigo-200 focus:ring-2 focus:ring-indigo-400
                   disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {surahs.map((s) => (
          <option key={s.surah} value={s.surah}>
            {s.surah}. {s.name} ({s.arabic_name}) · {s.total_verses} ayahs
          </option>
        ))}
      </select>
    </div>
  )
}
