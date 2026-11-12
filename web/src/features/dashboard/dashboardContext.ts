import { createContext, useContext } from 'react'

import type { Category, Feed, SmartFeed } from '../../api/client'

export interface DashboardData {
  feeds: Feed[]
  categories: Category[]
  smartFeeds: SmartFeed[]
  feedMap: Map<number, Feed>
  categoryMap: Map<number, Category>
  smartFeedMap: Map<number, SmartFeed>
  unreadByFeed: Map<number, number>
  unreadBySmartFeed: Map<number, number>
  ready: boolean
}

const EMPTY: DashboardData = { feeds: [], categories: [], smartFeeds: [], feedMap: new Map(), categoryMap: new Map(), smartFeedMap: new Map(), unreadByFeed: new Map(), unreadBySmartFeed: new Map(), ready: false }
export const DashboardDataContext = createContext<DashboardData>(EMPTY)

export function useDashboardData(): DashboardData {
  return useContext(DashboardDataContext)
}
