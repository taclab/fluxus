import { useLiveQuery } from 'dexie-react-hooks'

import { db } from '../../db/db'

const EMPTY = new Map<number, number>()

/** Non lus par flux : un seul parcours de l'index readFlag = 0. Partagé par la barre latérale et le tableau de bord. */
export function useUnreadCounts(): Map<number, number> {
  return (
    useLiveQuery(async () => {
      const counts = new Map<number, number>()
      await db.entries
        .where('readFlag')
        .equals(0)
        .each((e) => counts.set(e.feed_id, (counts.get(e.feed_id) ?? 0) + 1))
      return counts
    }, []) ?? EMPTY
  )
}
