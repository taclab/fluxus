/** Modèle du tableau de bord : types, validation, disposition par défaut. Aucune dépendance React. */

import type { Category, Feed, SmartFeed } from '../../api/client'
import type { View } from '../../app/view'
import { compareText, getT } from '../../i18n'

export const LAYOUT_VERSION = 1

/** Largeur en colonnes d'une grille de 6 : tiers, moitié, pleine. */
export type WidgetWidth = 2 | 3 | 6
export const WIDTHS: WidgetWidth[] = [2, 3, 6]
export const WIDTH_LABEL: Record<WidgetWidth, string> = { 2: '⅓', 3: '½', 6: '1' }
/** Classes écrites en clair : Tailwind ne détecte pas les classes construites dynamiquement. */
export const WIDTH_CLASS: Record<WidgetWidth, string> = { 2: 'md:col-span-2', 3: 'md:col-span-3', 6: 'md:col-span-6' }

export type EntrySource = { kind: 'all' } | { kind: 'category'; id: number } | { kind: 'feed'; id: number } | { kind: 'smart'; id: number }
export type YoutubeSource = { kind: 'all_youtube' } | { kind: 'category'; id: number }

interface WidgetBase {
  id: string
  width: WidgetWidth
  title?: string
}

export type NewsDisplay = 'list' | 'cards' | 'magazine' | 'carousel'
export const DISPLAYS: NewsDisplay[] = ['list', 'magazine', 'carousel', 'cards']
/** Libellé d'un mode d'affichage, dans la langue courante. */
export function displayLabel(d: NewsDisplay): string {
  return getT().dashboard.displays[d]
}
export type NewsWidget = WidgetBase & { type: 'news'; source: EntrySource; limit: number; display: NewsDisplay; unreadOnly: boolean }
export type YoutubeWidget = WidgetBase & { type: 'youtube'; source: YoutubeSource; limit: number; unreadOnly: boolean }
export type StarredWidget = WidgetBase & { type: 'starred'; limit: number }
export type SummariesWidget = WidgetBase & { type: 'summaries'; limit: number }
export type StatsWidget = WidgetBase & {
  type: 'stats'
  show: { unreadByCategory: boolean; readToday: boolean; feedErrors: boolean; tokens: boolean }
}
/** Widget écrit par une version plus récente : conservé tel quel, rendu « non pris en charge ». */
export type UnknownWidget = WidgetBase & { type: string; [k: string]: unknown }

export type Widget = NewsWidget | YoutubeWidget | StarredWidget | SummariesWidget | StatsWidget
export type AnyWidget = Widget | UnknownWidget
export type WidgetType = Widget['type']

export const KNOWN_TYPES: WidgetType[] = ['news', 'youtube', 'starred', 'summaries', 'stats']
/** Libellé d'un type de widget, dans la langue courante. */
export function typeLabel(type: WidgetType): string {
  return getT().dashboard.types[type]
}

export interface DashboardLayout {
  version: number
  updatedAt: string
  widgets: AnyWidget[]
}

export type ParsedLayout = { kind: 'ok'; layout: DashboardLayout } | { kind: 'empty' } | { kind: 'newer'; layout: DashboardLayout }

export function newWidgetId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export function isKnownWidget(w: AnyWidget): w is Widget {
  return (KNOWN_TYPES as string[]).includes(w.type)
}

const clampLimit = (n: unknown, fallback: number) => {
  const v = typeof n === 'number' && Number.isFinite(n) ? Math.round(n) : fallback
  return Math.min(30, Math.max(3, v))
}
const normWidth = (w: unknown): WidgetWidth => (w === 2 || w === 3 || w === 6 ? w : 3)

