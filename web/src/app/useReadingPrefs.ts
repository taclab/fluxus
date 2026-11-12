import { applyReadingPrefs, getReadingPrefs, readingPref, type ReadingPrefs } from './reading'

export function useReadingPrefs(): [ReadingPrefs, (p: Partial<ReadingPrefs>) => void] {
  const update = (p: Partial<ReadingPrefs>) => applyReadingPrefs({ ...getReadingPrefs(), ...p })
  return [readingPref.useValue(), update]
}
