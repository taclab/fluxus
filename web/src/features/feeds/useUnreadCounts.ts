import { liveQuery, type Subscription } from 'dexie'
import { useSyncExternalStore } from 'react'

import { EMPTY_UNREAD, sameUnreadCounts, unreadCounts, type UnreadCounts } from '../../db/entryQueries'

export type { UnreadCounts }

/** Une seule requête vivante pour toute l'application (barre latérale, tableau de bord, en-tête de
 * liste) : ouverte au premier abonné, fermée au dernier, dernier résultat gardé entre-temps. */
let current: UnreadCounts = EMPTY_UNREAD
let subscription: Subscription | null = null
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (!subscription) {
    subscription = liveQuery(unreadCounts).subscribe({
      next: (value) => {
        if (sameUnreadCounts(current, value)) return
        current = value
        listeners.forEach((l) => l())
      },
      error: (err) => console.error('Compteurs de non lus', err),
    })
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      subscription?.unsubscribe()
      subscription = null
    }
  }
}

/** Non lus par flux, par flux intelligent, favoris et total : clés d'index seulement. */
export function useUnreadCountsFull(): UnreadCounts {
  return useSyncExternalStore(subscribe, () => current)
}

/** Un seul nombre tiré des compteurs : le composant ne se redessine que si ce nombre change
 * (la liste d'articles n'a pas à suivre chaque recalcul des compteurs de la barre latérale). */
export function useUnreadCount(select: (counts: UnreadCounts) => number | null): number | null {
  return useSyncExternalStore(subscribe, () => select(current))
}
