import createClient, { type Middleware } from 'openapi-fetch'

import type { components, paths } from './schema'
import { EPOCH_HEADER, knownEpoch, markEpochStale, STALE_EPOCH_CODE, STALE_EPOCH_EVENT } from '../sync/epoch'
import { getT } from '../i18n'
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
export type SmartFeed = Schemas['SmartFeedOut']
export type SmartFeedIn = Schemas['SmartFeedIn']
export type SmartFeedDefinitionIn = Schemas['SmartFeedDefinitionIn']

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : extractMessage(detail) ?? getT().common.httpError(status))
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

async function isStaleEpoch(response: Response): Promise<boolean> {
  try {
    const body = (await response.clone().json()) as { detail?: { code?: string } }
    return body.detail?.code === STALE_EPOCH_CODE
  } catch {
    return false
  }
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    // Toute écriture annonce l'époque du cache : après un import de sauvegarde, les identifiants que
    // cet appareil connaît désignent d'autres contenus et le serveur refuse (409 `stale_epoch`).
    if (request.method !== 'GET') {
      const epoch = await knownEpoch().catch(() => null)
      if (epoch) request.headers.set(EPOCH_HEADER, encodeURIComponent(epoch))
    }
    return request
  },
  async onResponse({ response, request }) {
    if (response.status === 401 && !request.url.includes('/api/auth/')) {
      setToken(null)
    }
    if (response.status === 409 && (await isStaleEpoch(response))) {
      await markEpochStale()
      window.dispatchEvent(new Event(STALE_EPOCH_EVENT))
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
