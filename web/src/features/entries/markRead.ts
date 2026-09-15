/**
 * « Tout marquer comme lu » sur le périmètre complet d'une vue, pas seulement la page chargée.
 *
 * - les identifiants locaux viennent des index Dexie de la vue (jamais d'un scan) ;
 * - en ligne : une seule requête serveur ciblée (`POST /api/entries/mark-read`, bornée par `before`)
 *   puis mise à jour locale directe, sans passer par l'outbox ;
 * - hors ligne ou serveur injoignable : mise à jour locale et outbox (`recordBulkRead`), rejouée à la sync.
 * La vue « recherche » n'a pas de périmètre local fiable : elle ne traite que les résultats affichés.
 */

import { api, isOnline, unwrap, type Feed, type Schemas } from '../../api/client'
import type { View } from '../../app/view'
import { db } from '../../db/db'
import { recordBulkRead } from '../../sync/engine'

/** Au-delà, une confirmation est demandée avant de marquer. */
export const CONFIRM_THRESHOLD = 50

type MarkReadBody = Schemas['MarkReadIn']

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

/** Cible serveur équivalente à la vue ; null pour la recherche (identifiants explicites). */
function serverTarget(view: View): Partial<MarkReadBody> | null {
  switch (view.kind) {
    case 'feed':
      return { feed_id: view.id }
    case 'category':
      return { category_id: view.id }
    case 'smart':
      return { smart_feed_id: view.id }
    case 'starred':
      return { starred_only: true }
    case 'all':
    case 'unread':
      return { all: true }
    default:
      return null
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
  const target = serverTarget(view)
  const localIds = target ? await collectUnreadIds(view, feeds, now) : displayedIds
  const body: MarkReadBody = { all: false, starred_only: false, read: true, ...(target ? { ...target, before: at } : { entry_ids: displayedIds }) }

  let serverOk = false
  if (isOnline() && (target || displayedIds.length)) {
    try {
      unwrap(await api().POST('/api/entries/mark-read', { body }))
      serverOk = true
    } catch {
      serverOk = false
    }
  }
  if (serverOk) {
    // Le serveur a fait foi : écriture locale directe, sans action en attente.
    await db.entries.where('id').anyOf(localIds).modify({ read: true, readFlag: 1, updated_at: at, updatedTs: now })
    return { count: localIds.length, mode: 'server' }
  }
  if (localIds.length) await recordBulkRead(localIds, true)
  return { count: localIds.length, mode: 'outbox' }
}
