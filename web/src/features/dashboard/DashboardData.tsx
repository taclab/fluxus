import { useLiveQuery } from 'dexie-react-hooks'
import { createContext, useContext, useMemo, type ReactNode } from 'react'

import type { Category, Feed } from '../../api/client'
import { db } from '../../db/db'
import { useUnreadCounts } from '../feeds/useUnreadCounts'

export interface DashboardData {
  feeds: Feed[]
  categories: Category[]
  feedMap: Map<number, Feed>
  categoryMap: Map<number, Category>
  unreadByFeed: Map<number, number>
  ready: boolean
}

const EMPTY: DashboardData = { feeds: [], categories: [], feedMap: new Map(), categoryMap: new Map(), unreadByFeed: new Map(), ready: false }
const Ctx = createContext<DashboardData>(EMPTY)

/** Trois requêtes partagées par tous les widgets au lieu d'une par widget. */
export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const feeds = useLiveQuery(() => db.feeds.toArray(), [])
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), [])
  const unreadByFeed = useUnreadCounts()
  const value = useMemo<DashboardData>(
    () => ({
      feeds: feeds ?? [],
      categories: categories ?? [],
      feedMap: new Map((feeds ?? []).map((f) => [f.id, f])),
      categoryMap: new Map((categories ?? []).map((c) => [c.id, c])),
      unreadByFeed,
      ready: feeds !== undefined && categories !== undefined,
    }),
    [feeds, categories, unreadByFeed],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useDashboardData(): DashboardData {
  return useContext(Ctx)
}
