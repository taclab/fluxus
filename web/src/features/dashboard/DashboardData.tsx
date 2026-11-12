import { useLiveQuery } from 'dexie-react-hooks'
import { useMemo, type ReactNode } from 'react'

import { db } from '../../db/db'
import { useUnreadCountsFull } from '../feeds/useUnreadCounts'
import { DashboardDataContext, type DashboardData } from './dashboardContext'

/** Trois requêtes partagées par tous les widgets au lieu d'une par widget. */
export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const feeds = useLiveQuery(() => db.feeds.toArray(), [])
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), [])
  const smartFeeds = useLiveQuery(() => db.smartFeeds.orderBy('position').toArray(), [])
  const { byFeed: unreadByFeed, bySmartFeed: unreadBySmartFeed } = useUnreadCountsFull()
  const value = useMemo<DashboardData>(
    () => ({
      feeds: feeds ?? [],
      categories: categories ?? [],
      smartFeeds: smartFeeds ?? [],
      feedMap: new Map((feeds ?? []).map((f) => [f.id, f])),
      categoryMap: new Map((categories ?? []).map((c) => [c.id, c])),
      smartFeedMap: new Map((smartFeeds ?? []).map((s) => [s.id, s])),
      unreadByFeed,
      unreadBySmartFeed,
      ready: feeds !== undefined && categories !== undefined,
    }),
    [feeds, categories, smartFeeds, unreadByFeed, unreadBySmartFeed],
  )
  return <DashboardDataContext.Provider value={value}>{children}</DashboardDataContext.Provider>
}
