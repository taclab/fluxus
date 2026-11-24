import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Inbox, LayoutDashboard, ListChecks, Plus, RefreshCw, Rss, Settings, Star } from 'lucide-react'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { Category, Feed, SmartFeed } from '../../api/client'
import { createDevicePref, idList } from '../../app/devicePref'
import { IconButton } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import { useSyncState } from '../../sync/useSync'
import type { View } from '../../app/view'
import { AddFeedDialog } from './AddFeedDialog'
import { Count } from './FeedIcon'
import { SubscriptionTree } from './SubscriptionTree'
import { useUnreadCountsFull } from './useUnreadCounts'
import { useT } from '../../i18n'
import { timeOf } from '../../lib/format'

interface Props {
  view: View
  onSelect: (view: View) => void
  onEditFeed: (feed: Feed) => void
  onEditCategory: (category: Category | null) => void
  onEditSmartFeed: (smartFeed: SmartFeed | null) => void
}

const EMPTY_FEEDS: Feed[] = []
const EMPTY_CATEGORIES: Category[] = []
const EMPTY_SMART: SmartFeed[] = []
const collapsedPref = createDevicePref<number[]>('fluxus.collapsed', idList, JSON.stringify)

export function Sidebar({ view, onSelect, onEditFeed, onEditCategory, onEditSmartFeed }: Props) {
  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? EMPTY_FEEDS
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? EMPTY_CATEGORIES
  const { byFeed: unreadByFeed, bySmartFeed, total: totalUnread } = useUnreadCountsFull()
  const smartFeeds = useLiveQuery(() => db.smartFeeds.orderBy('position').toArray(), []) ?? EMPTY_SMART
  const starredCount = useLiveQuery(() => db.entries.where('starredFlag').equals(1).count(), []) ?? 0
  const sync = useSyncState()
  const [addOpen, setAddOpen] = useState(false)
  const collapsedIds = collapsedPref.useValue()
  const collapsed = useMemo(() => new Set(collapsedIds), [collapsedIds])

  const updateCollapsed = useCallback((fn: (next: Set<number>) => void) => {
    const next = new Set(collapsedPref.get())
    fn(next)
    collapsedPref.set([...next])
  }, [])
  const toggle = useCallback((id: number) => updateCollapsed((next) => (next.has(id) ? next.delete(id) : next.add(id))), [updateCollapsed])
  const expand = useCallback((id: number) => updateCollapsed((next) => next.delete(id)), [updateCollapsed])

  const isActive = useCallback((v: View) => view.kind === v.kind && (v.id === undefined || view.id === v.id), [view])
  const t = useT().feeds.sidebar

  return (
    <nav className="flex h-full flex-col bg-panel">
      <div className="flex items-center gap-2 px-4 h-14 border-b border-app shrink-0">
        <img src="/favicon.svg" alt="" className="size-7 rounded-lg" />
        <span className="font-semibold tracking-tight text-app flex-1">Fluxus</span>
        <IconButton label={t.sync} size="sm" onClick={() => void syncNow()} disabled={sync.status === 'syncing'}>
          <RefreshCw className={clsx('size-4', sync.status === 'syncing' && 'animate-spin')} />
        </IconButton>
        <IconButton label={t.addFeed} size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="size-4" />
        </IconButton>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        <NavItem icon={<LayoutDashboard className="size-4" />} label={t.dashboard} count={0} active={isActive({ kind: 'dashboard' })} onClick={() => onSelect({ kind: 'dashboard' })} />
        <NavItem icon={<Inbox className="size-4" />} label={t.all} count={totalUnread} active={isActive({ kind: 'all' })} onClick={() => onSelect({ kind: 'all' })} />
        <NavItem icon={<Rss className="size-4" />} label={t.unread} count={totalUnread} active={isActive({ kind: 'unread' })} onClick={() => onSelect({ kind: 'unread' })} />
        <NavItem icon={<Star className="size-4" />} label={t.starred} count={starredCount} muted active={isActive({ kind: 'starred' })} onClick={() => onSelect({ kind: 'starred' })} />

        <SubscriptionTree
          feeds={feeds}
          categories={categories}
          smartFeeds={smartFeeds}
          unreadByFeed={unreadByFeed}
          unreadBySmartFeed={bySmartFeed}
          collapsed={collapsed}
          onToggleCategory={toggle}
          onExpandCategory={expand}
          isActive={isActive}
          onSelect={onSelect}
          onEditFeed={onEditFeed}
          onEditCategory={onEditCategory}
          onEditSmartFeed={onEditSmartFeed}
          onAddFeed={() => setAddOpen(true)}
        />
      </div>

      <div className="border-t border-app px-2 py-2 flex items-center gap-1 shrink-0">
        <Link to="/settings" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted hover:text-app hover:bg-muted">
          <Settings className="size-4" /> {t.settings}
        </Link>
        <Link to="/abonnements" title={t.subscriptionsTitle} aria-label={t.subscriptions} className="flex items-center rounded-lg p-1.5 text-muted hover:text-app hover:bg-muted" data-testid="sidebar-subscriptions">
          <ListChecks className="size-4" />
        </Link>
        <div className="flex-1" />
        <SyncBadge />
      </div>

      <AddFeedDialog open={addOpen} onClose={() => setAddOpen(false)} categories={categories} />
    </nav>
  )
}

function NavItem({ icon, label, count, active, onClick, muted }: { icon: ReactNode; label: string; count: number; active: boolean; onClick: () => void; muted?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx('flex w-full items-center gap-2.5 px-4 py-2 text-sm text-left', active ? 'bg-accent-50 dark:bg-accent-900/20 text-accent-700 dark:text-accent-200 font-medium' : 'text-app hover:bg-muted')}
    >
      <span className={clsx(active ? 'text-accent-600 dark:text-accent-300' : 'text-muted')}>{icon}</span>
      <span className="flex-1 truncate">{label}</span>
      {count > 0 && <Count n={count} muted={muted} />}
    </button>
  )
}

function SyncBadge() {
  const s = useSyncState()
  const t = useT().feeds.sidebar
  const label = s.status === 'syncing' ? t.syncing : s.status === 'offline' ? t.offline : s.status === 'error' ? t.syncError : s.lastSyncAt ? t.upToDate : ''
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted pr-2" title={s.error ?? (s.lastSyncAt ? t.lastSync(timeOf(new Date(s.lastSyncAt))) : '')}>
      <span className={clsx('size-1.5 rounded-full', s.status === 'idle' && 'bg-emerald-500', s.status === 'syncing' && 'bg-accent-500 animate-soft-pulse', s.status === 'offline' && 'bg-amber-500', s.status === 'error' && 'bg-red-500')} />
      {label}
      {s.pendingChanges > 0 && <span>· {t.pending(s.pendingChanges)}</span>}
    </span>
  )
}
