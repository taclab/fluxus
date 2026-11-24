/**
 * Présentations de la liste d'articles et règles de résolution (sans React ni Dexie).
 *
 * Une présentation détermine aussi la disposition : `compact` et `cards` gardent l'article à côté de
 * la liste, les autres occupent toute la largeur et ouvrent l'article dans une fenêtre.
 *
 * La présentation est un attribut serveur, synchronisé entre appareils :
 * - flux : la sienne, sinon celle de son dossier, sinon le défaut global ;
 * - dossier, flux intelligent : la sienne, sinon le défaut ;
 * - Tous, Non lus, Favoris, Recherche : le défaut (le menu de ces vues modifie donc le défaut).
 */

import { AlignJustify, GalleryVertical, LayoutGrid, LayoutList, Rows3 } from 'lucide-react'

import type { Category, Feed, SmartFeed } from '../../api/client'
import type { View } from '../../app/view'
import { getT } from '../../i18n'

export type ViewMode = NonNullable<Feed['view_mode']>
export type ListLayout = 'split' | 'full'

interface ViewModeSpec {
  label: string
  Icon: typeof Rows3
  layout: ListLayout
  /** Classes du <ul>. */
  listClass: string
}

/** Table unique des présentations : menus, <ul>, rendu des lignes et validation en dérivent.
 * `label` est un accesseur : lu dans la langue courante à chaque usage, jamais figé au chargement. */
export const VIEW_MODE_SPECS: Record<ViewMode, ViewModeSpec> = {
  compact: { get label() { return getT().entries.viewMode.compact }, Icon: Rows3, layout: 'split', listClass: '' },
  cards: { get label() { return getT().entries.viewMode.cards }, Icon: LayoutList, layout: 'split', listClass: 'p-2 space-y-2' },
  grid: { get label() { return getT().entries.viewMode.grid }, Icon: LayoutGrid, layout: 'full', listClass: 'grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4' },
  magazine: { get label() { return getT().entries.viewMode.magazine }, Icon: GalleryVertical, layout: 'full', listClass: 'mx-auto w-full max-w-6xl' },
  list: { get label() { return getT().entries.viewMode.list }, Icon: Rows3, layout: 'full', listClass: 'mx-auto w-full max-w-6xl' },
  titles: { get label() { return getT().entries.viewMode.titles }, Icon: AlignJustify, layout: 'full', listClass: 'mx-auto w-full max-w-6xl' },
}
export const VIEW_MODES = Object.keys(VIEW_MODE_SPECS) as ViewMode[]
export const DEFAULT_VIEW_MODE: ViewMode = 'compact'

export const LAYOUT_GROUPS: { layout: ListLayout; label: string }[] = [
  { layout: 'split', get label() { return getT().entries.viewMode.layoutSplit } },
  { layout: 'full', get label() { return getT().entries.viewMode.layoutFull } },
]

export function modesFor(layout: ListLayout): ViewMode[] {
  return VIEW_MODES.filter((m) => VIEW_MODE_SPECS[m].layout === layout)
}

export function isViewMode(value: unknown): value is ViewMode {
  return typeof value === 'string' && (VIEW_MODES as string[]).includes(value)
}

export function layoutOf(mode: ViewMode): ListLayout {
  return VIEW_MODE_SPECS[mode].layout
}

/** Ce que modifie le menu d'une vue : l'objet affiché, ou le défaut pour les vues transversales. */
export type ViewModeTarget = { kind: 'feed' | 'category' | 'smart'; id: number } | { kind: 'default' }

export function targetOf(view: View): ViewModeTarget {
  if ((view.kind === 'feed' || view.kind === 'category' || view.kind === 'smart') && view.id) return { kind: view.kind, id: view.id }
  return { kind: 'default' }
}

export function targetKey(target: ViewModeTarget): string {
  return target.kind === 'default' ? 'default' : `${target.kind}:${target.id}`
}

export interface ResolvedViewMode {
  mode: ViewMode
  /** Origine de la présentation : propre à la vue, héritée du dossier, ou défaut global. */
  source: 'own' | 'category' | 'default'
  /** Titre du dossier dont la présentation est héritée. */
  inheritedFrom?: string
  target: ViewModeTarget
}

export function resolveViewMode(
  view: View,
  ctx: { feed?: Feed; category?: Category; smartFeed?: SmartFeed; fallback: ViewMode },
): ResolvedViewMode {
  const target = targetOf(view)
  const own = view.kind === 'feed' ? ctx.feed?.view_mode : view.kind === 'category' ? ctx.category?.view_mode : view.kind === 'smart' ? ctx.smartFeed?.view_mode : undefined
  if (target.kind !== 'default' && isViewMode(own)) return { mode: own, source: 'own', target }
  // Flux : `ctx.category` est alors le dossier du flux.
  if (view.kind === 'feed' && ctx.category && isViewMode(ctx.category.view_mode)) {
    return { mode: ctx.category.view_mode, source: 'category', inheritedFrom: ctx.category.title, target }
  }
  return { mode: ctx.fallback, source: 'default', target }
}

/** Libellé de l'origine, pour le menu et l'écran Abonnements. */
export function sourceLabel(r: Pick<ResolvedViewMode, 'source' | 'inheritedFrom' | 'target'>): string {
  const t = getT().entries.viewMode
  if (r.target.kind === 'default') return t.sourceDefaultViews
  if (r.source === 'own') return r.target.kind === 'category' ? t.sourceOwnFolder : t.sourceOwnFeed
  if (r.source === 'category') return t.sourceInherited(r.inheritedFrom ?? '')
  return t.sourceDefault
}
