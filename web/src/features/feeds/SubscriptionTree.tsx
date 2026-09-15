/**
 * Sections « Flux intelligents » et « Abonnements » de la barre latérale, réorganisables au clic long :
 * ordre de la racine (dossiers et flux sans dossier, au même niveau), ordre des flux dans un dossier,
 * entrée et sortie d'un dossier, ordre des flux intelligents. Un seul DndContext ; la stratégie neutre
 * réordonne réellement un brouillon pendant le survol (même motif que le tableau de bord), puis le
 * nouvel ordre est écrit dans Dexie et envoyé au serveur en une requête.
 */

import { closestCenter, DndContext, DragOverlay, MouseSensor, pointerWithin, TouchSensor, useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragOverEvent, type DragStartEvent } from '@dnd-kit/core'
import { SortableContext, useSortable, type SortingStrategy } from '@dnd-kit/sortable'
import { getEventCoordinates } from '@dnd-kit/utilities'
import clsx from 'clsx'
import { ChevronDown, ChevronRight, Folder, FolderOpen, Sparkles, Unlink } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'

import { api, isOnline, unwrap, type Category, type Feed, type SmartFeed } from '../../api/client'
import { useToast } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import type { View } from '../../app/view'
import { Count, FeedIcon } from './FeedIcon'
import { buildDraft, canDropOn, diffDraft, dragId, moveInDraft, parseDragId, type Draft, type DragRef, type HeaderZone, type ReorderChanges } from './sidebarOrder'

const noTransform: SortingStrategy = () => null
/** Durée du clic long avant la prise (souris et tactile). */
const HOLD_MS = 300
/** Survol d'un dossier replié avant qu'il ne s'ouvre pour y viser une place précise. */
const EXPAND_MS = 600
/** Part haute d'un en-tête de dossier où un flux se dépose « avant » le dossier, à la racine, plutôt que dedans. */
const BEFORE_ZONE = 0.35
/** Style anti-sélection pendant le clic long (iOS ignore user-select sur le callout). */
const holdStyle: CSSProperties = { WebkitTouchCallout: 'none', touchAction: 'manipulation' }

interface Props {
  feeds: Feed[]
  categories: Category[]
  smartFeeds: SmartFeed[]
  unreadByFeed: Map<number, number>
  unreadBySmartFeed: Map<number, number>
  collapsed: Set<number>
  onToggleCategory: (id: number) => void
  onExpandCategory: (id: number) => void
  isActive: (view: View) => boolean
  onSelect: (view: View) => void
  onEditFeed: (feed: Feed) => void
  onEditCategory: (category: Category | null) => void
  onEditSmartFeed: (smartFeed: SmartFeed | null) => void
  onAddFeed: () => void
}

