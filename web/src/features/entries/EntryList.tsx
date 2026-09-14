import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { CheckCheck, ChevronLeft, Circle, LayoutList, Menu, Rows3, Search, Sparkles, Star, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, isOnline, unwrap, type EntryListItem, type Feed } from '../../api/client'
import type { View } from '../../app/view'
import { Button, EmptyState, IconButton, Spinner } from '../../components/ui'
import { db, type LocalEntry } from '../../db/db'
import { hostname, relativeDate } from '../../lib/format'
import { recordBulkRead } from '../../sync/engine'
import { FeedIcon } from '../feeds/Sidebar'

export type Density = 'compact' | 'cards'

interface Props {
  view: View
  unreadOnly: boolean
  selectedId: number | null
  onSelect: (id: number) => void
  onToggleUnread: (v: boolean) => void
  onOpenSidebar: () => void
  onSearch: (q: string) => void
  onBack?: () => void
  onVisibleEntries: (ids: number[]) => void
}

const PAGE = 60

function titleFor(view: View, feeds: Map<number, Feed>, categoryTitle?: string): string {
  switch (view.kind) {
    case 'all':
      return 'Tous les articles'
    case 'unread':
      return 'Non lus'
    case 'starred':
      return 'Favoris'
    case 'feed':
      return feeds.get(view.id!)?.title ?? 'Flux'
    case 'category':
      return categoryTitle ?? 'Catégorie'
    case 'search':
      return `Recherche : ${view.query}`
    default:
      return 'Tableau de bord'
  }
}

