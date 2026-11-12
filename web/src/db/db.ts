import Dexie, { type EntityTable } from 'dexie'

import { getServerUrl } from '../api/auth'
import type { Category, EntryOut, Feed, SmartFeed, SummaryOut } from '../api/client'

/**
 * Nom de la base locale, propre à l'instance serveur : cache, outbox et curseur de sync d'un
 * serveur ne doivent jamais être rejoués sur un autre (changement d'adresse dans l'appli mobile,
 * reconnexion après expiration). Sur le web, même origine → nom historique `fluxus`.
 */
export function databaseName(serverUrl: string = getServerUrl()): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  if (!serverUrl || serverUrl === origin) return 'fluxus'
  return `fluxus@${serverUrl.replace(/^https?:\/\//, '').replace(/[^a-z0-9.:-]+/gi, '_')}`
}

/** Entrée locale : identique à EntryOut avec un index dérivé pour les filtres. */
export interface LocalEntry extends EntryOut {
  /** 0 = non lu, 1 = lu (Dexie n'indexe pas les booléens). */
  readFlag: 0 | 1
  starredFlag: 0 | 1
  /** published_at en millisecondes pour trier vite. */
  publishedTs: number
  /** updated_at en millisecondes (statistique « lus aujourd'hui »). */
  updatedTs: number
}

export interface OutboxItem {
  id?: number
  entry_id: number
  read?: boolean
  starred?: boolean
  at: string
}

export interface MetaRow {
  key: string
  value: string
}

export class FluxusDB extends Dexie {
  entries!: EntityTable<LocalEntry, 'id'>
  feeds!: EntityTable<Feed, 'id'>
  categories!: EntityTable<Category, 'id'>
  smartFeeds!: EntityTable<SmartFeed, 'id'>
  summaries!: EntityTable<SummaryOut, 'id'>
  outbox!: EntityTable<OutboxItem, 'id'>
  meta!: EntityTable<MetaRow, 'key'>

  constructor() {
    super(databaseName())
    this.version(1).stores({
      entries: 'id, feed_id, publishedTs, readFlag, starredFlag, [feed_id+publishedTs], [readFlag+publishedTs], [starredFlag+publishedTs], [feed_id+readFlag+publishedTs]',
      feeds: 'id, category_id',
      categories: 'id, position',
      summaries: 'id, entry_id',
      outbox: '++id, entry_id',
      meta: 'key',
    })
    // v2 : index sur la date de mise à jour pour compter les articles lus aujourd'hui.
    this.version(2)
      .stores({
        entries:
          'id, feed_id, publishedTs, readFlag, starredFlag, updatedTs, [feed_id+publishedTs], [readFlag+publishedTs], [starredFlag+publishedTs], [feed_id+readFlag+publishedTs], [readFlag+updatedTs]',
      })
      .upgrade((tx) =>
        tx
          .table('entries')
          .toCollection()
          .modify((e: LocalEntry) => {
            e.updatedTs = new Date(e.updated_at).getTime()
          }),
      )
    // v3 : flux intelligents — index multiEntry sur le champ serveur smart_feed_ids, table des définitions.
    this.version(3)
      .stores({
        entries:
          'id, feed_id, publishedTs, readFlag, starredFlag, updatedTs, *smart_feed_ids, [feed_id+publishedTs], [readFlag+publishedTs], [starredFlag+publishedTs], [feed_id+readFlag+publishedTs], [readFlag+updatedTs]',
        smartFeeds: 'id, position',
      })
      .upgrade((tx) =>
        tx
          .table('entries')
          .toCollection()
          .modify((e: LocalEntry) => {
            if (!Array.isArray(e.smart_feed_ids)) e.smart_feed_ids = []
          }),
      )
  }
}

export const db = new FluxusDB()

// Un autre onglet ouvre une version plus récente du schéma (application mise à jour) : cet onglet doit
// fermer sa connexion, sinon la migration de l'autre reste bloquée. Il se recharge pour la reprendre.
db.on('versionchange', () => {
  db.close()
  if (typeof window !== 'undefined' && typeof window.location?.reload === 'function') window.location.reload()
})

/** Millisecondes ou undefined : une clé NaN rendrait l'entrée invisible dans tous les index. */
function safeTs(iso: string | null | undefined): number | undefined {
  const t = iso ? new Date(iso).getTime() : NaN
  return Number.isFinite(t) ? t : undefined
}

export function toLocalEntry(e: EntryOut): LocalEntry {
  return {
    ...e,
    readFlag: e.read ? 1 : 0,
    starredFlag: e.starred ? 1 : 0,
    publishedTs: safeTs(e.published_at) ?? safeTs(e.updated_at) ?? Date.now(),
    updatedTs: safeTs(e.updated_at) ?? Date.now(),
  }
}

export async function getMeta(key: string): Promise<string | undefined> {
  return (await db.meta.get(key))?.value
}

export async function setMeta(key: string, value: string): Promise<void> {
  await db.meta.put({ key, value })
}

export async function clearDatabase(): Promise<void> {
  await Promise.all([
    db.entries.clear(),
    db.feeds.clear(),
    db.categories.clear(),
    db.smartFeeds.clear(),
    db.summaries.clear(),
    db.outbox.clear(),
    db.meta.clear(),
  ])
}
