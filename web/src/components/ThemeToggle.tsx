import { Monitor, Moon, Sun } from 'lucide-react'
import type { Theme } from '../app/theme'
import { useTheme } from '../app/useTheme'
import { IconButton } from './ui'

const LABELS: Record<Theme, string> = { light: 'Thème clair', dark: 'Thème sombre', system: 'Thème automatique (système)' }

export function ThemeToggle({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  const [theme, setTheme] = useTheme()
  // Bascule entre clair et sombre selon le rendu effectif ; « automatique » reste accessible dans les paramètres.
  const effectiveDark = document.documentElement.classList.contains('dark')
  const next: Theme = effectiveDark ? 'light' : 'dark'
  const Icon = theme === 'system' ? Monitor : theme === 'light' ? Sun : Moon
  return (
    <IconButton label={`${LABELS[theme]} — passer en ${next === 'dark' ? 'sombre' : 'clair'}`} size={size} onClick={() => setTheme(next)}>
      <Icon className="size-4" />
    </IconButton>
  )
}