export function EntryList({ view, unreadOnly, selectedId, onSelect, onToggleUnread, onOpenSidebar, onSearch, onBack, onVisibleEntries }: Props) {
  const [limit, setLimit] = useState(PAGE)
  const [density, setDensity] = useState<Density>(() => (localStorage.getItem('fluxus.density') as Density) || 'compact')
  const [searchOpen, setSearchOpen] = useState(view.kind === 'search')
  const [searchText, setSearchText] = useState(view.query ?? '')
  const [remote, setRemote] = useState<{ items: EntryListItem[]; loading: boolean; error?: string } | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? []
  const feedMap = useMemo(() => new Map(feeds.map((f) => [f.id, f])), [feeds])
  const category = useLiveQuery(() => (view.kind === 'category' ? db.categories.get(view.id!) : undefined), [view.kind, view.id])

  useEffect(() => {
    setLimit(PAGE)
    listRef.current?.scrollTo({ top: 0 })
  }, [view.kind, view.id, view.query, unreadOnly])

  useEffect(() => {
    if (view.kind === 'search') {
      setSearchOpen(true)
      setSearchText(view.query ?? '')
    }
  }, [view.kind, view.query])

  // Recherche : côté serveur quand on est en ligne, sinon filtre local sur le titre.
  useEffect(() => {
    if (view.kind !== 'search' || !view.query) {
      setRemote(null)
      return
    }
    if (!isOnline()) {
      setRemote(null)
      return
    }
    let cancelled = false
    setRemote({ items: [], loading: true })
    api()
      .GET('/api/entries', { params: { query: { q: view.query, limit: 100, status: unreadOnly ? 'unread' : 'all' } } })
      .then((r) => {
        if (cancelled) return
        const page = unwrap(r)
        setRemote({ items: page.items, loading: false })
        // On met les résultats en cache local pour pouvoir les ouvrir.
        void Promise.all(page.items.map((i) => api().GET('/api/entries/{entry_id}', { params: { path: { entry_id: i.id } } }))).then(async (rs) => {
          const full = rs.map((x) => x.data).filter(Boolean)
          const { toLocalEntry } = await import('../../db/db')
          await db.entries.bulkPut(full.map((e) => toLocalEntry(e!)))
        })
      })
      .catch((err) => !cancelled && setRemote({ items: [], loading: false, error: err instanceof Error ? err.message : 'Erreur' }))
    return () => {
      cancelled = true
    }
  }, [view.kind, view.query, unreadOnly])

  const entries = useLiveQuery(async (): Promise<LocalEntry[]> => {
    const wantUnread = unreadOnly || view.kind === 'unread'
    let coll
    switch (view.kind) {
      case 'feed':
        coll = wantUnread ? db.entries.where('[feed_id+readFlag+publishedTs]').between([view.id, 0, -Infinity], [view.id, 0, Infinity]) : db.entries.where('[feed_id+publishedTs]').between([view.id, -Infinity], [view.id, Infinity])
        break
      case 'category': {
        const ids = feeds.filter((f) => f.category_id === view.id).map((f) => f.id)
        coll = db.entries.where('feed_id').anyOf(ids)
        if (wantUnread) coll = coll.and((e) => e.readFlag === 0)
        const arr = await coll.toArray()
        arr.sort((a, b) => b.publishedTs - a.publishedTs)
        return arr.slice(0, limit)
      }
      case 'starred':
        coll = db.entries.where('[starredFlag+publishedTs]').between([1, -Infinity], [1, Infinity])
        if (unreadOnly) coll = coll.and((e) => e.readFlag === 0)
        break
      case 'search': {
        const q = (view.query ?? '').toLowerCase()
        coll = db.entries.orderBy('publishedTs').filter((e) => e.title.toLowerCase().includes(q) || (e.excerpt ?? '').toLowerCase().includes(q))
        if (wantUnread) coll = coll.and((e) => e.readFlag === 0)
        break
      }
      default:
        coll = wantUnread ? db.entries.where('[readFlag+publishedTs]').between([0, -Infinity], [0, Infinity]) : db.entries.orderBy('publishedTs')
    }
    return coll.reverse().limit(limit).toArray()
  }, [view.kind, view.id, view.query, unreadOnly, limit, feeds])

  const items: Array<LocalEntry | EntryListItem> = remote && !remote.loading && !remote.error ? remote.items : entries ?? []

  useEffect(() => {
    onVisibleEntries(items.map((e) => e.id))
  }, [items, onVisibleEntries])

  // Garde l'article sélectionné visible quand on navigue au clavier.
  useEffect(() => {
    if (selectedId == null) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-entry-id="${selectedId}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  const onScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 600 && (entries?.length ?? 0) >= limit) {
      setLimit((l) => l + PAGE)
    }
  }, [entries?.length, limit])

  async function markAllRead() {
    const ids = items.filter((e) => !e.read).map((e) => e.id)
    if (ids.length === 0) return
    await recordBulkRead(ids, true)
  }

  function toggleDensity() {
    const next: Density = density === 'compact' ? 'cards' : 'compact'
    setDensity(next)
    localStorage.setItem('fluxus.density', next)
  }

  const heading = titleFor(view, feedMap, category?.title)
  const unreadCount = items.filter((e) => !e.read).length

  return (
    <section className="flex h-full flex-col bg-app">
      <header className="flex items-center gap-1 h-14 px-2 border-b border-app bg-panel shrink-0">
        {onBack ? (
          <IconButton label="Retour" onClick={onBack}>
            <ChevronLeft className="size-5" />
          </IconButton>
        ) : (
          <IconButton label="Menu" onClick={onOpenSidebar} className="lg:hidden">
            <Menu className="size-5" />
          </IconButton>
        )}
        {searchOpen ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (searchText.trim()) onSearch(searchText.trim())
            }}
          >
            <input
              autoFocus
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Rechercher…"
              className="h-9 flex-1 rounded-lg bg-muted px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent-400"
              data-search-input
            />
            <IconButton
              label="Fermer la recherche"
              size="sm"
              onClick={() => {
                setSearchOpen(false)
                setSearchText('')
                if (view.kind === 'search') onBack ? onBack() : onSearch('')
              }}
            >
              <X className="size-4" />
            </IconButton>
          </form>
        ) : (
          <>
            <h1 className="flex-1 truncate px-2 font-semibold text-app">
              {heading}
              {unreadCount > 0 && <span className="ml-2 text-sm font-normal text-muted tabular-nums">{unreadCount}</span>}
            </h1>
            <IconButton label="Rechercher" onClick={() => setSearchOpen(true)} size="sm">
              <Search className="size-4" />
            </IconButton>
          </>
        )}
        <IconButton label={unreadOnly ? 'Afficher tout' : 'Non lus seulement'} active={unreadOnly} onClick={() => onToggleUnread(!unreadOnly)} size="sm">
          <Circle className={clsx('size-4', unreadOnly && 'fill-current')} />
        </IconButton>
        <IconButton label={density === 'compact' ? 'Vue cartes' : 'Vue compacte'} onClick={toggleDensity} size="sm" className="hidden sm:inline-flex">
          {density === 'compact' ? <Rows3 className="size-4" /> : <LayoutList className="size-4" />}
        </IconButton>
        <IconButton label="Tout marquer comme lu" onClick={() => void markAllRead()} size="sm" disabled={unreadCount === 0}>
          <CheckCheck className="size-4" />
        </IconButton>
      </header>

      <div ref={listRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
        {remote?.loading && (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        )}
        {remote?.error && <p className="p-4 text-sm text-red-600">{remote.error}</p>}
        {!remote?.loading && items.length === 0 && (
          <EmptyState
            icon={<CheckCheck className="size-10" />}
            title={view.kind === 'search' ? 'Aucun résultat' : unreadOnly || view.kind === 'unread' ? 'Tout est lu' : 'Aucun article'}
            hint={view.kind === 'search' ? undefined : unreadOnly ? 'Désactivez le filtre « non lus » pour revoir les articles.' : 'Les nouveaux articles apparaîtront ici après la prochaine synchronisation.'}
            action={unreadOnly && view.kind !== 'unread' ? <Button size="sm" onClick={() => onToggleUnread(false)}>Afficher tout</Button> : undefined}
          />
        )}
        <ul className={clsx(density === 'cards' && 'p-2 space-y-2')}>
          {items.map((e) => (
            <EntryRow key={e.id} entry={e} feed={feedMap.get(e.feed_id)} selected={e.id === selectedId} density={density} showFeed={view.kind !== 'feed'} onClick={() => onSelect(e.id)} />
          ))}
        </ul>
        {(entries?.length ?? 0) >= limit && !remote && (
          <div className="flex justify-center p-4">
            <Button size="sm" variant="ghost" onClick={() => setLimit((l) => l + PAGE)}>
              Charger plus
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}

export function EntryRow({ entry, feed, selected, density, showFeed, onClick }: { entry: LocalEntry | EntryListItem; feed?: Feed; selected: boolean; density: Density; showFeed: boolean; onClick: () => void }) {
  const source = feed?.title || hostname(entry.url)
  return (
    <li data-entry-id={entry.id}>
      <button
        type="button"
        onClick={onClick}
        className={clsx(
          'group w-full text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-400',
          density === 'compact' ? 'px-4 py-3 border-b border-app' : 'p-4 rounded-xl border border-app bg-panel',
          selected ? 'bg-accent-50 dark:bg-accent-900/20' : 'hover:bg-panel dark:hover:bg-slate-800/60',
        )}
      >
        <div className="flex gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-xs text-muted mb-1">
              {!entry.read && <span className="size-2 rounded-full bg-accent-500 shrink-0" aria-label="Non lu" />}
              {showFeed && feed && <FeedIcon feed={feed} className="size-3.5" />}
              {showFeed && <span className="truncate">{source}</span>}
              <span className="ml-auto shrink-0 tabular-nums">{relativeDate(entry.published_at)}</span>
              {entry.starred && <Star className="size-3.5 fill-amber-400 text-amber-400 shrink-0" />}
              {entry.has_summary && <Sparkles className="size-3.5 text-accent-500 shrink-0" />}
            </div>
            <h3 className={clsx('text-[15px] leading-snug', entry.read ? 'text-muted font-normal' : 'text-app font-semibold', density === 'compact' ? 'line-clamp-2' : 'line-clamp-3')}>{entry.title}</h3>
            {(density === 'cards' || !entry.read) && entry.excerpt && <p className={clsx('mt-1 text-sm text-muted', density === 'compact' ? 'line-clamp-1' : 'line-clamp-3')}>{entry.excerpt}</p>}
          </div>
          {entry.cover && density === 'cards' && <img src={entry.cover} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-20 rounded-lg object-cover shrink-0 bg-muted" />}
          {entry.youtube_id && density === 'compact' && <img src={`https://i.ytimg.com/vi/${entry.youtube_id}/mqdefault.jpg`} alt="" loading="lazy" className="w-24 h-14 rounded-md object-cover shrink-0 bg-muted" />}
        </div>
      </button>
    </li>
  )
}
