/**
 * « Tout marquer comme lu » sur le périmètre complet d'une vue, pas seulement la page chargée.
 *
 * - le périmètre est ce que cet appareil connaît : les identifiants viennent des index Dexie de la
 *   vue (jamais d'un scan). Une cible serveur (« tout le flux jusqu'à maintenant ») marquerait aussi
 *   les articles arrivés depuis la dernière synchronisation, que personne n'a vus ;
 * - en ligne : ces identifiants sont envoyés à `POST /api/entries/mark-read`, par lots, puis la base
 *   locale est mise à jour directement, sans passer par l'outbox ;
 * - hors ligne ou serveur injoignable : mise à jour locale et outbox (`recordBulkRead`), rejouée à la sync.
 * La vue « recherche » n'a pas de périmètre local fiable : elle ne traite que les résultats affichés.
 */

import { api, isOnline, unwrap, type Feed, type Schemas } from '../../api/client'
import type { View } from '../../app/view'
import { db } from '../../db/db'
import { recordBulkRead, resyncIfPulling } from '../../sync/engine'

/** Au-delà, une confirmation est demandée avant de marquer. */
export const CONFIRM_THRESHOLD = 50

type MarkReadBody = Schemas['MarkReadIn']

/** Identifiants par requête : un seul `UPDATE … WHERE id IN (…)` côté serveur. */
const BATCH = 2000

/** Identifiants des articles non lus de la vue, publiés jusqu'à `until`, par les index. */
export async function collectUnreadIds(view: View, feeds: Feed[], until: number): Promise<number[]> {
  const byFeed = (feedId: number) => db.entries.where('[feed_id+readFlag+publishedTs]').between([feedId, 0, -Infinity], [feedId, 0, until], true, true).primaryKeys()
  switch (view.kind) {
    case 'feed':
      return byFeed(view.id!)
    case 'category': {
      const ids = feeds.filter((f) => f.category_id === view.id).map((f) => f.id)
      return (await Promise.all(ids.map(byFeed))).flat()
    }
    case 'smart':
      return db.entries
        .where('smart_feed_ids')
        .equals(view.id!)
        .and((e) => e.readFlag === 0 && e.publishedTs <= until)
        .primaryKeys()
    case 'starred':
      return db.entries
        .where('[starredFlag+publishedTs]')
        .between([1, -Infinity], [1, until], true, true)
        .and((e) => e.readFlag === 0)
        .primaryKeys()
    case 'all':
    case 'unread':
      return db.entries.where('[readFlag+publishedTs]').between([0, -Infinity], [0, until], true, true).primaryKeys()
    default:
      return []
  }
}

export interface MarkResult {
  /** Articles marqués localement. */
  count: number
  /** `server` : requête ciblée acceptée ; `outbox` : rejoué à la prochaine synchronisation. */
  mode: 'server' | 'outbox'
}

/**
 * Marque comme lus tous les articles non lus de la vue. `displayedIds` sert de périmètre pour la
 * recherche (résultats affichés, y compris ceux qui ne sont pas dans la base locale).
 */
export async function markViewRead(view: View, feeds: Feed[], displayedIds: number[]): Promise<MarkResult> {
  const now = Date.now()
  const at = new Date(now).toISOString()
  const ids = view.kind === 'search' ? displayedIds : await collectUnreadIds(view, feeds, now)
  if (ids.length === 0) return { count: 0, mode: 'server' }

  if (isOnline()) {
    try {
      for (let i = 0; i < ids.length; i += BATCH) {
        const body: MarkReadBody = { all: false, starred_only: false, read: true, entry_ids: ids.slice(i, i + BATCH) }
        unwrap(await api().POST('/api/entries/mark-read', { body }))
      }
      // Le serveur a fait foi : écriture locale directe, sans action en attente.
      await db.entries.where('id').anyOf(ids).modify({ read: true, readFlag: 1, updated_at: at, updatedTs: now })
      resyncIfPulling()
      return { count: ids.length, mode: 'server' }
    } catch {
      // Lots déjà acceptés compris : le rejeu par l'outbox est idempotent.
    }
  }
  await recordBulkRead(ids, true)
  return { count: ids.length, mode: 'outbox' }
}
