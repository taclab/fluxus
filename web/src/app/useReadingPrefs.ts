import { useEffect, useState } from 'react'

import { applyReadingPrefs, getReadingPrefs, type ReadingPrefs } from './reading'

export function useReadingPrefs(): [ReadingPrefs, (p: Partial<ReadingPrefs>) => void] {
  const [prefs, setPrefs] = useState<ReadingPrefs>(getReadingPrefs())
  useEffect(() => {
    const handler = () => setPrefs(getReadingPrefs())
    window.addEventListener('fluxus:reading', handler)
    return () => window.removeEventListener('fluxus:reading', handler)
  }, [])
  const update = (p: Partial<ReadingPrefs>) => applyReadingPrefs({ ...getReadingPrefs(), ...p })
  return [prefs, update]
}