function normalizeWidget(raw: unknown): AnyWidget | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.type !== 'string') return null
  const base: WidgetBase = {
    id: typeof r.id === 'string' && r.id ? r.id : newWidgetId(),
    width: normWidth(r.width),
    ...(typeof r.title === 'string' && r.title.trim() ? { title: r.title.trim() } : {}),
  }
  switch (r.type) {
    case 'news': {
      const src = (r.source as EntrySource | undefined) ?? { kind: 'all' }
      const source: EntrySource =
        src.kind === 'category' && typeof src.id === 'number'
          ? { kind: 'category', id: src.id }
          : src.kind === 'feed' && typeof src.id === 'number'
            ? { kind: 'feed', id: src.id }
            : src.kind === 'smart' && typeof src.id === 'number'
              ? { kind: 'smart', id: src.id }
              : { kind: 'all' }
      const display: NewsDisplay = r.display === 'cards' || r.display === 'magazine' || r.display === 'carousel' ? r.display : 'list'
      return { ...base, type: 'news', source, limit: clampLimit(r.limit, 8), display, unreadOnly: r.unreadOnly !== false }
    }
    case 'youtube': {
      const src = (r.source as YoutubeSource | undefined) ?? { kind: 'all_youtube' }
      const source: YoutubeSource = src.kind === 'category' && typeof src.id === 'number' ? { kind: 'category', id: src.id } : { kind: 'all_youtube' }
      return { ...base, type: 'youtube', source, limit: clampLimit(r.limit, 12), unreadOnly: r.unreadOnly !== false }
    }
    case 'starred':
      return { ...base, type: 'starred', limit: clampLimit(r.limit, 8) }
    case 'summaries':
      return { ...base, type: 'summaries', limit: clampLimit(r.limit, 8) }
    case 'stats': {
      const show = (r.show as Partial<StatsWidget['show']> | undefined) ?? {}
      return {
        ...base,
        type: 'stats',
        show: {
          unreadByCategory: show.unreadByCategory !== false,
          readToday: show.readToday !== false,
          feedErrors: show.feedErrors !== false,
          tokens: show.tokens !== false,
        },
      }
    }
    default:
      return { ...r, ...base, type: r.type } as UnknownWidget
  }
}

/** Analyse défensive du JSON stocké. Ne lève jamais. */
export function parseLayout(raw: string | null | undefined): ParsedLayout {
  if (!raw || !raw.trim()) return { kind: 'empty' }
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return { kind: 'empty' }
  }
  if (!data || typeof data !== 'object') return { kind: 'empty' }
  const d = data as Record<string, unknown>
  const version = typeof d.version === 'number' ? d.version : 1
  const widgets = Array.isArray(d.widgets) ? (d.widgets.map(normalizeWidget).filter(Boolean) as AnyWidget[]) : []
  const layout: DashboardLayout = {
    version,
    updatedAt: typeof d.updatedAt === 'string' ? d.updatedAt : new Date(0).toISOString(),
    widgets,
  }
  if (version > LAYOUT_VERSION) return { kind: 'newer', layout }
  return { kind: 'ok', layout: migrate(layout) }
}

function migrate(layout: DashboardLayout): DashboardLayout {
  // Aucune migration pour l'instant ; ajouter ici un `case` par saut de version.
  return { ...layout, version: LAYOUT_VERSION }
}

export function serializeLayout(layout: DashboardLayout): string {
  return JSON.stringify({ version: LAYOUT_VERSION, updatedAt: layout.updatedAt, widgets: layout.widgets })
}

/** Disposition dérivée des données quand rien n'est stocké. Jamais persistée telle quelle. */
export function buildDefaultLayout(categories: Category[], feeds: Feed[]): DashboardLayout {
  const widgets: Widget[] = []
  widgets.push({ id: 'default-stats', type: 'stats', width: 6, show: { unreadByCategory: true, readToday: true, feedErrors: true, tokens: true } })
  widgets.push({ id: 'default-feed', type: 'news', width: 6, source: { kind: 'all' }, limit: 12, display: 'carousel', unreadOnly: true })
  const sorted = [...categories].sort((a, b) => a.position - b.position || compareText(a.title, b.title))
  for (const cat of sorted) {
    widgets.push({ id: `default-cat-${cat.id}`, type: 'news', width: 3, source: { kind: 'category', id: cat.id }, limit: 5, display: 'magazine', unreadOnly: true })
  }
  if (feeds.some((f) => f.category_id == null) && categories.length > 0) {
    widgets.push({ id: 'default-uncategorized', type: 'news', width: 3, source: { kind: 'all' }, limit: 5, display: 'magazine', unreadOnly: true })
  }
  if (feeds.some((f) => f.is_youtube)) {
    widgets.push({ id: 'default-youtube', type: 'youtube', width: 6, source: { kind: 'all_youtube' }, limit: 12, unreadOnly: true })
  }
  widgets.push({ id: 'default-starred', type: 'starred', width: 3, limit: 8 })
  widgets.push({ id: 'default-summaries', type: 'summaries', width: 3, limit: 8 })
  return { version: LAYOUT_VERSION, updatedAt: new Date(0).toISOString(), widgets }
}

