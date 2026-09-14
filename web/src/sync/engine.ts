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

export type SyncStatus = 'idle' | 'syncing' | 'offline' | 'error'

type Listener = (state: SyncState) => void

export interface SyncState {
  status: SyncStatus
  lastSyncAt: string | null
  error: string | null
  pendingChanges: number
}

const RETENTION_DAYS = 30
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
      const patch: Partial<LocalEntry> = { updated_at: at }
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
    await db.entries.where('id').anyOf(entryIds).modify({ read, readFlag: read ? 1 : 0, updated_at: at })
    await db.outbox.bulkAdd(entryIds.map((entry_id) => ({ entry_id, read, at })))
  })
  await refreshPending()
  void pushOutbox()
}

let pushing = false
export async function pushOutbox(): Promise<void> {
  if (pushing || !isOnline() || !getToken()) return
  pushing = true
  try {
    while (true) {
      const batch = await db.outbox.orderBy('id').limit(200).toArray()
      if (batch.length === 0) break
      const changes = batch.map((b) => ({ entry_id: b.entry_id, read: b.read ?? null, starred: b.starred ?? null, at: b.at }))
      unwrap(await api().POST('/api/sync', { body: { changes } }))
      await db.outbox.bulkDelete(batch.map((b) => b.id!))
    }
  } catch (err) {
    console.warn('push outbox échoué', err)
  } finally {
    pushing = false
    await refreshPending()
  }
}

async function mergeFeeds(page: SyncPull) {
  await db.transaction('rw', db.entries, db.feeds, db.categories, db.summaries, async () => {
    await db.categories.clear()
    await db.categories.bulkPut(page.categories)
    await db.feeds.clear()
    await db.feeds.bulkPut(page.feeds)
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
  const cutoff = Date.now() - RETENTION_DAYS * 24 * 3600 * 1000
  const old = await db.entries.where('publishedTs').below(cutoff).filter((e) => e.read && !e.starred).primaryKeys()
  if (old.length) {
    await db.entries.bulkDelete(old)
    await db.summaries.where('entry_id').anyOf(old).delete()
  }
}

export async function syncNow(): Promise<void> {
  if (inFlight) return inFlight
  inFlight = (async () => {
    if (!getToken()) return
    if (!isOnline()) {
      emit({ status: 'offline' })
      return
    }
    emit({ status: 'syncing', error: null })
    try {
      await pushOutbox()
      const since = (await getMeta('sync.since')) ?? undefined
      let cursor: string | undefined
      let runStartedAt: string | undefined
      let hasMore = true
      let pages = 0
      while (hasMore && pages < 500) {
        const page = unwrap(
          await api().GET('/api/sync', { params: { query: cursor ? { cursor, limit: 300 } : { since, limit: 300 } } }),
        )
        runStartedAt ??= page.server_time
        if (pages === 0) await mergeFeeds(page)
        await mergeEntries(page)
        cursor = page.next_cursor ?? undefined
        hasMore = page.has_more && Boolean(cursor)
        pages += 1
      }
      // Le curseur `since` n'avance qu'une fois la passe complète : une passe interrompue repart du début.
      if (runStartedAt) await setMeta('sync.since', runStartedAt)
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
  const onVisible = () => {
    if (document.visibilityState === 'visible') void syncNow()
  }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', () => emit({ status: 'offline' }))
  document.addEventListener('visibilitychange', onVisible)
  timer = window.setInterval(() => void syncNow(), intervalMs)
  return () => {
    window.removeEventListener('online', onOnline)
    document.removeEventListener('visibilitychange', onVisible)
    if (timer) window.clearInterval(timer)
  }
}
