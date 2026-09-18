import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type { Category, Feed, SmartFeed } from '../api/client'
import { db } from '../db/db'
import { recordChange } from '../sync/engine'
import { Dashboard } from '../features/dashboard/Dashboard'
import { EntryList } from '../features/entries/EntryList'
import { CategoryDialog, FeedDialog } from '../features/feeds/FeedDialog'
import { Sidebar } from '../features/feeds/Sidebar'
import { SmartFeedDialog } from '../features/smartfeeds/SmartFeedDialog'
import { openExternal, Reader } from '../features/reader/Reader'
import { ReaderModal } from '../features/reader/ReaderModal'
import { useListLayout } from './display'
import { useKeyboard } from './useKeyboard'
import { useView } from './view'

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const handler = () => setMatches(mq.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [query])
  return matches
}

export function Layout() {
  const { view, entryId, unreadOnly, setView, setEntry, setUnreadOnly, openEntryIn } = useView()
  const isDashboard = view.kind === 'dashboard'
  const listLayout = useListLayout()
  // Disposition « pleine largeur » : la liste occupe tout, l'article s'ouvre dans une fenêtre.
  const fullList = listLayout === 'full' && !isDashboard
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const isTablet = useMediaQuery('(min-width: 768px)')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [editFeed, setEditFeed] = useState<Feed | null>(null)
  const [editCategory, setEditCategory] = useState<{ open: boolean; category: Category | null }>({ open: false, category: null })
  const [editSmart, setEditSmart] = useState<{ open: boolean; smartFeed: SmartFeed | null; preset: { scope_kind: 'feed' | 'category'; scope_id: number } | null }>({ open: false, smartFeed: null, preset: null })
  const openSmartEditor = useCallback((sf: SmartFeed | null, preset: { scope_kind: 'feed' | 'category'; scope_id: number } | null = null) => setEditSmart({ open: true, smartFeed: sf, preset }), [])
  const [summaryTick, setSummaryTick] = useState(0)
  const [fullTick, setFullTick] = useState(0)
  // Ordre des articles affichés par la liste (navigation j/k, ← →, position « n / total »). La liste le
  // renvoie à chaque émission de Dexie : on ne remplace l'état que si l'ordre a réellement changé.
  const [visible, setVisible] = useState<number[]>([])
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? []
  const current = useLiveQuery(() => (entryId ? db.entries.get(entryId) : undefined), [entryId])

  const onVisibleEntries = useCallback((ids: number[]) => {
    setVisible((prev) => (prev.length === ids.length && prev.every((v, i) => v === ids[i]) ? prev : ids))
  }, [])
  const visibleIndex = useMemo(() => (entryId ? visible.indexOf(entryId) : -1), [entryId, visible])

  const selectView = useCallback(
    (v: typeof view) => {
      setView(v)
      setSidebarOpen(false)
    },
    [setView],
  )

  const move = useCallback(
    (delta: 1 | -1) => {
      const ids = visible
      if (ids.length === 0) return
      const idx = entryId ? ids.indexOf(entryId) : -1
      const next = idx === -1 ? (delta === 1 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, idx + delta))
      if (ids[next] !== undefined) setEntry(ids[next], true)
    },
    [visible, entryId, setEntry],
  )

  const goHome = useCallback(() => setView({ kind: 'dashboard' }), [setView])
  const focusSearch = useCallback(
    () => document.querySelector<HTMLInputElement>('[data-search-input]')?.focus() ?? document.querySelector<HTMLButtonElement>('[aria-label="Rechercher"]')?.click(),
    [],
  )
  const help = useCallback(
    () => alert('Raccourcis : h accueil · j/k naviguer · m lu/non lu · s favori · v ouvrir · f article complet · r résumer · u non lus · / rechercher · Échap retour'),
    [],
  )

  // Sur le tableau de bord, pas d'article courant : on ne capture ni les flèches ni les touches d'article
  // (dnd-kit utilise les flèches pour déplacer un widget au clavier).
  const dashboardShortcuts = useMemo(
    () => ({ h: goHome, '/': focusSearch, '?': help, Escape: () => setSidebarOpen(false) }),
    [goHome, focusSearch, help],
  )

  const shortcuts = useMemo(
    () => ({
      h: goHome,
      j: () => move(1),
      k: () => move(-1),
      ArrowDown: () => move(1),
      ArrowUp: () => move(-1),
      m: () => current && void recordChange(current.id, { read: !current.read }),
      s: () => current && void recordChange(current.id, { starred: !current.starred }),
      v: () => openExternal(current?.url),
      f: () => current && setFullTick((t) => t + 1),
      r: () => current && setSummaryTick((t) => t + 1),
      u: () => setUnreadOnly(!unreadOnly),
      Escape: () => (entryId ? setEntry(null) : setSidebarOpen(false)),
      '/': focusSearch,
      '?': help,
    }),
    [move, current, entryId, setEntry, setUnreadOnly, unreadOnly, goHome, focusSearch, help],
  )
  useKeyboard(isDashboard ? dashboardShortcuts : shortcuts)

  // Mobile : une seule colonne à la fois. Pleine largeur : la liste reste toujours montée, l'article vit dans la fenêtre.
  const showList = fullList || isTablet || entryId === null
  const showReader = !fullList && (isTablet || entryId !== null)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Barre latérale : fixe sur grand écran, tiroir ailleurs */}
      {isDesktop ? (
        <div className="w-64 xl:w-72 shrink-0 border-r border-app">
          <Sidebar view={view} onSelect={selectView} onEditFeed={setEditFeed} onEditCategory={(c) => setEditCategory({ open: true, category: c })} onEditSmartFeed={(sf) => openSmartEditor(sf)} />
        </div>
      ) : (
        <>
          <div className={clsx('fixed inset-0 z-40 bg-black/40 transition-opacity', sidebarOpen ? 'opacity-100' : 'pointer-events-none opacity-0')} onClick={() => setSidebarOpen(false)} />
          <div className={clsx('fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] border-r border-app shadow-xl transition-transform', sidebarOpen ? 'translate-x-0' : '-translate-x-full')}>
            <Sidebar view={view} onSelect={selectView} onEditFeed={setEditFeed} onEditCategory={(c) => setEditCategory({ open: true, category: c })} onEditSmartFeed={(sf) => openSmartEditor(sf)} />
          </div>
        </>
      )}

      {isDashboard && (
        <div className="flex-1 min-w-0">
          <Dashboard onOpenSidebar={() => setSidebarOpen(true)} onOpenEntry={openEntryIn} onSearch={() => setView({ kind: 'search', query: '' })} />
        </div>
      )}

      {!isDashboard && showList && (
        <div className={clsx(fullList ? 'flex-1 min-w-0' : clsx('shrink-0 border-r border-app', isTablet ? 'w-[24rem] xl:w-[26rem]' : 'w-full'))}>
          <EntryList
            layout={fullList ? 'full' : 'split'}
            view={view}
            unreadOnly={unreadOnly}
            selectedId={entryId}
            onSelect={(id) => setEntry(id)}
            onToggleUnread={setUnreadOnly}
            onOpenSidebar={() => setSidebarOpen(true)}
            onSearch={(q) => (q ? setView({ kind: 'search', query: q }) : setView({ kind: 'all' }))}
            onVisibleEntries={onVisibleEntries}
            onEditSmartFeed={(sf) => openSmartEditor(sf)}
          />
        </div>
      )}

      {!isDashboard && showReader && (
        <div className="flex-1 min-w-0">
          <Reader
            entryId={entryId}
            onBack={isTablet ? undefined : () => setEntry(null)}
            summaryRequested={summaryTick}
            fullContentRequested={fullTick}
            onCreateSmartFeed={(feedId) => openSmartEditor(null, { scope_kind: 'feed', scope_id: feedId })}
          />
        </div>
      )}

      {fullList && entryId !== null && (
        <ReaderModal entryId={entryId} index={visibleIndex} total={visible.length} onClose={() => setEntry(null)} onMove={move}>
          <Reader
            entryId={entryId}
            onBack={() => setEntry(null)}
            summaryRequested={summaryTick}
            fullContentRequested={fullTick}
            onCreateSmartFeed={(feedId) => openSmartEditor(null, { scope_kind: 'feed', scope_id: feedId })}
          />
        </ReaderModal>
      )}

      <FeedDialog feed={editFeed} categories={categories} onClose={() => setEditFeed(null)} />
      <CategoryDialog open={editCategory.open} category={editCategory.category} onClose={() => setEditCategory({ open: false, category: null })} />
      <SmartFeedDialog
        open={editSmart.open}
        smartFeed={editSmart.smartFeed}
        preset={editSmart.preset}
        onClose={() => setEditSmart((s) => ({ ...s, open: false }))}
        onSaved={(sf) => setView({ kind: 'smart', id: sf.id })}
      />
    </div>
  )
}
