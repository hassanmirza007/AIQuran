export type WordStatus = 'idle' | 'correct' | 'incorrect' | 'missed' | 'extra' | 'active'

export interface SurahInfo {
  surah: number
  name: string
  arabic_name: string
  type: string
  total_verses: number
}

export interface AyahData {
  ayah_number: number
  words: string[]
}

export interface MissedPosition {
  ayah: number
  word_index: number
  word: string
}

export interface WordResult {
  spoken: string
  expected: string
  status: string
  message: string
  missed_words?: string[]
  // Backend-provided authoritative position (null for extra/noise/empty)
  ayah?: number | null
  word_index?: number | null
  // Only present on jump results: explicit position for each skipped word
  missed_positions?: MissedPosition[]
}

export interface Pointer {
  ayah: number
  word_index: number
}

export interface Mistake {
  ayah: number
  type: string
  expected: string
  spoken: string
}

export interface SummaryData {
  mistakes: Mistake[]
  stats: {
    total: number
    correct: number
    missed: number
    incorrect: number
    accuracy: number
  }
}
