import { isCapacitor } from '../api/auth'
import { createDevicePref, oneOf } from './devicePref'

export type Theme = 'light' | 'dark' | 'system'

/** Même clé que `public/theme-init.js`, qui applique le thème avant le premier rendu. */
export const themePref = createDevicePref<Theme>('fluxus.theme', oneOf(['light', 'dark', 'system'] as const, 'system'))

export function getTheme(): Theme {
  return themePref.get()
}

export function applyTheme(theme: Theme): void {
  themePref.set(theme)
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', dark ? '#0f172a' : '#ffffff')
  void syncStatusBar(dark)
}

/**
 * APK : la WebView passe sous la barre d'état (bord à bord) ; ses icônes doivent contraster avec le
 * fond de l'application. `Style.Dark` = icônes claires pour fond sombre.
 */
async function syncStatusBar(dark: boolean): Promise<void> {
  if (!isCapacitor()) return
  try {
    const { StatusBar, Style } = await import('@capacitor/status-bar')
    await StatusBar.setStyle({ style: dark ? Style.Dark : Style.Light })
  } catch (err) {
    console.warn('barre d’état', err)
  }
}

export function watchSystemTheme(): () => void {
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = () => {
    if (getTheme() === 'system') applyTheme('system')
  }
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}
