/**
 * Écran « Abonnements » : tous les flux en un tableau, réglages modifiables sur place et actions groupées.
 *
 * Les flux et dossiers viennent de Dexie (disponibles hors ligne, en lecture) ; l'activité (articles
 * des 7 derniers jours, résumés automatiques du jour) et le budget viennent du serveur quand on est en
 * ligne. Chaque modification part aussitôt (`PUT /feeds/{id}`, `POST /feeds/bulk`) et la réponse
 * remplace la ligne Dexie : la barre latérale et les vues suivent sans resynchronisation.
 */

import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { AlertTriangle, ChevronLeft, CirclePause, CirclePlay, ExternalLink, Pencil, RefreshCw, Search, Sparkles, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api, isOnline, unwrap, type Category, type Feed, type Schemas } from '../../api/client'
import { Button, EmptyState, IconButton, Input, Select, useToast } from '../../components/ui'
import { db } from '../../db/db'
import { agoDate, formatTokens, hostname, plural, untilDate } from '../../lib/format'
import { syncNow } from '../../sync/engine'
import { setViewMode, useDefaultViewMode } from '../entries/useViewMode'
import { VIEW_MODE_SPECS, type ViewMode } from '../entries/viewMode'
import { CategoryDialog, FeedDialog, ViewModeSelect } from './FeedDialog'
import { FeedIcon } from './FeedIcon'
import { EXTRACTION_OPTIONS, deleteFeeds, intervalLabel, intervalOptions, updateFeed, updateFeeds, type ExtractionMode, type FeedChanges, type FeedUpdateChanges } from './feedSettings'

type FeedStats = Schemas['FeedStats']
type StatusFilter = 'all' | 'active' | 'paused' | 'error' | 'auto' | 'full'
type SortKey = 'order' | 'title' | 'fetched' | 'activity' | 'errors'

const STATUS_FILTERS: { id: StatusFilter; label: string }[] = [
  { id: 'all', label: 'Tous les états' },
  { id: 'active', label: 'Actifs' },
  { id: 'paused', label: 'En pause' },
  { id: 'error', label: 'En erreur' },
  { id: 'auto', label: 'Résumés automatiques' },
  { id: 'full', label: 'Article complet toujours' },
]

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'order', label: 'Par dossier' },
  { id: 'title', label: 'Nom' },
  { id: 'fetched', label: 'Dernière récupération' },
  { id: 'activity', label: 'Articles sur 7 jours' },
  { id: 'errors', label: 'Erreurs' },
]

/** Seuil au-delà duquel un flux est signalé en erreur (même règle que la barre latérale). */
const ERROR_THRESHOLD = 2

const EMPTY_FEEDS: Feed[] = []
const EMPTY_CATEGORIES: Category[] = []

function hasError(f: Feed): boolean {
  return f.error_count > ERROR_THRESHOLD
}

function matchesStatus(f: Feed, s: StatusFilter): boolean {
  switch (s) {
    case 'active':
      return !f.disabled
    case 'paused':
      return f.disabled
    case 'error':
      return hasError(f)
    case 'auto':
      return f.auto_summary
    case 'full':
      return f.extraction_mode === 'http' || f.extraction_mode === 'browser'
    default:
      return true
  }
}

