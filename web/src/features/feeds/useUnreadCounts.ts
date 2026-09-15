import { useLiveQuery } from 'dexie-react-hooks'

import { db } from '../../db/db'

export interface UnreadCounts {
  byFeed: Map<number, number>
  bySmartFeed: Map<number, number>
}

const EMPTY: UnreadCounts = { byFeed: new Map(), bySmartFeed: new Map() }

/** Non lus par flux et par flux intelligent : un seul parcours de l'index readFlag = 0. */
export function useUnreadCountsFull(): UnreadCounts {
  return (
    useLiveQuery(async (): Promise<UnreadCounts> => {
      const byFeed = new Map<number, number>()
      const bySmartFeed = new Map<number, number>()
      await db.entries
        .where('readFlag')
        .equals(0)
        .each((e) => {
          byFeed.set(e.feed_id, (byFeed.get(e.feed_id) ?? 0) + 1)
          for (const id of e.smart_feed_ids ?? []) bySmartFeed.set(id, (bySmartFeed.get(id) ?? 0) + 1)
        })
      return { byFeed, bySmartFeed }
    }, []) ?? EMPTY
  )
}

/** Compatibilité : non lus par flux seulement. */
export function useUnreadCounts(): Map<number, number> {
  return useUnreadCountsFull().byFeed
}
