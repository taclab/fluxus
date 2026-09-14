/** Stockage du token et de l'adresse du serveur (localStorage). */

const TOKEN_KEY = 'fluxus.token'
const SERVER_KEY = 'fluxus.serverUrl'

export const isCapacitor = (): boolean =>
  typeof window !== 'undefined' && Boolean((window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor?.isNativePlatform?.())

export function getServerUrl(): string {
  const stored = localStorage.getItem(SERVER_KEY)
  if (stored) return stored.replace(/\/+$/, '')
  // Sur le web, l'API est servie par la même origine.
  return isCapacitor() ? '' : window.location.origin
}

export function setServerUrl(url: string): void {
  localStorage.setItem(SERVER_KEY, url.trim().replace(/\/+$/, ''))
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
  window.dispatchEvent(new Event('fluxus:auth'))
}

export function isDeviceToken(token: string | null): boolean {
  return Boolean(token && token.startsWith('flx_'))
}