export function SubscriptionsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? EMPTY_FEEDS
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? EMPTY_CATEGORIES
  const defaultMode = useDefaultViewMode()
  const [stats, setStats] = useState<Map<number, FeedStats> | null>(null)
  const [budget, setBudget] = useState<{ tokens_today: number; budget: number } | null>(null)
  const [query, setQuery] = useState('')
  const [folder, setFolder] = useState<string>('any')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<SortKey>('order')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [editFeed, setEditFeed] = useState<Feed | null>(null)
  const [editCategory, setEditCategory] = useState<Category | null>(null)
  const [online, setOnline] = useState(isOnline())

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  const loadStats = useCallback(async () => {
    if (!isOnline()) return
    try {
      const [s, sf] = await Promise.all([api().GET('/api/feeds/stats'), api().GET('/api/smart-feeds')])
      setStats(new Map(unwrap(s).map((x) => [x.feed_id, x])))
      const listing = unwrap(sf)
      setBudget({ tokens_today: listing.tokens_today, budget: listing.budget })
    } catch {
      /* activité indisponible : les colonnes restent vides */
    }
  }, [])
  useEffect(() => {
    void loadStats()
  }, [loadStats, online])

  const catById = useMemo(() => new Map(categories.map((c) => [c.id, c])), [categories])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return feeds.filter((f) => {
      if (folder === 'none' ? f.category_id !== null : folder !== 'any' && String(f.category_id) !== folder) return false
      if (!matchesStatus(f, status)) return false
      if (q && !f.title.toLowerCase().includes(q) && !f.url.toLowerCase().includes(q) && !(f.site_url ?? '').toLowerCase().includes(q)) return false
      return true
    })
  }, [feeds, query, folder, status])

  /** Groupes affichés : par dossier (ordre de la barre latérale), ou une seule liste triée. */
  const groups = useMemo(() => {
    if (sort !== 'order') {
      const sorted = [...visible].sort((a, b) => {
        switch (sort) {
          case 'title':
            return a.title.localeCompare(b.title, 'fr', { sensitivity: 'base' })
          case 'fetched':
            return (b.last_fetched_at ? Date.parse(b.last_fetched_at) : 0) - (a.last_fetched_at ? Date.parse(a.last_fetched_at) : 0)
          case 'activity':
            return (stats?.get(b.id)?.entries_7d ?? 0) - (stats?.get(a.id)?.entries_7d ?? 0)
          case 'errors':
            return b.error_count - a.error_count || a.title.localeCompare(b.title, 'fr')
          default:
            return 0
        }
      })
      return [{ key: 'all', category: null as Category | null, feeds: sorted }]
    }
    const byCat = new Map<number | null, Feed[]>()
    for (const f of visible) {
      const k = f.category_id !== null && catById.has(f.category_id) ? f.category_id : null
      byCat.set(k, [...(byCat.get(k) ?? []), f])
    }
    const out: { key: string; category: Category | null; feeds: Feed[] }[] = []
    for (const c of categories) {
      const list = byCat.get(c.id)
      if (list?.length) out.push({ key: `c${c.id}`, category: c, feeds: list.sort((a, b) => a.position - b.position) })
    }
    const loose = byCat.get(null)
    if (loose?.length) out.push({ key: 'none', category: null, feeds: loose.sort((a, b) => a.position - b.position) })
    return out
  }, [visible, sort, stats, categories, catById])

  // La sélection ne garde que des flux encore visibles (filtre changé, flux supprimé).
  useEffect(() => {
    setSelected((prev) => {
      const ids = new Set(visible.map((f) => f.id))
      const next = new Set([...prev].filter((id) => ids.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [visible])

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const toggleMany = (ids: number[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev)
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)))
      return next
    })

  const run = useCallback(
    async (action: () => Promise<unknown>, success?: string) => {
      if (!isOnline()) {
        toast('Modification impossible hors ligne', 'error')
        return false
      }
      setBusy(true)
      try {
        await action()
        if (success) toast(success, 'success')
        return true
      } catch (err) {
        toast(err instanceof Error ? err.message : 'Erreur', 'error')
        return false
      } finally {
        setBusy(false)
      }
    },
    [toast],
  )

  const saveOne = useCallback((feed: Feed, changes: FeedUpdateChanges) => void run(() => updateFeed(feed.id, changes)), [run])

  const selectedIds = [...selected]
  const bulk = (changes: FeedChanges, label: string) =>
    void run(async () => {
      await updateFeeds(selectedIds, changes)
      if ('auto_summary' in changes) void loadStats()
    }, `${label} : ${selectedIds.length} ${plural(selectedIds.length, 'flux', 'flux')}`)

  async function refreshSelected() {
    await run(async () => {
      for (const id of selectedIds) unwrap(await api().POST('/api/feeds/{feed_id}/refresh', { params: { path: { feed_id: id } } }))
      window.setTimeout(() => void syncNow(), 5000)
    }, `Rafraîchissement lancé pour ${selectedIds.length} flux`)
  }

  async function removeSelected() {
    if (!window.confirm(`Se désabonner de ${selectedIds.length} flux ? Leurs articles seront supprimés.`)) return
    if (await run(() => deleteFeeds(selectedIds), 'Désabonné')) setSelected(new Set())
  }

  const counts = useMemo(
    () => ({ paused: feeds.filter((f) => f.disabled).length, errors: feeds.filter(hasError).length, auto: feeds.filter((f) => f.auto_summary).length }),
    [feeds],
  )
  const allVisibleSelected = visible.length > 0 && visible.every((f) => selected.has(f.id))

  return (
    <div className="flex h-full flex-col bg-app">
      <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b border-app bg-panel px-2">
        <IconButton label="Retour" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/'))}>
          <ChevronLeft className="size-5" />
        </IconButton>
        <h1 className="font-semibold">Abonnements</h1>
        <span className="min-w-0 truncate text-sm text-muted tabular-nums">
          {feeds.length} flux
          {counts.paused > 0 && ` · ${counts.paused} en pause`}
          {counts.errors > 0 && ` · ${counts.errors} en erreur`}
        </span>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => navigate('/settings')}>
          Paramètres
        </Button>
      </header>

      <div className="shrink-0 space-y-2 border-b border-app bg-panel px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[12rem] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Rechercher un flux (nom, adresse)…" className="h-9 pl-8" aria-label="Rechercher un flux" data-testid="subs-search" />
          </div>
          <div className="grid w-full grid-cols-3 gap-2 md:flex md:w-auto">
            <Select value={folder} onChange={(e) => setFolder(e.target.value)} className={filterSelect} aria-label="Dossier" data-testid="subs-folder">
              <option value="any">Tous les dossiers</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
              <option value="none">Sans dossier</option>
            </Select>
            <Select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} className={filterSelect} aria-label="État" data-testid="subs-status">
              {STATUS_FILTERS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
            <Select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={filterSelect} aria-label="Tri" data-testid="subs-sort">
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <p className="text-xs text-muted">
          Présentation par défaut : {VIEW_MODE_SPECS[defaultMode].label} (Paramètres) · un flux sans présentation suit son dossier.
          {budget && (
            <>
              {' '}
              · Résumés automatiques : {counts.auto} {plural(counts.auto, 'flux', 'flux')}, {formatTokens(budget.tokens_today)} / {formatTokens(budget.budget)} tokens aujourd’hui
              {budget.budget > 0 && budget.tokens_today >= budget.budget && <span className="ml-1 font-medium text-amber-600 dark:text-amber-400">budget atteint</span>}
            </>
          )}
          {!online && <span className="ml-1 font-medium text-amber-600 dark:text-amber-400">Hors ligne : consultation seulement.</span>}
        </p>
      </div>

      <div className="flex-1 overflow-auto">
        {visible.length === 0 ? (
          <EmptyState icon={<Search className="size-10" />} title={feeds.length ? 'Aucun flux ne correspond' : 'Aucun abonnement'} hint={feeds.length ? 'Modifiez la recherche ou les filtres.' : 'Ajoutez un flux depuis la barre latérale.'} />
        ) : (
          <>
            {/* Tableau (tablette et bureau) */}
            <table className="hidden w-full min-w-[68rem] border-collapse text-sm md:table" data-testid="subs-table">
              <thead className="sticky top-0 z-[5] border-b border-app bg-panel text-left text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="w-10 px-3 py-2">
                    <input type="checkbox" className="size-4 accent-accent-600" checked={allVisibleSelected} onChange={(e) => toggleMany(visible.map((f) => f.id), e.target.checked)} aria-label="Tout sélectionner" />
                  </th>
                  <th className="px-2 py-2 font-medium">Flux</th>
                  <th className="px-2 py-2 font-medium">État</th>
                  <th className="px-2 py-2 font-medium">Récupération</th>
                  <th className="px-2 py-2 font-medium">Intervalle</th>
                  <th className="px-2 py-2 font-medium">Article complet</th>
                  <th className="px-2 py-2 font-medium">Résumés auto</th>
                  <th className="px-2 py-2 font-medium">Présentation</th>
                  <th className="px-2 py-2 text-right font-medium">7 j</th>
                  <th className="w-20 px-2 py-2" />
                </tr>
              </thead>
              {groups.map((g) => (
                <tbody key={g.key}>
                  {sort === 'order' && (
                    <GroupRow
                      category={g.category}
                      feeds={g.feeds}
                      selected={selected}
                      defaultMode={defaultMode}
                      disabled={!online || busy}
                      onToggle={toggleMany}
                      onEdit={setEditCategory}
                      onViewMode={(c, v) => void run(() => setViewMode({ kind: 'category', id: c.id }, v || 'inherit'))}
                    />
                  )}
                  {g.feeds.map((f) => (
                    <FeedRow
                      key={f.id}
                      feed={f}
                      category={f.category_id !== null ? catById.get(f.category_id) : undefined}
                      stats={stats?.get(f.id)}
                      defaultMode={defaultMode}
                      selected={selected.has(f.id)}
                      disabled={!online || busy}
                      onToggle={() => toggle(f.id)}
                      onSave={(changes) => saveOne(f, changes)}
                      onEdit={() => setEditFeed(f)}
                    />
                  ))}
                </tbody>
              ))}
            </table>

            {/* Cartes (mobile) */}
            <ul className="md:hidden" data-testid="subs-cards">
              {groups.map((g) => (
                <li key={g.key}>
                  {sort === 'order' && <p className="bg-muted px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted">{g.category?.title ?? 'Sans dossier'}</p>}
                  <ul>
                    {g.feeds.map((f) => (
                      <li key={f.id} className="flex items-center gap-3 border-b border-app px-3 py-2.5">
                        <input type="checkbox" className="size-4 shrink-0 accent-accent-600" checked={selected.has(f.id)} onChange={() => toggle(f.id)} aria-label={`Sélectionner ${f.title}`} />
                        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setEditFeed(f)}>
                          <FeedIcon feed={f} />
                          <span className="min-w-0 flex-1">
                            <span className={clsx('block truncate text-sm', f.disabled ? 'text-muted' : 'text-app font-medium')}>{f.title || hostname(f.url)}</span>
                            <span className="mt-0.5 flex flex-wrap gap-1">
                              <StatusBadge feed={f} />
                              <Badge>{intervalLabel(f)}</Badge>
                              {f.auto_summary && <Badge tone="accent">Résumés auto</Badge>}
                              {f.view_mode && <Badge>{VIEW_MODE_SPECS[f.view_mode].label}</Badge>}
                            </span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {selected.size > 0 && (
        <BulkBar
          count={selected.size}
          categories={categories}
          disabled={!online || busy}
          onClear={() => setSelected(new Set())}
          onChanges={bulk}
          onRefresh={() => void refreshSelected()}
          onDelete={() => void removeSelected()}
        />
      )}

      <FeedDialog feed={editFeed} categories={categories} onClose={() => setEditFeed(null)} />
      <CategoryDialog open={editCategory !== null} category={editCategory} onClose={() => setEditCategory(null)} />
    </div>
  )
}

function Badge({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'accent' | 'warn' | 'error' }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] leading-none',
        tone === 'muted' && 'bg-muted text-muted',
        tone === 'accent' && 'bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-200',
        tone === 'warn' && 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200',
        tone === 'error' && 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
      )}
    >
      {children}
    </span>
  )
}

function StatusBadge({ feed }: { feed: Feed }) {
  if (feed.disabled) return <Badge tone="warn">En pause</Badge>
  if (hasError(feed))
    return (
      <span title={feed.last_error ?? undefined}>
        <Badge tone="error">
          <AlertTriangle className="size-3" /> Erreur ×{feed.error_count}
        </Badge>
      </span>
    )
  return <Badge>Actif</Badge>
}

const cellSelect = 'h-8 max-w-[12rem] px-2 text-xs'
const filterSelect = 'h-9 px-2 text-xs md:w-48! md:text-sm'

function GroupRow({
  category,
  feeds,
  selected,
  defaultMode,
  disabled,
  onToggle,
  onEdit,
  onViewMode,
}: {
  category: Category | null
  feeds: Feed[]
  selected: Set<number>
  defaultMode: ViewMode
  disabled: boolean
  onToggle: (ids: number[], on: boolean) => void
  onEdit: (c: Category) => void
  onViewMode: (c: Category, v: ViewMode | '') => void
}) {
  const ids = feeds.map((f) => f.id)
  const all = ids.every((id) => selected.has(id))
  return (
    <tr className="bg-muted/60" data-testid={category ? `subs-group-${category.id}` : 'subs-group-none'}>
      <td className="px-3 py-1.5">
        <input type="checkbox" className="size-4 accent-accent-600" checked={all} onChange={(e) => onToggle(ids, e.target.checked)} aria-label={`Sélectionner le dossier ${category?.title ?? 'Sans dossier'}`} />
      </td>
      <td colSpan={6} className="px-2 py-1.5">
        <span className="font-semibold text-app">{category?.title ?? 'Sans dossier'}</span>
        <span className="ml-2 text-xs text-muted">
          {feeds.length} {plural(feeds.length, 'flux', 'flux')}
        </span>
      </td>
      <td className="px-2 py-1.5">
        {category && (
          <ViewModeSelect
            value={category.view_mode ?? ''}
            inheritLabel={`Défaut (${VIEW_MODE_SPECS[defaultMode].label})`}
            onChange={(v) => onViewMode(category, v)}
            disabled={disabled}
            aria-label={`Présentation du dossier ${category.title}`}
            className={cellSelect}
          />
        )}
      </td>
      <td />
      <td className="px-2 py-1.5 text-right">
        {category && (
          <IconButton label={`Modifier le dossier ${category.title}`} size="sm" onClick={() => onEdit(category)} disabled={disabled}>
            <Pencil className="size-3.5" />
          </IconButton>
        )}
      </td>
    </tr>
  )
}

function FeedRow({
  feed,
  category,
  stats,
  defaultMode,
  selected,
  disabled,
  onToggle,
  onSave,
  onEdit,
}: {
  feed: Feed
  category?: Category
  stats?: FeedStats
  defaultMode: ViewMode
  selected: boolean
  disabled: boolean
  onToggle: () => void
  onSave: (changes: FeedUpdateChanges) => void
  onEdit: () => void
}) {
  const [cap, setCap] = useState(String(feed.summary_daily_cap))
  useEffect(() => setCap(String(feed.summary_daily_cap)), [feed.summary_daily_cap])
  const inherited = category?.view_mode ? `Dossier (${VIEW_MODE_SPECS[category.view_mode].label})` : `Défaut (${VIEW_MODE_SPECS[defaultMode].label})`

  function commitCap() {
    const n = Number(cap)
    if (Number.isInteger(n) && n >= 1 && n <= 200) {
      if (n !== feed.summary_daily_cap) onSave({ summary_daily_cap: n })
    } else setCap(String(feed.summary_daily_cap))
  }

  return (
    <tr className={clsx('border-b border-app align-middle hover:bg-muted/40', selected && 'bg-accent-50/60 dark:bg-accent-900/10')} data-feed-row={feed.id}>
      <td className="px-3 py-2">
        <input type="checkbox" className="size-4 accent-accent-600" checked={selected} onChange={onToggle} aria-label={`Sélectionner ${feed.title}`} />
      </td>
      <td className="max-w-[18rem] px-2 py-2">
        <div className="flex items-center gap-2">
          <FeedIcon feed={feed} />
          <div className="min-w-0">
            <Link to={`/feed/${feed.id}`} className={clsx('block truncate hover:underline', feed.disabled ? 'text-muted' : 'font-medium text-app')} title={feed.title}>
              {feed.title || hostname(feed.url)}
            </Link>
            <span className="block truncate text-xs text-muted" title={feed.url}>
              {hostname(feed.site_url || feed.url)}
            </span>
          </div>
        </div>
      </td>
      <td className="px-2 py-2">
        <button type="button" onClick={() => onSave({ disabled: !feed.disabled })} disabled={disabled} className="group inline-flex items-center gap-1 disabled:cursor-not-allowed" title={feed.disabled ? 'Reprendre' : 'Mettre en pause'} data-testid="subs-toggle-pause">
          <StatusBadge feed={feed} />
          {feed.disabled ? <CirclePlay className="size-3.5 text-muted opacity-0 group-hover:opacity-100" /> : <CirclePause className="size-3.5 text-muted opacity-0 group-hover:opacity-100" />}
        </button>
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-xs text-muted">
        <div>{feed.last_fetched_at ? agoDate(feed.last_fetched_at) : 'jamais'}</div>
        <div>{feed.disabled ? '—' : `prochaine ${untilDate(feed.next_fetch_at)}`}</div>
      </td>
      <td className="px-2 py-2">
        <Select value={feed.fetch_interval_fixed_min ?? 0} onChange={(e) => onSave({ fetch_interval_fixed_min: Number(e.target.value) })} disabled={disabled} className={cellSelect} aria-label={`Intervalle de ${feed.title}`} title={intervalLabel(feed)}>
          {intervalOptions(feed).map((o) => (
            <option key={o.minutes} value={o.minutes}>
              {o.minutes === 0 ? `Auto (${intervalLabel(feed).replace('Auto · ', '')})` : o.label.replace('Toutes les ', '').replace('Une fois par jour', '24 h')}
            </option>
          ))}
        </Select>
      </td>
      <td className="px-2 py-2">
        <Select value={feed.extraction_mode} onChange={(e) => onSave({ extraction_mode: e.target.value as ExtractionMode })} disabled={disabled} className={cellSelect} aria-label={`Article complet de ${feed.title}`}>
          {EXTRACTION_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.short}
            </option>
          ))}
        </Select>
      </td>
      <td className="whitespace-nowrap px-2 py-2">
        <div className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="size-4 accent-accent-600"
            checked={feed.auto_summary}
            onChange={(e) => onSave({ auto_summary: e.target.checked })}
            disabled={disabled}
            aria-label={`Résumés automatiques de ${feed.title}`}
            data-testid="subs-auto-summary"
          />
          {feed.auto_summary ? (
            <>
              <input
                type="number"
                min={1}
                max={200}
                value={cap}
                onChange={(e) => setCap(e.target.value)}
                onBlur={commitCap}
                onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                disabled={disabled}
                className="h-7 w-14 rounded-md border border-app bg-panel px-1.5 text-xs"
                aria-label={`Quota quotidien de ${feed.title}`}
                title="Résumés automatiques au plus par jour"
              />
              <span className="text-xs text-muted tabular-nums" title="Résumés automatiques aujourd’hui / quota">
                <Sparkles className="mr-0.5 inline size-3" />
                {stats?.auto_summaries_today ?? '–'}/{feed.summary_daily_cap}
              </span>
            </>
          ) : (
            <span className="text-xs text-muted">Non</span>
          )}
        </div>
      </td>
      <td className="px-2 py-2">
        <ViewModeSelect value={feed.view_mode ?? ''} inheritLabel={inherited} onChange={(v) => onSave({ view_mode: v || 'inherit' })} disabled={disabled} aria-label={`Présentation de ${feed.title}`} className={cellSelect} />
      </td>
      <td className="px-2 py-2 text-right text-xs tabular-nums text-muted" title={stats ? `${stats.entries_30d} sur 30 jours${stats.last_entry_at ? ` · dernier article ${agoDate(stats.last_entry_at)}` : ''}` : undefined}>
        {stats ? stats.entries_7d : '–'}
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-right">
        {feed.site_url && (
          <a href={feed.site_url} target="_blank" rel="noreferrer" className="inline-flex size-8 items-center justify-center rounded-lg text-muted hover:bg-muted hover:text-app" aria-label={`Ouvrir le site de ${feed.title}`}>
            <ExternalLink className="size-3.5" />
          </a>
        )}
        <IconButton label={`Modifier ${feed.title}`} size="sm" onClick={onEdit}>
          <Pencil className="size-3.5" />
        </IconButton>
      </td>
    </tr>
  )
}