export function SubscriptionTree({ feeds, categories, smartFeeds, unreadByFeed, unreadBySmartFeed, collapsed, onToggleCategory, onExpandCategory, isActive, onSelect, onEditFeed, onEditCategory, onEditSmartFeed, onAddFeed }: Props) {
  const toast = useToast()
  const base = useMemo(() => buildDraft(categories, feeds, smartFeeds), [categories, feeds, smartFeeds])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const [headerZone, setHeaderZone] = useState<HeaderZone>('into')
  const activeRef = useRef<string | null>(null)
  const startRef = useRef<Draft | null>(null)
  /** Dernier survol appliqué (cible + zone) : onDragMove ne rejoue rien tant qu'il ne change pas. */
  const lastApplied = useRef<string | null>(null)
  const pendingHeader = useRef<DragRef | null>(null)
  const modelRef = useRef<Draft | null>(null)
  const expandTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const feedById = useMemo(() => new Map(feeds.map((f) => [f.id, f])), [feeds])
  const catById = useMemo(() => new Map(categories.map((c) => [c.id, c])), [categories])
  const smartById = useMemo(() => new Map(smartFeeds.map((s) => [s.id, s])), [smartFeeds])
  const model = draft ?? base
  useEffect(() => {
    modelRef.current = model
  }, [model])

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { delay: HOLD_MS, tolerance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: HOLD_MS, tolerance: 6 } }),
  )

  // Une fois le dépôt reflété par Dexie, on relâche le brouillon (sinon l'ancien ordre réapparaîtrait un instant).
  useEffect(() => {
    if (draft === null || activeId !== null) return
    if (diffDraft(base, draft, categories, feeds, smartFeeds) === null) setDraft(null)
  }, [base, draft, activeId, categories, feeds, smartFeeds])

  const clearExpandTimer = () => {
    if (expandTimer.current) clearTimeout(expandTimer.current)
    expandTimer.current = null
    pendingHeader.current = null
  }

  /**
   * Cible = l'élément sous le pointeur (la zone haute/basse d'un en-tête dépend du pointeur, pas du
   * centre de l'élément saisi), sinon le centre le plus proche ; filtrée par les règles de dépôt.
   */
  const collision = useCallback<CollisionDetection>((args) => {
    const active = parseDragId(args.active.id)
    const model = modelRef.current
    if (!active || !model) return []
    const containers = args.droppableContainers.filter((c) => {
      const ref = parseDragId(c.id)
      return ref !== null && canDropOn(model, active, ref)
    })
    const within = pointerWithin({ ...args, droppableContainers: containers })
    return within.length ? within : closestCenter({ ...args, droppableContainers: containers })
  }, [])

  /** Zone d'un en-tête de dossier sous le pointeur : haut → « avant, à la racine », sinon « dedans ». */
  const zoneFor = (e: DragMoveEvent | DragOverEvent): HeaderZone => {
    if (!e.over) return 'into'
    const start = e.activatorEvent ? getEventCoordinates(e.activatorEvent) : null
    if (!start) return 'into'
    const y = start.y + e.delta.y
    const r = e.over.rect
    return y < r.top + r.height * BEFORE_ZONE ? 'before' : 'into'
  }

  const onDragStart = useCallback(
    (e: DragStartEvent) => {
      const id = String(e.active.id)
      // Le filet du dépôt précédent ne doit pas vider le brouillon en plein glisser.
      if (settleTimer.current) clearTimeout(settleTimer.current)
      settleTimer.current = null
      activeRef.current = id
      startRef.current = base
      lastApplied.current = null
      setActiveId(id)
      setOverId(null)
      setHeaderZone('into')
      setDraft(base)
    },
    [base],
  )

  /** Applique le survol courant (cible + zone) au brouillon ; commun à onDragOver et onDragMove. */
  const applyHover = useCallback(
    (e: DragOverEvent | DragMoveEvent) => {
      const over = e.over ? String(e.over.id) : null
      setOverId(over)
      if (!over || over === String(e.active.id)) return
      const a = parseDragId(e.active.id)
      const o = parseDragId(over)
      if (!a || !o) return
      const zone: HeaderZone = a.kind === 'feed' && o.kind === 'cat' ? zoneFor(e) : 'into'
      setHeaderZone(zone)
      const key = `${over}|${zone}`
      if (key === lastApplied.current) return
      lastApplied.current = key
      clearExpandTimer()
      if (a.kind === 'feed' && o.kind === 'cat' && zone === 'into' && collapsed.has(o.id)) {
        // Dossier replié : on attend un peu avant de l'ouvrir et d'y déposer le flux en tête.
        pendingHeader.current = o
        expandTimer.current = setTimeout(() => {
          expandTimer.current = null
          pendingHeader.current = null
          onExpandCategory(o.id)
          setDraft((cur) => (cur ? (moveInDraft(cur, a, o, 'into') ?? cur) : cur))
        }, EXPAND_MS)
        return
      }
      setDraft((cur) => (cur ? (moveInDraft(cur, a, o, zone) ?? cur) : cur))
    },
    [collapsed, onExpandCategory],
  )

  const commit = useCallback(
    async (changes: ReorderChanges) => {
      if (!isOnline()) {
        toast('Réorganisation impossible hors connexion', 'error')
        setDraft(null)
        return
      }
      try {
        await db.transaction('rw', db.categories, db.feeds, db.smartFeeds, async () => {
          if (changes.categories.length) await db.categories.bulkPut(changes.categories)
          if (changes.feeds.length) await db.feeds.bulkPut(changes.feeds)
          if (changes.smartFeeds.length) await db.smartFeeds.bulkPut(changes.smartFeeds)
        })
        unwrap(await api().POST('/api/subscriptions/reorder', { body: changes.body }))
      } catch (err) {
        toast(err instanceof Error ? err.message : 'Réorganisation impossible', 'error')
        setDraft(null)
      }
      void syncNow()
    },
    [toast],
  )

  const finish = useCallback(
    (e: DragEndEvent | null) => {
      clearExpandTimer()
      const start = startRef.current
      let final = draft
      if (e && final && pendingHeader.current) {
        // Dépôt sur l'en-tête d'un dossier replié avant la fin de l'attente : on l'applique quand même.
        const a = parseDragId(e.active.id)
        if (a) final = moveInDraft(final, a, pendingHeader.current, 'into') ?? final
      }
      pendingHeader.current = null
      activeRef.current = null
      setActiveId(null)
      setOverId(null)
      const changes = e && start && final ? diffDraft(start, final, categories, feeds, smartFeeds) : null
      if (changes) {
        setDraft(final)
        void commit(changes)
        // Filet : si Dexie ne reflète jamais le dépôt (erreur silencieuse), on relâche le brouillon.
        if (settleTimer.current) clearTimeout(settleTimer.current)
        settleTimer.current = setTimeout(() => {
          settleTimer.current = null
          if (!activeRef.current) setDraft(null)
        }, 3000)
      } else {
        setDraft(null)
      }
      startRef.current = null
    },
    [draft, categories, feeds, smartFeeds, commit],
  )

  useEffect(() => () => {
    clearExpandTimer()
    if (settleTimer.current) clearTimeout(settleTimer.current)
  }, [])

  const dragging = activeId !== null
  const activeRefParsed = activeId ? parseDragId(activeId) : null
  const draggingFeed = activeRefParsed?.kind === 'feed'

  // Tous les identifiants visibles : la stratégie est neutre, seul le re-mesurage des cibles en dépend.
  const items = useMemo(() => {
    const out: string[] = model.smartFeeds.map((id) => dragId({ kind: 'smart', id }))
    for (const item of model.root) {
      out.push(dragId(item))
      if (item.kind === 'cat' && !collapsed.has(item.id)) for (const fid of model.feeds.get(item.id) ?? []) out.push(dragId({ kind: 'feed', id: fid }))
    }
    return out
  }, [model, collapsed])

  const guard = (fn: () => void) => () => {
    if (!activeRef.current) fn()
  }

  const announcements = {
    onDragStart: ({ active }: { active: { id: string | number } }) => `${labelFor(String(active.id))} saisi.`,
    onDragOver: ({ over }: { over: { id: string | number } | null }) => (over ? `Déplacé vers ${labelFor(String(over.id))}.` : 'Hors de la liste.'),
    onDragEnd: ({ over }: { over: { id: string | number } | null }) => (over ? 'Déposé.' : 'Déplacement annulé.'),
    onDragCancel: () => 'Déplacement annulé.',
  }

  function labelFor(id: string): string {
    const ref = parseDragId(id)
    if (!ref) return id
    if (ref.kind === 'cat') return `le dossier ${catById.get(ref.id)?.title ?? ''}`
    if (ref.kind === 'feed') return `le flux ${feedById.get(ref.id)?.title ?? ''}`
    if (ref.kind === 'smart') return `le flux intelligent ${smartById.get(ref.id)?.name ?? ''}`
    return ''
  }

  return (
    <DndContext sensors={sensors} collisionDetection={collision} onDragStart={onDragStart} onDragOver={applyHover} onDragMove={applyHover} onDragEnd={finish} onDragCancel={() => finish(null)} accessibility={{ announcements }}>
      <SortableContext items={items} strategy={noTransform}>
        <div className="mt-3 px-4 pb-1 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">Flux intelligents</span>
          <button type="button" onClick={() => onEditSmartFeed(null)} className="text-[11px] text-muted hover:text-app" title="Nouveau flux intelligent" data-testid="new-smartfeed">
            + nouveau
          </button>
        </div>
        {smartFeeds.length === 0 && <p className="px-4 pb-2 text-xs text-muted">Une recherche enregistrée à travers vos flux, par sujet.</p>}
        {model.smartFeeds.map((id) => {
          const sf = smartById.get(id)
          if (!sf) return null
          const orphan = (sf.scope_kind === 'category' && !catById.has(sf.scope_id ?? -1)) || (sf.scope_kind === 'feed' && !feedById.has(sf.scope_id ?? -1))
          return (
            <SmartRow key={id} smartFeed={sf} orphan={orphan} count={unreadBySmartFeed.get(id) ?? 0} active={isActive({ kind: 'smart', id })} dimmed={activeId === dragId({ kind: 'smart', id })} onClick={() => onSelect({ kind: 'smart', id })} onEdit={guard(() => onEditSmartFeed(sf))} />
          )
        })}

        <div className="mt-3 px-4 pb-1 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">Abonnements</span>
          <button type="button" onClick={() => onEditCategory(null)} className="text-[11px] text-muted hover:text-app" title="Nouvelle catégorie">
            + catégorie
          </button>
        </div>

        {model.root.map((item) => {
          if (item.kind === 'feed') {
            const f = feedById.get(item.id)
            return f ? (
              <div key={dragId(item)} data-root-item={dragId(item)}>
                <FeedRow feed={f} count={unreadByFeed.get(item.id) ?? 0} active={isActive({ kind: 'feed', id: item.id })} dimmed={activeId === dragId(item)} onClick={() => onSelect({ kind: 'feed', id: item.id })} onEdit={guard(() => onEditFeed(f))} />
              </div>
            ) : null
          }
          const cid = item.id
          const cat = catById.get(cid)
          if (!cat) return null
          const list = model.feeds.get(cid) ?? []
          const count = list.reduce((a, fid) => a + (unreadByFeed.get(fid) ?? 0), 0)
          const open = !collapsed.has(cid)
          const id = dragId(item)
          return (
            <div key={id} data-category-id={cid} data-root-item={id}>
              <CategoryRow
                category={cat}
                count={count}
                open={open}
                active={isActive({ kind: 'category', id: cid })}
                dimmed={activeId === id}
                target={draggingFeed && overId === id && headerZone === 'into'}
                onToggle={() => onToggleCategory(cid)}
                onClick={() => onSelect({ kind: 'category', id: cid })}
                onEdit={guard(() => onEditCategory(cat))}
              />
              {open &&
                list.map((fid) => {
                  const f = feedById.get(fid)
                  return f ? <FeedRow key={fid} feed={f} count={unreadByFeed.get(fid) ?? 0} active={isActive({ kind: 'feed', id: fid })} dimmed={activeId === dragId({ kind: 'feed', id: fid })} onClick={() => onSelect({ kind: 'feed', id: fid })} onEdit={guard(() => onEditFeed(f))} indent /> : null
                })}
            </div>
          )
        })}

        {feeds.length === 0 && (
          <p className="px-4 py-6 text-sm text-muted">
            Aucun abonnement pour l’instant.{' '}
            <button type="button" className="text-accent-600 dark:text-accent-300 underline" onClick={onAddFeed}>
              Ajouter un flux
            </button>{' '}
            ou importer un fichier OPML depuis les paramètres.
          </p>
        )}
      </SortableContext>
      <DragOverlay dropAnimation={null}>{dragging && activeRefParsed ? <OverlayCard item={activeRefParsed} feedById={feedById} catById={catById} smartById={smartById} /> : null}</DragOverlay>
    </DndContext>
  )
}

