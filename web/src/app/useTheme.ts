import { useEffect, useState } from 'react'

import { applyTheme, getTheme, type Theme } from './theme'

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(getTheme())
  useEffect(() => {
    const handler = () => setTheme(getTheme())
    window.addEventListener('fluxus:theme', handler)
    return () => window.removeEventListener('fluxus:theme', handler)
  }, [])
  const set = (t: Theme) => {
    applyTheme(t)
    window.dispatchEvent(new Event('fluxus:theme'))
  }
  return [theme, set]
}
