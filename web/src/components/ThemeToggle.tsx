import { Monitor, Moon, Sun } from 'lucide-react'
import type { Theme } from '../app/theme'
import { useTheme } from '../app/useTheme'
import { useT } from '../i18n'
import { IconButton } from './ui'

export function ThemeToggle({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  const t = useT().app.theme
  const [theme, setTheme] = useTheme()
  // Bascule entre clair et sombre selon le rendu effectif ; « automatique » reste accessible dans les paramètres.
  const effectiveDark = document.documentElement.classList.contains('dark')
  const next: Theme = effectiveDark ? 'light' : 'dark'
  const Icon = theme === 'system' ? Monitor : theme === 'light' ? Sun : Moon
  return (
    <IconButton label={t.switchTo(t[theme], next === 'dark')} size={size} onClick={() => setTheme(next)}>
      <Icon className="size-4" />
    </IconButton>
  )
}
