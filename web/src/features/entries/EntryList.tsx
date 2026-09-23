import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Check, CheckCheck, ChevronLeft, Circle, CircleCheck, Menu, Search, Settings2, Sparkles, Star, Undo2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, isOnline, unwrap, type EntryListItem, type Feed, type SmartFeed } from '../../api/client'
import type { View } from '../../app/view'
import { Button, EmptyState, IconButton, Spinner, useToast } from '../../components/ui'
import { db, toLocalEntry, type LocalEntry } from '../../db/db'
import { hostname, relativeDate } from '../../lib/format'
import { recordChange, syncNow } from '../../sync/engine'
import { FeedIcon } from '../feeds/Sidebar'
import { CONFIRM_THRESHOLD, markViewRead } from './markRead'
import { setViewMode } from './useViewMode'
import { DEFAULT_VIEW_MODE, LAYOUT_GROUPS, VIEW_MODE_SPECS, layoutOf, modesFor, sourceLabel, type ResolvedViewMode, type ViewMode } from './viewMode'

/** Présentations en pleine largeur (article dans une fenêtre). */
type FullDensity = Exclude<ViewMode, 'compact' | 'cards'>
type Variant = ViewMode

function isFullVariant(v: Variant): v is FullDensity {
  return layoutOf(v) === 'full'
}

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
  onEditSmartFeed?: (sf: SmartFeed) => void
  /** Présentation résolue de la vue (flux → dossier → défaut), calculée par `Layout`. */
  viewMode?: ResolvedViewMode
}

const PAGE = 60
const EMPTY_FEEDS: Feed[] = []

function titleFor(view: View, feeds: Map<number, Feed>, categoryTitle?: string, smartTitle?: string): string {
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
    case 'smart':
      return smartTitle ?? 'Flux intelligent'
    case 'search':
      return `Recherche : ${view.query}`
    default:
      return 'Tableau de bord'
  }
}

