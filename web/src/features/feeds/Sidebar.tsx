import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Inbox, LayoutDashboard, ListChecks, Plus, RefreshCw, Rss, Settings, Star } from 'lucide-react'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { Category, Feed, SmartFeed } from '../../api/client'
import { ThemeToggle } from '../../components/ThemeToggle'
import { IconButton } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import { useSyncState } from '../../sync/useSync'
import { viewPath, type View } from '../../app/view'
import { AddFeedDialog } from './AddFeedDialog'
import { Count, FeedIcon } from './FeedIcon'
import { SubscriptionTree } from './SubscriptionTree'
import { useUnreadCountsFull } from './useUnreadCounts'

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
const COLLAPSED_KEY = 'fluxus.collapsed'

export function Sidebar({ view, onSelect, onEditFeed, onEditCategory, onEditSmartFeed }: Props) {
  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? EMPTY_FEEDS
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? EMPTY_CATEGORIES
  const { byFeed: unreadByFeed, bySmartFeed } = useUnreadCountsFull()
  const smartFeeds = useLiveQuery(() => db.smartFeeds.orderBy('position').toArray(), []) ?? EMPTY_SMART
  const starredCount = useLiveQuery(() => db.entries.where('starredFlag').equals(1).count(), []) ?? 0
  const sync = useSyncState()
  const [addOpen, setAddOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '[]') as number[])
    } catch {
      return new Set()
    }
  })

  const totalUnread = useMemo(() => [...unreadByFeed.values()].reduce((a, b) => a + b, 0), [unreadByFeed])

  const updateCollapsed = useCallback((fn: (next: Set<number>) => void) => {
    setCollapsed((cur) => {
      const next = new Set(cur)
      fn(next)
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]))
      return next
    })
  }, [])
  const toggle = useCallback((id: number) => updateCollapsed((next) => (next.has(id) ? next.delete(id) : next.add(id))), [updateCollapsed])
  const expand = useCallback((id: number) => updateCollapsed((next) => next.delete(id)), [updateCollapsed])

  const isActive = useCallback((v: View) => view.kind === v.kind && (v.id === undefined || view.id === v.id), [view])

  return (
    <nav className="flex h-full flex-col bg-panel">
      <div className="flex items-center gap-2 px-4 h-14 border-b border-app shrink-0">
        <img src="/favicon.svg" alt="" className="size-7 rounded-lg" />
        <span className="font-semibold tracking-tight text-app flex-1">Fluxus</span>
        <IconButton label="Synchroniser" size="sm" onClick={() => void syncNow()} disabled={sync.status === 'syncing'}>
          <RefreshCw className={clsx('size-4', sync.status === 'syncing' && 'animate-spin')} />
        </IconButton>
        <IconButton label="Ajouter un flux" size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="size-4" />
        </IconButton>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        <NavItem icon={<LayoutDashboard className="size-4" />} label="Tableau de bord" count={0} active={isActive({ kind: 'dashboard' })} onClick={() => onSelect({ kind: 'dashboard' })} />
        <NavItem icon={<Inbox className="size-4" />} label="Tous les articles" count={totalUnread} active={isActive({ kind: 'all' })} onClick={() => onSelect({ kind: 'all' })} />
        <NavItem icon={<Rss className="size-4" />} label="Non lus" count={totalUnread} active={isActive({ kind: 'unread' })} onClick={() => onSelect({ kind: 'unread' })} />
        <NavItem icon={<Star className="size-4" />} label="Favoris" count={starredCount} muted active={isActive({ kind: 'starred' })} onClick={() => onSelect({ kind: 'starred' })} />

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
          <Settings className="size-4" /> Paramètres
        </Link>
        <Link to="/abonnements" title="Abonnements : gérer tous les flux" aria-label="Abonnements" className="flex items-center rounded-lg p-1.5 text-muted hover:text-app hover:bg-muted" data-testid="sidebar-subscriptions">
          <ListChecks className="size-4" />
        </Link>
        <ThemeToggle />
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
  const label = s.status === 'syncing' ? 'Synchronisation…' : s.status === 'offline' ? 'Hors ligne' : s.status === 'error' ? 'Erreur de sync' : s.lastSyncAt ? 'À jour' : ''
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted pr-2" title={s.error ?? (s.lastSyncAt ? `Dernière sync : ${new Date(s.lastSyncAt).toLocaleTimeString('fr-FR')}` : '')}>
      <span className={clsx('size-1.5 rounded-full', s.status === 'idle' && 'bg-emerald-500', s.status === 'syncing' && 'bg-accent-500 animate-soft-pulse', s.status === 'offline' && 'bg-amber-500', s.status === 'error' && 'bg-red-500')} />
      {label}
      {s.pendingChanges > 0 && <span>· {s.pendingChanges} en attente</span>}
    </span>
  )
}

export { FeedIcon, viewPath }