/** Barre des actions groupées : chaque liste déroulante applique sa valeur à toute la sélection. */
function BulkBar({
  count,
  categories,
  disabled,
  onClear,
  onChanges,
  onRefresh,
  onDelete,
}: {
  count: number
  categories: Category[]
  disabled: boolean
  onClear: () => void
  onChanges: (changes: FeedChanges, label: string) => void
  onRefresh: () => void
  onDelete: () => void
}) {
  // Liste déroulante « action » : revient sur son intitulé après chaque choix.
  const pick = (label: string, options: { value: string; label: string }[], apply: (v: string) => void, testId: string) => (
    <Select
      value=""
      onChange={(e) => {
        if (e.target.value) apply(e.target.value)
      }}
      disabled={disabled}
      className="h-8 w-auto! px-2 text-xs"
      aria-label={label}
      data-testid={testId}
    >
      <option value="">{label}…</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </Select>
  )

  return (
    <div className="shrink-0 border-t border-app bg-panel px-3 py-2 shadow-[0_-4px_12px_rgba(0,0,0,0.06)]" data-testid="subs-bulk">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium tabular-nums">
          {count} {plural(count, 'sélectionné')}
        </span>
        <IconButton label="Désélectionner" size="sm" onClick={onClear}>
          <X className="size-4" />
        </IconButton>
        <span className="mx-1 h-5 border-l border-app" />
        <Button size="sm" variant="ghost" onClick={() => onChanges({ disabled: true }, 'Mis en pause')} disabled={disabled} data-testid="bulk-pause">
          <CirclePause className="size-4" /> Pause
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onChanges({ disabled: false }, 'Repris')} disabled={disabled} data-testid="bulk-resume">
          <CirclePlay className="size-4" /> Reprendre
        </Button>
        {pick(
          'Déplacer',
          [...categories.map((c) => ({ value: String(c.id), label: c.title })), { value: 'none', label: 'Sans dossier' }],
          (v) => onChanges(v === 'none' ? { clear_category: true } : { category_id: Number(v) }, 'Déplacé'),
          'bulk-move',
        )}
        {pick(
          'Présentation',
          [{ value: 'inherit', label: 'Hériter (dossier ou défaut)' }, ...Object.entries(VIEW_MODE_SPECS).map(([id, s]) => ({ value: id, label: s.label }))],
          (v) => onChanges({ view_mode: v as FeedChanges['view_mode'] }, 'Présentation'),
          'bulk-view',
        )}
        {pick(
          'Article complet',
          EXTRACTION_OPTIONS.map((o) => ({ value: o.id, label: o.short })),
          (v) => onChanges({ extraction_mode: v as ExtractionMode }, 'Article complet'),
          'bulk-extraction',
        )}
        {pick(
          'Intervalle',
          intervalOptions().map((o) => ({ value: String(o.minutes), label: o.label })),
          (v) => onChanges({ fetch_interval_fixed_min: Number(v) }, 'Intervalle'),
          'bulk-interval',
        )}
        {pick(
          'Résumés auto',
          [
            { value: 'on', label: 'Activer' },
            { value: 'off', label: 'Désactiver' },
          ],
          (v) => onChanges({ auto_summary: v === 'on' }, v === 'on' ? 'Résumés automatiques activés' : 'Résumés automatiques désactivés'),
          'bulk-auto',
        )}
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={onRefresh} disabled={disabled}>
          <RefreshCw className="size-4" /> Rafraîchir
        </Button>
        <Button size="sm" variant="ghost" className="text-red-600 dark:text-red-400" onClick={onDelete} disabled={disabled} data-testid="bulk-delete">
          <Trash2 className="size-4" /> Se désabonner
        </Button>
      </div>
    </div>
  )
}