/** Titre affiché : personnalisé, sinon dérivé de la cible. Renvoie `orphan: true` si la cible a disparu. */
export function widgetTitle(w: AnyWidget, categories: Map<number, Category>, feeds: Map<number, Feed>, smartFeeds: Map<number, SmartFeed> = new Map()): { title: string; orphan: boolean } {
  const t = getT().dashboard.titles
  if (!isKnownWidget(w)) return { title: w.title ?? t.unsupported, orphan: false }
  const custom = defaultTitle(w) ?? w.title
  switch (w.type) {
    case 'news': {
      if (w.source.kind === 'category') {
        const c = categories.get(w.source.id)
        return { title: custom ?? c?.title ?? t.deletedCategory, orphan: !c }
      }
      if (w.source.kind === 'feed') {
        const f = feeds.get(w.source.id)
        return { title: custom ?? f?.title ?? t.deletedFeed, orphan: !f }
      }
      if (w.source.kind === 'smart') {
        const sf = smartFeeds.get(w.source.id)
        return { title: custom ?? sf?.name ?? t.deletedSmartFeed, orphan: !sf }
      }
      return { title: custom ?? t.allEntries, orphan: false }
    }
    case 'youtube': {
      if (w.source.kind === 'category') {
        const c = categories.get(w.source.id)
        return { title: custom ?? t.videosOf(c?.title ?? t.deletedCategory), orphan: !c }
      }
      return { title: custom ?? t.youtube, orphan: false }
    }
    case 'starred':
      return { title: custom ?? t.starred, orphan: false }
    case 'summaries':
      return { title: custom ?? t.summaries, orphan: false }
    case 'stats':
      return { title: custom ?? t.stats, orphan: false }
  }
}

/**
 * Titres des widgets automatiques « Fil d’actualité » et « Sans catégorie », traduits à l'affichage.
 * Les dispositions enregistrées avant la traduction portent le titre français en clair : il est
 * reconnu (identifiant + texte d'origine) et traité comme le titre par défaut, pas comme un titre
 * personnalisé. Un titre modifié par l'utilisateur reste tel quel.
 */
const DEFAULT_TITLES: Record<string, { legacy: string; key: 'newsFeed' | 'uncategorized' }> = {
  'default-feed': { legacy: 'Fil d’actualité', key: 'newsFeed' },
  'default-uncategorized': { legacy: 'Sans catégorie', key: 'uncategorized' },
}

function defaultTitle(w: Widget): string | undefined {
  const d = DEFAULT_TITLES[w.id]
  if (!d || (w.title !== undefined && w.title !== d.legacy)) return undefined
  return getT().dashboard.titles[d.key]
}

/** Widget vierge d'un type donné, pour le dialogue d'ajout. */
export function blankWidget(type: WidgetType): Widget {
  const base = { id: newWidgetId(), width: 3 as WidgetWidth }
  switch (type) {
    case 'news':
      return { ...base, type, source: { kind: 'all' }, limit: 6, display: 'magazine', unreadOnly: true }
    case 'youtube':
      return { ...base, type, width: 6, source: { kind: 'all_youtube' }, limit: 12, unreadOnly: true }
    case 'starred':
      return { ...base, type, limit: 8 }
    case 'summaries':
      return { ...base, type, limit: 8 }
    case 'stats':
      return { ...base, type, width: 6, show: { unreadByCategory: true, readToday: true, feedErrors: true, tokens: true } }
  }
}

/** Vue de liste qui correspond à la source d'un widget d'actualités. */
export function viewForSource(source: NewsWidget['source']): View {
  if (source.kind === 'category') return { kind: 'category', id: source.id }
  if (source.kind === 'feed') return { kind: 'feed', id: source.id }
  if (source.kind === 'smart') return { kind: 'smart', id: source.id }
  return { kind: 'all' }
}
