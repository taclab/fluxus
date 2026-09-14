export type Theme = 'light' | 'dark' | 'system'

const KEY = 'fluxus.theme'

export function getTheme(): Theme {
  return (localStorage.getItem(KEY) as Theme) || 'system'
}

export function applyTheme(theme: Theme): void {
  localStorage.setItem(KEY, theme)
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', dark ? '#0f172a' : '#ffffff')
}

export function watchSystemTheme(): () => void {
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = () => {
    if (getTheme() === 'system') applyTheme('system')
  }
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}
