/**
 * Moteur de synchronisation offline-first.
 *
 * - pull : GET /api/sync?since=… en boucle jusqu'à has_more=false, fusion dans Dexie
 * - push : rejoue la table outbox (changements lu / favori) vers POST /api/sync
 * - les lectures de l'interface se font toujours dans Dexie
 */

import { api, isOnline, unwrap, type EntryOut, type SyncPull } from '../api/client'
import { getToken } from '../api/auth'
import { db, getMeta, setMeta, toLocalEntry, type LocalEntry } from '../db/db'
import { flushDashboardLayout } from '../features/dashboard/useDashboardLayout'

export type SyncStatus = 'idle' | 'syncing' | 'offline' | 'error'

type Listener = (state: SyncState) => void

export interface SyncState {
  status: SyncStatus
  lastSyncAt: string | null
  error: string | null
  pendingChanges: number
}

const DEFAULT_RETENTION_DAYS = 30
let retentionDays = DEFAULT_RETENTION_DAYS
const listeners = new Set<Listener>()
let state: SyncState = { status: 'idle', lastSyncAt: null, error: null, pendingChanges: 0 }
let inFlight: Promise<void> | null = null
let timer: number | null = null

function emit(patch: Partial<SyncState>) {
  state = { ...state, ...patch }
  listeners.forEach((l) => l(state))
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  listener(state)
  return () => listeners.delete(listener)
}

export function getSyncState(): SyncState {
  return state
}

async function refreshPending() {
  emit({ pendingChanges: await db.outbox.count() })
}

/** Enregistre un changement local et tente de le pousser immédiatement. */
export async function recordChange(entryId: number, change: { read?: boolean; starred?: boolean }) {
  const at = new Date().toISOString()
  await db.transaction('rw', db.entries, db.outbox, async () => {
    const entry = await db.entries.get(entryId)
    if (entry) {
      const patch: Partial<LocalEntry> = { updated_at: at, updatedTs: Date.parse(at) }
      if (change.read !== undefined) {
        patch.read = change.read
        patch.readFlag = change.read ? 1 : 0
      }
      if (change.starred !== undefined) {
        patch.starred = change.starred
        patch.starredFlag = change.starred ? 1 : 0
      }
      await db.entries.update(entryId, patch)
    }
    await db.outbox.add({ entry_id: entryId, ...change, at })
  })
  await refreshPending()
  void pushOutbox()
}

export async function recordBulkRead(entryIds: number[], read: boolean) {
  const at = new Date().toISOString()
  await db.transaction('rw', db.entries, db.outbox, async () => {
    await db.entries.where('id').anyOf(entryIds).modify({ read, readFlag: read ? 1 : 0, updated_at: at, updatedTs: Date.parse(at) })
    await db.outbox.bulkAdd(entryIds.map((entry_id) => ({ entry_id, read, at })))
  })
  await refreshPending()
  void pushOutbox()
}

let pushInFlight: Promise<void> | null = null
let pushRequested = false

/** Pousse l'outbox. Un appel pendant un push en cours partage la promesse et demande un nouveau tour. */
export function pushOutbox(): Promise<void> {
  if (!isOnline() || !getToken()) return Promise.resolve()
  if (pushInFlight) {
    pushRequested = true
    return pushInFlight
  }
  pushInFlight = (async () => {
    try {
      do {
        pushRequested = false
        while (true) {
          const batch = await db.outbox.orderBy('id').limit(200).toArray()
          if (batch.length === 0) break
          const changes = batch.map((b) => ({ entry_id: b.entry_id, read: b.read ?? null, starred: b.starred ?? null, at: b.at }))
          const result = unwrap(await api().POST('/api/sync', { body: { changes } }))
          await db.transaction('rw', db.entries, db.outbox, async () => {
            await db.outbox.bulkDelete(batch.map((b) => b.id!))
            // Changements refusés (un autre appareil a modifié le même champ plus récemment) : le
            // serveur renvoie son état, on réaligne le cache sauf si une action locale plus récente
            // attend encore dans l'outbox pour cette entrée.
            for (const c of result.conflicts ?? []) {
              const pending = await db.outbox.where('entry_id').equals(c.entry_id).count()
              if (pending > 0) continue
              await db.entries.update(c.entry_id, { read: c.read, readFlag: c.read ? 1 : 0, starred: c.starred, starredFlag: c.starred ? 1 : 0 })
            }
          })
        }
      } while (pushRequested)
    } catch (err) {
      console.warn('push outbox échoué', err)
    } finally {
      pushInFlight = null
      await refreshPending()
    }
  })()
  return pushInFlight
}

