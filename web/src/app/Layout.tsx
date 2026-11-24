import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import type { Category, Feed, SmartFeed } from '../api/client'
import { Dialog, Kbd } from '../components/ui'
import { db } from '../db/db'
import { recordChange } from '../sync/engine'
import { Dashboard } from '../features/dashboard/Dashboard'
import { EntryList } from '../features/entries/EntryList'
import { useViewMode } from '../features/entries/useViewMode'
import { DEFAULT_VIEW_MODE, layoutOf } from '../features/entries/viewMode'
import { AddFeedDialog } from '../features/feeds/AddFeedDialog'
import { sharedUrl } from '../features/feeds/sharedUrl'
import { CategoryDialog, FeedDialog } from '../features/feeds/FeedDialog'
import { Sidebar } from '../features/feeds/Sidebar'
import { SmartFeedDialog } from '../features/smartfeeds/SmartFeedDialog'
import { Reader } from '../features/reader/Reader'
import { openExternal } from '../lib/links'
import { ReaderModal } from '../features/reader/ReaderModal'
import { useT } from '../i18n'
import type { Messages } from '../i18n'
import { useKeyboard } from './useKeyboard'
import { useView } from './view'

function shortcutHelp(t: Messages['app']['layout']): [string[], string][] {
  const s = t.shortcuts
  return [
    [['h'], s.dashboard],
    [['j', 'k'], s.nextPrev],
    [['m'], s.read],
    [['s'], s.star],
    [['v'], s.original],
    [['f'], s.full],
    [['r'], s.summarize],
    [['u'], s.unreadOnly],
    [['/'], s.search],
    [[t.escapeKey], s.back],
    [['?'], s.help],
  ]
}

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
  const t = useT().app.layout
  const { view, entryId, unreadOnly, setView, setEntry, setUnreadOnly, openEntryIn } = useView()
  const location = useLocation()
  const navigate = useNavigate()
  // « Partager → Fluxus » (share_target du manifeste) : tableau de bord en fond, ajout de flux prérempli.
  const sharing = location.pathname === '/partage'
  const shared = useMemo(() => (sharing ? sharedUrl(new URLSearchParams(location.search)) : ''), [sharing, location.search])
  const isDashboard = view.kind === 'dashboard'
  // Présentation propre à la vue (flux → dossier → défaut) ; elle fixe aussi la disposition :
  // « pleine largeur », la liste occupe tout et l'article s'ouvre dans une fenêtre.
  const viewMode = useViewMode(view)
  const mode = viewMode?.mode ?? DEFAULT_VIEW_MODE
  const fullList = layoutOf(mode) === 'full' && !isDashboard
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
    () => document.querySelector<HTMLInputElement>('[data-search-input]')?.focus() ?? document.querySelector<HTMLButtonElement>('[data-search-button]')?.click(),
    [],
  )
  const [helpOpen, setHelpOpen] = useState(false)
  const help = useCallback(() => setHelpOpen(true), [])
  const readerModalOpen = fullList && entryId !== null

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
      // Fenêtre de lecture ouverte : les flèches font défiler l'article (j / k changent d'article).
      ...(readerModalOpen ? {} : { ArrowDown: () => move(1), ArrowUp: () => move(-1) }),
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
    [move, current, entryId, setEntry, setUnreadOnly, unreadOnly, goHome, focusSearch, help, readerModalOpen],
  )
  useKeyboard(isDashboard ? dashboardShortcuts : shortcuts)

  // Mobile : une seule colonne à la fois. Pleine largeur : la liste reste toujours montée, l'article vit dans la fenêtre.
  // Première lecture de la présentation : rien n'est monté plutôt qu'une disposition qui basculerait aussitôt.
  const ready = viewMode !== undefined
  const showList = ready && (fullList || isTablet || entryId === null)
  const showReader = ready && !fullList && (isTablet || entryId !== null)

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
          <div className={clsx('fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] bg-app pt-[var(--sat)] pb-[var(--sab)] pl-[var(--sal)] border-r border-app shadow-xl transition-transform', sidebarOpen ? 'translate-x-0' : '-translate-x-full')}>
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
            viewMode={viewMode}
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

      {ready && fullList && entryId !== null && (
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

      {sharing && <AddFeedDialog open initialUrl={shared} categories={categories} onClose={() => navigate('/', { replace: true })} />}
      <FeedDialog feed={editFeed} categories={categories} onClose={() => setEditFeed(null)} />
      <CategoryDialog open={editCategory.open} category={editCategory.category} onClose={() => setEditCategory({ open: false, category: null })} />
      <SmartFeedDialog
        open={editSmart.open}
        smartFeed={editSmart.smartFeed}
        preset={editSmart.preset}
        onClose={() => setEditSmart((s) => ({ ...s, open: false }))}
        onSaved={(sf) => setView({ kind: 'smart', id: sf.id })}
      />
      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} title={t.shortcutsTitle}>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm" data-testid="shortcuts-help">
          {shortcutHelp(t).map(([keys, label]) => (
            <Fragment key={label}>
              <dt className="flex gap-1">
                {keys.map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
              </dt>
              <dd className="text-muted">{label}</dd>
            </Fragment>
          ))}
        </dl>
      </Dialog>
    </div>
  )
}
