import createClient, { type Middleware } from 'openapi-fetch'

import type { components, paths } from './schema'
import { getServerUrl, getToken, setToken } from './auth'

export type Schemas = components['schemas']
export type Feed = Schemas['FeedOut']
export type Category = Schemas['CategoryOut']
export type EntryListItem = Schemas['EntryListItem']
export type EntryOut = Schemas['EntryOut']
export type SummaryOut = Schemas['SummaryOut']
export type SyncPull = Schemas['SyncPull']
export type SettingsOut = Schemas['SettingsOut']
export type StatsOut = Schemas['StatsOut']
export type DeviceOut = Schemas['DeviceOut']

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : extractMessage(detail) ?? `Erreur HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

function extractMessage(detail: unknown): string | undefined {
  if (!detail || typeof detail !== 'object') return undefined
  const d = detail as { detail?: unknown; message?: string }
  if (typeof d.detail === 'string') return d.detail
  if (d.detail && typeof d.detail === 'object' && 'message' in (d.detail as object)) {
    return (d.detail as { message: string }).message
  }
  if (Array.isArray(d.detail) && d.detail[0]?.msg) return d.detail.map((x: { msg: string }) => x.msg).join(', ')
  if (d.message) return d.message
  return undefined
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  async onResponse({ response, request }) {
    if (response.status === 401 && !request.url.includes('/api/auth/')) {
      setToken(null)
    }
    return response
  },
}

let client = createClient<paths>({ baseUrl: getServerUrl() })
client.use(authMiddleware)

/** À appeler quand l'adresse du serveur change (appli mobile). */
export function reconfigureClient(): void {
  client = createClient<paths>({ baseUrl: getServerUrl() })
  client.use(authMiddleware)
}

export function api() {
  return client
}

/** Déballe une réponse openapi-fetch en levant ApiError si nécessaire. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(result.response.status, result.error)
  }
  return result.data as T
}

export function apiUrl(path: string): string {
  return `${getServerUrl()}${path}`
}

export function authHeaders(): HeadersInit {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export const isOnline = (): boolean => (typeof navigator === 'undefined' ? true : navigator.onLine)