/* ---------- Lignes ---------- */

const rowActive = 'bg-accent-50 dark:bg-accent-900/20 text-accent-700 dark:text-accent-200 font-medium'

function SmartRow({ smartFeed: sf, orphan, count, active, dimmed, onClick, onEdit }: { smartFeed: SmartFeed; orphan: boolean; count: number; active: boolean; dimmed: boolean; onClick: () => void; onEdit: () => void }) {
  const { setNodeRef, listeners } = useSortable({ id: dragId({ kind: 'smart', id: sf.id }), transition: null })
  return (
    <button
      ref={setNodeRef}
      type="button"
      data-smartfeed-id={sf.id}
      onClick={onClick}
      onDoubleClick={onEdit}
      onContextMenu={(e) => {
        e.preventDefault()
        onEdit()
      }}
      title={orphan ? 'La catégorie ou le flux ciblé a disparu' : sf.name}
      style={holdStyle}
      className={clsx('flex w-full items-center gap-2.5 px-4 py-1.5 text-sm text-left min-w-0 select-none', active ? rowActive : 'text-app hover:bg-muted', dimmed && 'opacity-40')}
      {...listeners}
    >
      <Sparkles className={clsx('size-4 shrink-0', active ? 'text-accent-600 dark:text-accent-300' : 'text-accent-500/80')} />
      <span className={clsx('flex-1 truncate', count === 0 && !active && 'text-muted')}>{sf.name}</span>
      {orphan && <Unlink className="size-3.5 shrink-0 text-amber-500" />}
      {sf.auto_summary && <span className="size-1.5 shrink-0 rounded-full bg-accent-400" title="Résumé automatique" />}
      {count > 0 && <Count n={count} />}
    </button>
  )
}

