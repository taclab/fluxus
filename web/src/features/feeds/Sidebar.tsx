import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { ChevronDown, ChevronRight, Folder, Inbox, Play, Plus, RefreshCw, Rss, Settings, Star } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { Category, Feed } from '../../api/client'
import { IconButton } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import { useSyncState } from '../../sync/useSync'
import { viewPath, type View } from '../../app/view'
import { AddFeedDialog } from './AddFeedDialog'

interface Props {
  view: View
  onSelect: (view: View) => void
  onEditFeed: (feed: Feed) => void
  onEditCategory: (category: Category | null) => void
}

const UNCATEGORIZED = -1

export function Sidebar({ view, onSelect, onEditFeed, onEditCategory }: Props) {
  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? []
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? []
  const unreadByFeed = useLiveQuery(async () => {
    const counts = new Map<number, number>()
    await db.entries.where('readFlag').equals(0).each((e) => counts.set(e.feed_id, (counts.get(e.feed_id) ?? 0) + 1))
    return counts
  }, []) ?? new Map<number, number>()
  const starredCount = useLiveQuery(() => db.entries.where('starredFlag').equals(1).count(), []) ?? 0
  const sync = useSyncState()
  const [addOpen, setAddOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('fluxus.collapsed') ?? '[]') as number[])
    } catch {
      return new Set()
    }
  })

  const totalUnread = useMemo(() => [...unreadByFeed.values()].reduce((a, b) => a + b, 0), [unreadByFeed])

  const groups = useMemo(() => {
    const byCat = new Map<number, Feed[]>()
    for (const f of feeds) {
      const key = f.category_id ?? UNCATEGORIZED
      if (!byCat.has(key)) byCat.set(key, [])
      byCat.get(key)!.push(f)
    }
    for (const list of byCat.values()) list.sort((a, b) => a.title.localeCompare(b.title, 'fr'))
    return byCat
  }, [feeds])

  function toggle(id: number) {
    const next = new Set(collapsed)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setCollapsed(next)
    localStorage.setItem('fluxus.collapsed', JSON.stringify([...next]))
  }

  const isActive = (v: View) => view.kind === v.kind && (v.id === undefined || view.id === v.id)

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
        <NavItem icon={<Inbox className="size-4" />} label="Tous les articles" count={totalUnread} active={isActive({ kind: 'all' })} onClick={() => onSelect({ kind: 'all' })} />
        <NavItem icon={<Rss className="size-4" />} label="Non lus" count={totalUnread} active={isActive({ kind: 'unread' })} onClick={() => onSelect({ kind: 'unread' })} />
        <NavItem icon={<Star className="size-4" />} label="Favoris" count={starredCount} muted active={isActive({ kind: 'starred' })} onClick={() => onSelect({ kind: 'starred' })} />

        <div className="mt-3 px-4 pb-1 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">Abonnements</span>
          <button type="button" onClick={() => onEditCategory(null)} className="text-[11px] text-muted hover:text-app" title="Nouvelle catégorie">
            + catégorie
          </button>
        </div>

        {categories.map((cat) => {
          const list = groups.get(cat.id) ?? []
          const count = list.reduce((a, f) => a + (unreadByFeed.get(f.id) ?? 0), 0)
          const open = !collapsed.has(cat.id)
          return (
            <div key={cat.id}>
              <div className={clsx('group flex items-center pr-2', isActive({ kind: 'category', id: cat.id }) && 'bg-accent-50 dark:bg-accent-900/20')}>
                <button type="button" onClick={() => toggle(cat.id)} className="pl-3 pr-1 py-2 text-muted hover:text-app" aria-label={open ? 'Replier' : 'Déplier'}>
                  {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                </button>
                <button
                  type="button"
                  onClick={() => onSelect({ kind: 'category', id: cat.id })}
                  onDoubleClick={() => onEditCategory(cat)}
                  className="flex flex-1 items-center gap-2 py-2 text-sm text-left min-w-0"
                >
                  <Folder className="size-4 text-muted shrink-0" />
                  <span className="truncate font-medium">{cat.title}</span>
                  {count > 0 && <Count n={count} />}
                </button>
              </div>
              {open && list.map((f) => <FeedItem key={f.id} feed={f} count={unreadByFeed.get(f.id) ?? 0} active={isActive({ kind: 'feed', id: f.id })} onClick={() => onSelect({ kind: 'feed', id: f.id })} onEdit={() => onEditFeed(f)} indent />)}
            </div>
          )
        })}

        {(groups.get(UNCATEGORIZED) ?? []).map((f) => (
          <FeedItem key={f.id} feed={f} count={unreadByFeed.get(f.id) ?? 0} active={isActive({ kind: 'feed', id: f.id })} onClick={() => onSelect({ kind: 'feed', id: f.id })} onEdit={() => onEditFeed(f)} />
        ))}

        {feeds.length === 0 && (
          <p className="px-4 py-6 text-sm text-muted">
            Aucun abonnement pour l’instant.{' '}
            <button type="button" className="text-accent-600 dark:text-accent-300 underline" onClick={() => setAddOpen(true)}>
              Ajouter un flux
            </button>{' '}
            ou importer un fichier OPML depuis les paramètres.
          </p>
        )}
      </div>

      <div className="border-t border-app px-2 py-2 flex items-center justify-between shrink-0">
        <Link to="/settings" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted hover:text-app hover:bg-muted">
          <Settings className="size-4" /> Paramètres
        </Link>
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

function FeedItem({ feed, count, active, onClick, onEdit, indent }: { feed: Feed; count: number; active: boolean; onClick: () => void; onEdit: () => void; indent?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      onDoubleClick={onEdit}
      onContextMenu={(e) => {
        e.preventDefault()
        onEdit()
      }}
      title={feed.last_error ? `Erreur : ${feed.last_error}` : feed.title}
      className={clsx(
        'group flex w-full items-center gap-2.5 py-1.5 pr-3 text-sm text-left min-w-0',
        indent ? 'pl-9' : 'pl-4',
        active ? 'bg-accent-50 dark:bg-accent-900/20 text-accent-700 dark:text-accent-200 font-medium' : 'text-app hover:bg-muted',
        feed.disabled && 'opacity-50',
      )}
    >
      <FeedIcon feed={feed} />
      <span className={clsx('flex-1 truncate', count === 0 && !active && 'text-muted')}>{feed.title || feed.url}</span>
      {feed.error_count > 2 && <span className="size-1.5 rounded-full bg-red-500 shrink-0" title={feed.last_error ?? 'Erreur'} />}
      {count > 0 && <Count n={count} />}
    </button>
  )
}

export function FeedIcon({ feed, className }: { feed: Feed; className?: string }) {
  const [failed, setFailed] = useState(false)
  if (feed.is_youtube) return <Play className={clsx('size-4 text-red-500 shrink-0 fill-current', className)} />
  if (!feed.icon_url || failed) return <Rss className={clsx('size-4 text-muted shrink-0', className)} />
  return <img src={feed.icon_url} alt="" onError={() => setFailed(true)} className={clsx('size-4 rounded-sm shrink-0 object-contain', className)} loading="lazy" referrerPolicy="no-referrer" />
}

function Count({ n, muted }: { n: number; muted?: boolean }) {
  return <span className={clsx('text-xs tabular-nums shrink-0', muted ? 'text-muted' : 'text-muted font-medium')}>{n > 999 ? '999+' : n}</span>
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

export { viewPath }