async function mergeFeeds(page: SyncPull) {
  await db.transaction('rw', db.entries, db.feeds, db.categories, db.smartFeeds, db.summaries, async () => {
    await db.categories.clear()
    await db.categories.bulkPut(page.categories)
    await db.feeds.clear()
    await db.feeds.bulkPut(page.feeds)
    await db.smartFeeds.clear()
    await db.smartFeeds.bulkPut(page.smart_feeds)
    // Entrées dont le flux a été supprimé côté serveur.
    const orphanIds = await db.entries.where('feed_id').noneOf(page.feed_ids).primaryKeys()
    if (orphanIds.length) {
      await db.entries.bulkDelete(orphanIds)
      await db.summaries.where('entry_id').anyOf(orphanIds).delete()
    }
  })
}

async function mergeEntries(page: SyncPull) {
  await db.transaction('rw', db.entries, db.summaries, db.outbox, async () => {
    // Un changement local encore en attente ne doit pas être écrasé par la version serveur.
    const pendingIds = new Set((await db.outbox.toArray()).map((o) => o.entry_id))
    const incoming: LocalEntry[] = []
    for (const e of page.entries as EntryOut[]) {
      const local = toLocalEntry(e)
      if (pendingIds.has(e.id)) {
        const current = await db.entries.get(e.id)
        if (current) {
          local.read = current.read
          local.readFlag = current.readFlag
          local.starred = current.starred
          local.starredFlag = current.starredFlag
        }
      }
      incoming.push(local)
    }
    await db.entries.bulkPut(incoming)
    if (page.summaries.length) await db.summaries.bulkPut(page.summaries)
  })
}

async function applyRetention() {
  const cutoff = Date.now() - retentionDays * 24 * 3600 * 1000
  const old = await db.entries.where('publishedTs').below(cutoff).filter((e) => e.read && !e.starred).primaryKeys()
  if (old.length) {
    await db.entries.bulkDelete(old)
    await db.summaries.where('entry_id').anyOf(old).delete()
  }
}

/** Passe supplémentaire demandée pendant une passe en cours (une seule, quel que soit le nombre d'appels). */
let queued: Promise<void> | null = null

export async function syncNow(): Promise<void> {
  if (inFlight) {
    // Une passe en cours a pu lire le serveur avant le changement qui motive cet appel :
    // on en relance une à sa suite au lieu de se contenter de l'attendre.
    queued ??= inFlight.then(() => {
      queued = null
      return syncNow()
    })
    return queued
  }
  inFlight = (async () => {
    if (!getToken()) return
    if (!isOnline()) {
      emit({ status: 'offline' })
      return
    }
    emit({ status: 'syncing', error: null })
    try {
      await pushOutbox()
      await flushDashboardLayout()
      const since = (await getMeta('sync.since')) ?? undefined
      let cursor: string | undefined
      let runStartedAt: string | undefined
      let hasMore = true
      let pages = 0
      const MAX_PAGES = 500
      while (hasMore && pages < MAX_PAGES) {
        const page = unwrap(
          await api().GET('/api/sync', { params: { query: cursor ? { cursor, limit: 300 } : { since, limit: 300 } } }),
        )
        runStartedAt ??= page.server_time
        if (page.retention_days) retentionDays = page.retention_days
        if (pages === 0) await mergeFeeds(page)
        await mergeEntries(page)
        cursor = page.next_cursor ?? undefined
        hasMore = page.has_more && Boolean(cursor)
        pages += 1
      }
      // Le curseur `since` n'avance qu'une fois la passe complète : une passe interrompue (ou plafonnée)
      // repart du début et ne perd rien.
      if (runStartedAt && !hasMore) await setMeta('sync.since', runStartedAt)
      await applyRetention()
      const now = new Date().toISOString()
      await setMeta('sync.lastSyncAt', now)
      emit({ status: 'idle', lastSyncAt: now, error: null })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      emit({ status: isOnline() ? 'error' : 'offline', error: message })
    }
  })().finally(() => {
    inFlight = null
  })
  return inFlight
}

/** Oublie le curseur pour forcer une resynchronisation complète (30 derniers jours). */
export async function resetAndSync(): Promise<void> {
  await db.meta.delete('sync.since')
  await syncNow()
}

export function startAutoSync(intervalMs = 5 * 60 * 1000): () => void {
  void getMeta('sync.lastSyncAt').then((v) => emit({ lastSyncAt: v ?? null }))
  void refreshPending()
  void syncNow()
  const onOnline = () => void syncNow()
  const onOffline = () => emit({ status: 'offline' })
  const onVisible = () => {
    if (document.visibilityState === 'visible') void syncNow()
  }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  document.addEventListener('visibilitychange', onVisible)
  timer = window.setInterval(() => void syncNow(), intervalMs)
  return () => {
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    document.removeEventListener('visibilitychange', onVisible)
    if (timer) window.clearInterval(timer)
  }
}