function CategoryRow({ category, count, open, active, dimmed, target, onToggle, onClick, onEdit }: { category: Category; count: number; open: boolean; active: boolean; dimmed: boolean; target: boolean; onToggle: () => void; onClick: () => void; onEdit: () => void }) {
  const { setNodeRef, setActivatorNodeRef, listeners } = useSortable({ id: dragId({ kind: 'cat', id: category.id }), transition: null })
  return (
    <div ref={setNodeRef} className={clsx('group flex items-center pr-2 rounded-md', active && 'bg-accent-50 dark:bg-accent-900/20', dimmed && 'opacity-40', target && 'ring-2 ring-inset ring-accent-400 bg-accent-50/60 dark:bg-accent-900/30')}>
      <button type="button" onClick={onToggle} className="pl-3 pr-1 py-2 text-muted hover:text-app" aria-label={open ? 'Replier' : 'Déplier'}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
      </button>
      <button ref={setActivatorNodeRef} type="button" onClick={onClick} onDoubleClick={onEdit} style={holdStyle} className="flex flex-1 items-center gap-2 py-2 text-sm text-left min-w-0 select-none" {...listeners}>
        {target ? <FolderOpen className="size-4 text-accent-500 shrink-0" /> : <Folder className="size-4 text-muted shrink-0" />}
        <span className="truncate font-medium">{category.title}</span>
        {count > 0 && <Count n={count} />}
      </button>
    </div>
  )
}

