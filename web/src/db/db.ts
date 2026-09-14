import Dexie, { type EntityTable } from 'dexie'

import type { Category, EntryOut, Feed, SummaryOut } from '../api/client'

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
  summaries!: EntityTable<SummaryOut, 'id'>
  outbox!: EntityTable<OutboxItem, 'id'>
  meta!: EntityTable<MetaRow, 'key'>

  constructor() {
    super('fluxus')
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
  }
}

export const db = new FluxusDB()

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
    db.summaries.clear(),
    db.outbox.clear(),
    db.meta.clear(),
  ])
}