export function EntryList({ view, unreadOnly, selectedId, onSelect, onToggleUnread, onOpenSidebar, onSearch, onBack, onVisibleEntries, onEditSmartFeed, viewMode }: Props) {
  const [limit, setLimit] = useState(PAGE)
  const variant: Variant = viewMode?.mode ?? DEFAULT_VIEW_MODE
  const [densityOpen, setDensityOpen] = useState(false)
  const densityRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!densityOpen) return
    const onDown = (e: MouseEvent) => {
      if (!densityRef.current?.contains(e.target as Node)) setDensityOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [densityOpen])
  const [searchOpen, setSearchOpen] = useState(view.kind === 'search')
  const [searchText, setSearchText] = useState(view.query ?? '')
  const [remote, setRemote] = useState<{ items: EntryListItem[]; nextCursor: string | null; loading: boolean; loadingMore: boolean; error?: string } | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? EMPTY_FEEDS
  const feedMap = useMemo(() => new Map(feeds.map((f) => [f.id, f])), [feeds])
  const category = useLiveQuery(() => (view.kind === 'category' ? db.categories.get(view.id!) : undefined), [view.kind, view.id])
  const smartFeed = useLiveQuery(() => (view.kind === 'smart' ? db.smartFeeds.get(view.id!) : undefined), [view.kind, view.id])
  const toast = useToast()
  const [batchBusy, setBatchBusy] = useState(false)

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

  // Recherche : côté serveur quand on est en ligne (paginée par curseur), sinon filtre local sur le titre.
  const SEARCH_PAGE = 100
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
    setRemote({ items: [], nextCursor: null, loading: true, loadingMore: false })
    api()
      .GET('/api/entries', { params: { query: { q: view.query, limit: SEARCH_PAGE, status: unreadOnly ? 'unread' : 'all' } } })
      .then((r) => {
        if (cancelled) return
        const page = unwrap(r)
        setRemote({ items: page.items, nextCursor: page.next_cursor ?? null, loading: false, loadingMore: false })
      })
      .catch((err) => !cancelled && setRemote({ items: [], nextCursor: null, loading: false, loadingMore: false, error: err instanceof Error ? err.message : 'Erreur' }))
    return () => {
      cancelled = true
    }
  }, [view.kind, view.query, unreadOnly])

  /** Page suivante des résultats distants (bouton ou défilement). */
  const loadMoreRemote = useCallback(() => {
    if (!remote || !remote.nextCursor || remote.loading || remote.loadingMore || view.kind !== 'search' || !view.query) return
    const cursor = remote.nextCursor
    setRemote((cur) => (cur ? { ...cur, loadingMore: true } : cur))
    api()
      .GET('/api/entries', { params: { query: { q: view.query, limit: SEARCH_PAGE, status: unreadOnly ? 'unread' : 'all', cursor } } })
      .then((r) => {
        const page = unwrap(r)
        setRemote((cur) => {
          if (!cur || cur.nextCursor !== cursor) return cur // la recherche a changé entre-temps
          const seen = new Set(cur.items.map((e) => e.id))
          return { ...cur, items: [...cur.items, ...page.items.filter((e) => !seen.has(e.id))], nextCursor: page.next_cursor ?? null, loadingMore: false }
        })
      })
      .catch((err) => setRemote((cur) => (cur ? { ...cur, loadingMore: false, error: err instanceof Error ? err.message : 'Erreur' } : cur)))
  }, [remote, view.kind, view.query, unreadOnly])

  // Les résultats distants sont un instantané : l'état lu/favori vient de la base locale quand elle a l'article.
  const remoteIdsKey = remote?.items.map((e) => e.id).join(',') ?? ''
  const localForRemote = useLiveQuery(async () => {
    if (!remoteIdsKey) return new Map<number, LocalEntry>()
    const ids = remoteIdsKey.split(',').map(Number)
    const rows = await db.entries.bulkGet(ids)
    return new Map(rows.filter((r): r is LocalEntry => Boolean(r)).map((r) => [r.id, r]))
  }, [remoteIdsKey])

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
      case 'smart': {
        // Index multiEntry : pas de composé possible, tri mémoire borné par le nombre de correspondances.
        let c = db.entries.where('smart_feed_ids').equals(view.id!)
        if (wantUnread) c = c.and((e) => e.readFlag === 0)
        const arr = await c.toArray()
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

  const items: Array<LocalEntry | EntryListItem> = useMemo(() => {
    if (remote && !remote.loading && !remote.error) {
      return remote.items.map((e) => {
        const local = localForRemote?.get(e.id)
        return local ? { ...e, read: local.read, starred: local.starred, has_summary: local.has_summary || e.has_summary } : e
      })
    }
    return entries ?? []
  }, [remote, localForRemote, entries])

  useEffect(() => {
    onVisibleEntries(items.map((e) => e.id))
  }, [items, onVisibleEntries])

  // Garde l'article sélectionné visible quand on navigue au clavier. Une seule fois par sélection, mais
  // en réessayant tant que la ligne n'est pas encore rendue (lien profond : la liste arrive après l'article).
  const scrolledFor = useRef<number | null>(null)
  useEffect(() => {
    if (selectedId == null || scrolledFor.current === selectedId) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-entry-id="${selectedId}"]`)
    if (!el) return
    scrolledFor.current = selectedId
    el.scrollIntoView({ block: 'nearest' })
  }, [selectedId, items])

  // L'article sélectionné approche de la fin de la page chargée (fenêtre de lecture : « suivant » ne
  // doit pas buter sur la 60e carte) : on charge la page suivante sans attendre le défilement.
  useEffect(() => {
    if (selectedId == null) return
    const idx = items.findIndex((e) => e.id === selectedId)
    if (idx === -1 || idx < items.length - 3) return
    if (remote) {
      if (remote.nextCursor && !remote.loading && !remote.loadingMore) loadMoreRemote()
    } else if ((entries?.length ?? 0) >= limit) {
      setLimit((l) => l + PAGE)
    }
  }, [selectedId, items, remote, entries?.length, limit, loadMoreRemote])

  const onScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    if (el.scrollTop + el.clientHeight < el.scrollHeight - 600) return
    if (remote) loadMoreRemote()
    else if ((entries?.length ?? 0) >= limit) setLimit((l) => l + PAGE)
  }, [entries?.length, limit, remote, loadMoreRemote])

  /** Résultat de recherche absent de la base locale : on le récupère avant d'ouvrir le lecteur. */
  async function select(id: number) {
    if (remote && !(await db.entries.get(id))) {
      try {
        const full = unwrap(await api().GET('/api/entries/{entry_id}', { params: { path: { entry_id: id } } }))
        await db.entries.put(toLocalEntry(full))
      } catch {
        /* hors-ligne ou erreur : le lecteur affichera « introuvable » */
      }
    }
    onSelect(id)
  }

  /** Tout le périmètre de la vue (index Dexie + requête serveur ciblée) ; la recherche ne traite que l'affiché. */
  async function markAllRead() {
    const displayed = items.filter((e) => !e.read).map((e) => e.id)
    if (view.kind === 'search' && displayed.length === 0) return
    if (view.kind !== 'search') {
      // Le compteur de la barre latérale vaut pour tout le périmètre : on confirme au-delà d'un seuil.
      const scopeCount = unreadCount
      if (scopeCount > CONFIRM_THRESHOLD && !window.confirm(`Marquer ${scopeCount} articles comme lus ?`)) return
    }
    setBatchBusy(true)
    try {
      const res = await markViewRead(view, feeds, displayed)
      if (res.count > CONFIRM_THRESHOLD || res.mode === 'outbox') toast(res.mode === 'outbox' ? `${res.count} articles marqués, envoi à la prochaine synchronisation` : `${res.count} articles marqués comme lus`, 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBatchBusy(false)
    }
  }

  function chooseVariant(next: Variant | 'inherit') {
    setDensityOpen(false)
    if (viewMode) void setViewMode(viewMode.target, next)
  }

  const heading = titleFor(view, feedMap, category?.title, smartFeed?.name)
  // Non lus de tout le périmètre de la vue (pas seulement la page chargée) ; recherche : l'affiché.
  const scopeUnread = useLiveQuery(async () => {
    switch (view.kind) {
      case 'feed':
        return db.entries.where('[feed_id+readFlag+publishedTs]').between([view.id, 0, -Infinity], [view.id, 0, Infinity]).count()
      case 'category': {
        const ids = feeds.filter((f) => f.category_id === view.id).map((f) => f.id)
        const counts = await Promise.all(ids.map((id) => db.entries.where('[feed_id+readFlag+publishedTs]').between([id, 0, -Infinity], [id, 0, Infinity]).count()))
        return counts.reduce((a, b) => a + b, 0)
      }
      case 'smart':
        return db.entries.where('smart_feed_ids').equals(view.id!).and((e) => e.readFlag === 0).count()
      case 'starred':
        return db.entries.where('[starredFlag+publishedTs]').between([1, -Infinity], [1, Infinity]).and((e) => e.readFlag === 0).count()
      case 'all':
      case 'unread':
        return db.entries.where('readFlag').equals(0).count()
      default:
        return null
    }
  }, [view.kind, view.id, feeds])
  const unreadCount = view.kind === 'search' || scopeUnread == null ? items.filter((e) => !e.read).length : scopeUnread
  const currentFeed = view.kind === 'feed' ? feedMap.get(view.id!) : undefined
  const batchTarget = view.kind === 'smart' ? smartFeed?.name : currentFeed ? currentFeed.title || hostname(currentFeed.url) : undefined
  const unsummarized = view.kind === 'smart' || view.kind === 'feed' ? items.filter((e) => !e.has_summary).length : 0

  /** « Résumer les non résumés » d'un flux intelligent ou d'un flux : lot borné par le budget quotidien. */
  async function summarizeAll() {
    if (!batchTarget) return
    if (!isOnline()) {
      toast('Le résumé nécessite une connexion au serveur', 'error')
      return
    }
    const scope = unreadOnly ? 'non lus et non encore résumés' : 'non encore résumés'
    if (!window.confirm(`Résumer les articles ${scope} de « ${batchTarget} » ?\nLe budget quotidien de tokens limite le nombre de résumés lancés.`)) return
    setBatchBusy(true)
    try {
      const res =
        view.kind === 'smart' && smartFeed
          ? unwrap(await api().POST('/api/smart-feeds/{smart_feed_id}/summarize-all', { params: { path: { smart_feed_id: smartFeed.id } }, body: { unread_only: unreadOnly } }))
          : unwrap(await api().POST('/api/feeds/{feed_id}/summarize-all', { params: { path: { feed_id: view.id! } }, body: { unread_only: unreadOnly } }))
      toast(res.queued ? `${res.queued} résumé${res.queued > 1 ? 's' : ''} en cours${res.skipped_budget ? `, ${res.skipped_budget} au-delà du budget` : ''}` : res.skipped_budget ? 'Budget quotidien épuisé' : 'Rien à résumer', res.queued ? 'success' : 'info')
      window.setTimeout(() => void syncNow(), 30000)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBatchBusy(false)
    }
  }

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
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault()
                  setSearchOpen(false)
                  setSearchText('')
                  if (view.kind === 'search') onBack ? onBack() : onSearch('')
                }
              }}
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
        {batchTarget && (
          <IconButton label={unsummarized ? `Résumer les non résumés (${unsummarized} visibles)` : 'Résumer les non résumés'} size="sm" onClick={() => void summarizeAll()} disabled={batchBusy} data-testid="summarize-all">
            <Sparkles className={clsx('size-4', batchBusy && 'animate-pulse')} />
          </IconButton>
        )}
        {view.kind === 'smart' && smartFeed && (
          <>
            {onEditSmartFeed && (
              <IconButton label="Modifier ce flux intelligent" size="sm" onClick={() => onEditSmartFeed(smartFeed)} data-testid="edit-smartfeed">
                <Settings2 className="size-4" />
              </IconButton>
            )}
          </>
        )}
        <IconButton label={unreadOnly ? 'Afficher tout' : 'Non lus seulement'} active={unreadOnly} onClick={() => onToggleUnread(!unreadOnly)} size="sm">
          <Circle className={clsx('size-4', unreadOnly && 'fill-current')} />
        </IconButton>
        <div className="relative" ref={densityRef}>
          <IconButton label="Présentation de la liste" onClick={() => setDensityOpen((o) => !o)} active={densityOpen} size="sm" data-testid="density-menu" aria-haspopup="menu" aria-expanded={densityOpen} disabled={!viewMode}>
            <VariantIcon variant={variant} />
          </IconButton>
          {densityOpen && viewMode && (
            <div role="menu" aria-label="Présentation de la liste" className="absolute right-0 top-full z-20 mt-1 w-60 rounded-xl border border-app bg-panel p-1 shadow-lg text-sm">
              {LAYOUT_GROUPS.map((group) => (
                <div key={group.layout} role="group" aria-label={group.label}>
                  <p className="px-2 pb-0.5 pt-1.5 text-xs font-medium uppercase tracking-wide text-muted">{group.label}</p>
                  {modesFor(group.layout).map((id) => (
                    <button
                      key={id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={id === variant}
                      data-density={id}
                      onClick={() => chooseVariant(id)}
                      className={clsx('flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-muted', id === variant ? 'text-app font-medium' : 'text-muted')}
                    >
                      <VariantIcon variant={id} />
                      <span className="flex-1">{VIEW_MODE_SPECS[id].label}</span>
                      {id === variant && <Check className="size-4 text-accent-600 dark:text-accent-300" />}
                    </button>
                  ))}
                </div>
              ))}
              <div className="mt-1 border-t border-app px-2 pb-1 pt-2 text-xs text-muted" data-testid="view-mode-source">
                {sourceLabel(viewMode)}
              </div>
              {viewMode.source === 'own' && (
                <button type="button" role="menuitem" onClick={() => chooseVariant('inherit')} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-muted hover:bg-muted" data-testid="view-mode-inherit">
                  <Undo2 className="size-4" />
                  <span className="flex-1">{view.kind === 'feed' && currentFeed?.category_id != null ? 'Revenir à l’affichage du dossier' : 'Revenir à l’affichage par défaut'}</span>
                </button>
              )}
            </div>
          )}
        </div>
        <IconButton label={view.kind === 'search' ? 'Marquer les résultats affichés comme lus' : 'Tout marquer comme lu'} onClick={() => void markAllRead()} size="sm" disabled={unreadCount === 0 || batchBusy} data-testid="mark-all-read">
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
            title={view.kind === 'search' ? 'Aucun résultat' : view.kind === 'smart' && !unreadOnly ? 'Aucun article ne correspond pour l’instant' : unreadOnly || view.kind === 'unread' ? 'Tout est lu' : 'Aucun article'}
            hint={view.kind === 'search' ? undefined : unreadOnly ? 'Désactivez le filtre « non lus » pour revoir les articles.' : 'Les nouveaux articles apparaîtront ici après la prochaine synchronisation.'}
            action={unreadOnly && view.kind !== 'unread' ? <Button size="sm" onClick={() => onToggleUnread(false)}>Afficher tout</Button> : undefined}
          />
        )}
        <ul data-density={variant} className={VIEW_MODE_SPECS[variant].listClass || undefined}>
          {items.map((e) => (
            <EntryRow key={e.id} entry={e} feed={feedMap.get(e.feed_id)} selected={e.id === selectedId} density={variant} showFeed={view.kind !== 'feed'} onClick={() => void select(e.id)} />
          ))}
        </ul>
        {(entries?.length ?? 0) >= limit && !remote && (
          <div className="flex justify-center p-4">
            <Button size="sm" variant="ghost" onClick={() => setLimit((l) => l + PAGE)}>
              Charger plus
            </Button>
          </div>
        )}
        {remote && !remote.loading && remote.nextCursor && (
          <div className="flex justify-center p-4">
            <Button size="sm" variant="ghost" onClick={loadMoreRemote} disabled={remote.loadingMore}>
              {remote.loadingMore ? 'Chargement…' : 'Charger plus'}
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}

export function EntryRow({ entry, feed, selected, density, showFeed, onClick }: { entry: LocalEntry | EntryListItem; feed?: Feed; selected: boolean; density: Variant; showFeed: boolean; onClick: () => void }) {
  const source = feed?.title || hostname(entry.url)
  if (isFullVariant(density)) {
    return <FullRow entry={entry} feed={feed} source={source} selected={selected} variant={density} showFeed={showFeed} onClick={onClick} />
  }
  return (
    <li data-entry-id={entry.id}>
      <button
        type="button"
        data-entry-focus
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
              {!entry.read && <span className="size-2 rounded-full bg-accent-500 shrink-0" role="img" aria-label="Non lu" />}
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

/** Image de couverture (ou vignette YouTube) : masquée si absente ou en erreur. */
function Cover({ entry, className }: { entry: LocalEntry | EntryListItem; className: string }) {
  const [failed, setFailed] = useState(false)
  const src = entry.cover ?? (entry.youtube_id ? `https://i.ytimg.com/vi/${entry.youtube_id}/mqdefault.jpg` : null)
  if (!src || failed) return null
  return <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className={clsx('bg-muted object-cover', className)} />
}

function VariantIcon({ variant }: { variant: Variant }) {
  const Icon = VIEW_MODE_SPECS[variant].Icon
  return <Icon className="size-4" />
}

/** Actions rapides d'une ligne : favori et lu/non lu. Frères du bouton d'ouverture, au-dessus de son pseudo-élément étiré. */
function RowActions({ entry }: { entry: LocalEntry | EntryListItem }) {
  return (
    <span className="relative z-10 flex items-center gap-0.5 shrink-0">
      <IconButton label={entry.starred ? 'Retirer des favoris' : 'Ajouter aux favoris'} size="sm" active={entry.starred} onClick={() => void recordChange(entry.id, { starred: !entry.starred })}>
        <Star className={clsx('size-4', entry.starred && 'fill-amber-400 text-amber-400')} />
      </IconButton>
      <IconButton label={entry.read ? 'Marquer non lu' : 'Marquer lu'} size="sm" onClick={() => void recordChange(entry.id, { read: !entry.read })}>
        {entry.read ? <CircleCheck className="size-4" /> : <Circle className="size-4" />}
      </IconButton>
    </span>
  )
}

/**
 * Rendus « pleine largeur » : grille de cartes, magazine (vignette à gauche), liste, titres.
 * Le titre est le seul bouton d'ouverture ; un pseudo-élément l'étire sur toute la ligne (le conteneur
 * est `relative`), et les actions rapides sont des frères placés au-dessus : pas de contenu interactif
 * imbriqué, donc Entrée ou Espace sur « favori » n'ouvrent pas l'article. `data-entry-focus` est le
 * contrat que la fenêtre de lecture cible pour rendre le focus à la fermeture.
 */
function FullRow({ entry, feed, source, selected, variant, showFeed, onClick }: { entry: LocalEntry | EntryListItem; feed?: Feed; source: string; selected: boolean; variant: FullDensity; showFeed: boolean; onClick: () => void }) {
  const titleClass = clsx('leading-snug', entry.read ? 'text-muted font-normal' : 'text-app font-semibold')
  const meta = (
    <>
      {!entry.read && <span className="size-2 rounded-full bg-accent-500 shrink-0" role="img" aria-label="Non lu" />}
      {showFeed && feed && <FeedIcon feed={feed} className="size-3.5" />}
      {showFeed && <span className="truncate">{source}</span>}
      {entry.starred && <Star className="size-3.5 fill-amber-400 text-amber-400 shrink-0" />}
      {entry.has_summary && <Sparkles className="size-3.5 text-accent-500 shrink-0" />}
    </>
  )
  const titleButton = (
    <button
      type="button"
      data-entry-focus
      onClick={onClick}
      className="text-left focus:outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-accent-400 focus-visible:after:rounded-[inherit]"
    >
      {entry.title}
    </button>
  )
  const rowClass = clsx('group relative transition-colors', selected ? 'bg-accent-50 dark:bg-accent-900/20' : 'hover:bg-panel dark:hover:bg-slate-800/60')

  if (variant === 'grid') {
    return (
      <li data-entry-id={entry.id} className="min-w-0">
        <div className={clsx('group relative flex h-full flex-col overflow-hidden rounded-xl border bg-panel transition-colors', selected ? 'border-accent-400 ring-2 ring-accent-400' : 'border-app hover:border-accent-300 dark:hover:border-accent-700')}>
          <Cover entry={entry} className="aspect-video w-full" />
          <div className="flex flex-1 flex-col gap-1.5 p-4">
            <h3 className={clsx(titleClass, 'text-[15px] line-clamp-3')}>{titleButton}</h3>
            <div className="flex items-center gap-1.5 text-xs text-muted min-w-0">{meta}</div>
            {entry.excerpt && <p className="text-sm text-muted line-clamp-4">{entry.excerpt}</p>}
            <div className="mt-auto flex items-center justify-between pt-2 text-xs text-muted">
              <span className="tabular-nums">{relativeDate(entry.published_at)}</span>
              <RowActions entry={entry} />
            </div>
          </div>
        </div>
      </li>
    )
  }
  if (variant === 'magazine') {
    return (
      <li data-entry-id={entry.id}>
        <div className={clsx(rowClass, 'flex gap-4 border-b border-app px-4 py-3')}>
          <div className="w-40 shrink-0 overflow-hidden rounded-lg bg-muted aspect-[4/3] hidden sm:block">
            <Cover entry={entry} className="h-full w-full" />
          </div>
          <div className="flex-1 min-w-0 flex flex-col">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1 min-w-0">
              {meta}
              <span className="ml-auto shrink-0 tabular-nums">{relativeDate(entry.published_at)}</span>
            </div>
            <h3 className={clsx(titleClass, 'text-base line-clamp-2')}>{titleButton}</h3>
            {entry.excerpt && <p className="mt-1 text-sm text-muted line-clamp-2">{entry.excerpt}</p>}
            <div className="mt-auto flex justify-end pt-1">
              <RowActions entry={entry} />
            </div>
          </div>
        </div>
      </li>
    )
  }
  if (variant === 'titles') {
    // Sans actions rapides : la ligne entière est le bouton, comme en colonnes.
    return (
      <li data-entry-id={entry.id}>
        <button
          type="button"
          data-entry-focus
          onClick={onClick}
          className={clsx(rowClass, 'flex h-9 w-full items-center gap-2 border-b border-app px-4 text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-400')}
        >
          {!entry.read ? <span className="size-2 rounded-full bg-accent-500 shrink-0" role="img" aria-label="Non lu" /> : <span className="size-2 shrink-0" />}
          {showFeed && feed && <FeedIcon feed={feed} className="size-3.5" />}
          <span className={clsx('flex-1 truncate', titleClass)}>{entry.title}</span>
          {showFeed && <span className="hidden md:inline w-40 truncate text-xs text-muted">{source}</span>}
          {entry.starred && <Star className="size-3.5 fill-amber-400 text-amber-400 shrink-0" />}
          <span className="w-14 text-right text-xs text-muted tabular-nums shrink-0">{relativeDate(entry.published_at)}</span>
        </button>
      </li>
    )
  }
  // list
  return (
    <li data-entry-id={entry.id}>
      <div className={clsx(rowClass, 'flex gap-3 border-b border-app px-4 py-3')}>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 text-xs text-muted mb-1 min-w-0">
            {meta}
            <span className="ml-auto shrink-0 tabular-nums">{relativeDate(entry.published_at)}</span>
          </div>
          <h3 className={clsx(titleClass, 'text-[15px] line-clamp-2')}>{titleButton}</h3>
          {entry.excerpt && <p className="mt-1 text-sm text-muted line-clamp-2">{entry.excerpt}</p>}
        </div>
        <Cover entry={entry} className="hidden sm:block size-20 rounded-lg shrink-0" />
        <div className="hidden sm:flex items-start">
          <RowActions entry={entry} />
        </div>
      </div>
    </li>
  )
}
