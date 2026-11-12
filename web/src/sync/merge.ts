/**
 * Écritures du pull dans la base locale : fusion d'une page d'articles et purge par rétention.
 * Ne dépend que de Dexie (ni réseau ni état du moteur) : testé dans `merge.test.ts`.
 */

import type { EntryOut, SyncPull } from '../api/client'
import { db, toLocalEntry, type LocalEntry } from '../db/db'

/** Un article dont l'état a changé depuis moins longtemps que ceci échappe à la purge locale. */
export const RECENT_CHANGE_MS = 24 * 3600 * 1000

export async function mergeEntries(page: Pick<SyncPull, 'entries' | 'summaries'>) {
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

/**
 * Purge les articles lus, non favoris, plus vieux que la rétention. Par les index, clés seulement :
 * un article local porte tout son HTML. Un article marqué lu depuis moins d'un jour est gardé, sinon
 * un vieil article ouvert disparaîtrait du lecteur à la synchronisation suivante.
 */
export async function applyRetention(retentionDays: number, now: number = Date.now()) {
  const cutoff = now - retentionDays * 24 * 3600 * 1000
  const [readOld, starredOld, justChanged] = await Promise.all([
    db.entries.where('[readFlag+publishedTs]').between([1, -Infinity], [1, cutoff], true, false).primaryKeys(),
    db.entries.where('[starredFlag+publishedTs]').between([1, -Infinity], [1, cutoff], true, false).primaryKeys(),
    db.entries.where('[readFlag+updatedTs]').between([1, now - RECENT_CHANGE_MS], [1, Infinity], true, true).primaryKeys(),
  ])
  const keep = new Set<number>([...starredOld, ...justChanged])
  const old = readOld.filter((id) => !keep.has(id))
  if (old.length) {
    await db.entries.bulkDelete(old)
    await db.summaries.where('entry_id').anyOf(old).delete()
  }
}