function FeedRow({ feed, count, active, dimmed, onClick, onEdit, indent }: { feed: Feed; count: number; active: boolean; dimmed: boolean; onClick: () => void; onEdit: () => void; indent?: boolean }) {
  const { setNodeRef, listeners } = useSortable({ id: dragId({ kind: 'feed', id: feed.id }), transition: null })
  return (
    <button
      ref={setNodeRef}
      type="button"
      data-feed-id={feed.id}
      onClick={onClick}
      onDoubleClick={onEdit}
      onContextMenu={(e) => {
        e.preventDefault()
        onEdit()
      }}
      title={feed.last_error ? `Erreur : ${feed.last_error}` : feed.title}
      style={holdStyle}
      className={clsx(
        'group flex w-full items-center gap-2.5 py-1.5 pr-3 text-sm text-left min-w-0 select-none',
        indent ? 'pl-9' : 'pl-4',
        active ? rowActive : 'text-app hover:bg-muted',
        feed.disabled && 'opacity-50',
        dimmed && 'opacity-40',
      )}
      {...listeners}
    >
      <FeedIcon feed={feed} />
      <span className={clsx('flex-1 truncate', count === 0 && !active && 'text-muted')}>{feed.title || feed.url}</span>
      {feed.error_count > 2 && <span className="size-1.5 rounded-full bg-red-500 shrink-0" title={feed.last_error ?? 'Erreur'} />}
      {count > 0 && <Count n={count} />}
    </button>
  )
}

/** Fantôme compact sous le pointeur. */
function OverlayCard({ item: ref, feedById, catById, smartById }: { item: DragRef; feedById: Map<number, Feed>; catById: Map<number, Category>; smartById: Map<number, SmartFeed> }) {
  let icon: ReactNode = null
  let title = ''
  if (ref.kind === 'feed') {
    const f = feedById.get(ref.id)
    if (f) {
      icon = <FeedIcon feed={f} />
      title = f.title || f.url
    }
  } else if (ref.kind === 'cat') {
    icon = <Folder className="size-4 text-muted" />
    title = catById.get(ref.id)?.title ?? ''
  } else if (ref.kind === 'smart') {
    icon = <Sparkles className="size-4 text-accent-500" />
    title = smartById.get(ref.id)?.name ?? ''
  }
  return (
    <div className="flex items-center gap-2 rounded-lg border border-accent-300 bg-panel px-3 py-1.5 shadow-xl text-app text-sm w-56 cursor-grabbing">
      {icon}
      <span className="truncate font-medium">{title}</span>
    </div>
  )
}
