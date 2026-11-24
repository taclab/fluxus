/**
 * Ordre manuel de la barre latérale : modèle brouillon pendant un glisser-déposer, règles de
 * déplacement et calcul du nouvel ordre à envoyer au serveur. Aucune dépendance React.
 *
 * La liste racine mélange dossiers et flux sans dossier, triés ensemble par `position` : un flux
 * sans dossier se comporte comme un dossier (avant, entre ou après les dossiers).
 */

import type { Category, Feed, Schemas, SmartFeed } from '../../api/client'
import { compareText } from '../../i18n'

export type RootItem = { kind: 'cat'; id: number } | { kind: 'feed'; id: number }

export interface Draft {
  /** Dossiers et flux sans dossier, dans l'ordre affiché. */
  root: RootItem[]
  /** Flux de chaque dossier, dans l'ordre affiché. */
  feeds: Map<number, number[]>
  smartFeeds: number[]
}

export type DragRef = { kind: 'cat'; id: number } | { kind: 'feed'; id: number } | { kind: 'smart'; id: number }

/** Où déposer un flux survolant l'en-tête d'un dossier : avant lui à la racine, ou dedans en tête. */
export type HeaderZone = 'before' | 'into'

export function dragId(ref: DragRef | RootItem): string {
  return `${ref.kind}:${ref.id}`
}

export function parseDragId(id: string | number): DragRef | null {
  const [kind, raw] = String(id).split(':')
  const n = Number(raw)
  if (!Number.isInteger(n)) return null
  if (kind === 'cat' || kind === 'feed' || kind === 'smart') return { kind, id: n }
  return null
}

/** Tri d'affichage : position manuelle, puis titre pour les égalités (anciennes données sans position). */
export function compareFeeds(a: Feed, b: Feed): number {
  return (a.position ?? 0) - (b.position ?? 0) || compareText(a.title, b.title)
}

export function groupFeeds(feeds: Feed[]): Map<number | null, Feed[]> {
  const byContainer = new Map<number | null, Feed[]>()
  for (const f of feeds) {
    const key = f.category_id ?? null
    if (!byContainer.has(key)) byContainer.set(key, [])
    byContainer.get(key)!.push(f)
  }
  for (const list of byContainer.values()) list.sort(compareFeeds)
  return byContainer
}

export function buildDraft(categories: Category[], feeds: Feed[], smartFeeds: SmartFeed[]): Draft {
  const grouped = groupFeeds(feeds)
  const rootEntries: { item: RootItem; position: number; title: string }[] = [
    ...categories.map((c) => ({ item: { kind: 'cat', id: c.id } as RootItem, position: c.position ?? 0, title: c.title })),
    ...(grouped.get(null) ?? []).map((f) => ({ item: { kind: 'feed', id: f.id } as RootItem, position: f.position ?? 0, title: f.title })),
  ]
  rootEntries.sort((a, b) => a.position - b.position || compareText(a.title, b.title))
  const map = new Map<number, number[]>()
  for (const [key, list] of grouped) if (key !== null) map.set(key, list.map((f) => f.id))
  for (const c of categories) if (!map.has(c.id)) map.set(c.id, [])
  return { root: rootEntries.map((e) => e.item), feeds: map, smartFeeds: smartFeeds.map((s) => s.id) }
}

function arrayMove<T>(list: T[], from: number, to: number): T[] {
  const next = list.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

function rootIndex(draft: Draft, ref: RootItem): number {
  return draft.root.findIndex((r) => r.kind === ref.kind && r.id === ref.id)
}

/** Conteneur d'un flux : id de dossier, null à la racine, undefined si inconnu. */
export function containerOf(draft: Draft, feedId: number): number | null | undefined {
  for (const [key, ids] of draft.feeds) if (ids.includes(feedId)) return key
  return rootIndex(draft, { kind: 'feed', id: feedId }) === -1 ? undefined : null
}

function cloneFeeds(feeds: Map<number, number[]>): Map<number, number[]> {
  return new Map([...feeds].map(([k, v]) => [k, v.slice()]))
}

/** Retire un flux de partout (dossier ou racine). */
function detachFeed(draft: Draft, feedId: number): Draft {
  const feeds = cloneFeeds(draft.feeds)
  for (const list of feeds.values()) {
    const i = list.indexOf(feedId)
    if (i !== -1) list.splice(i, 1)
  }
  return { ...draft, feeds, root: draft.root.filter((r) => !(r.kind === 'feed' && r.id === feedId)) }
}

/** Cibles admises : dossiers et flux racine survolent des éléments racine ; un flux survole des flux ou des en-têtes. */
export function canDropOn(draft: Draft, active: DragRef, over: DragRef): boolean {
  if (active.kind === 'smart') return over.kind === 'smart'
  if (over.kind === 'smart') return false
  if (active.kind === 'cat') return over.kind === 'cat' || (over.kind === 'feed' && containerOf(draft, over.id) === null)
  return true
}

/**
 * Applique un survol : renvoie le nouveau brouillon, ou null si rien ne change.
 * - flux intelligent sur flux intelligent : permutation ;
 * - dossier sur élément racine (dossier ou flux sans dossier) : permutation à la racine ;
 * - flux sur flux : prend la place du survolé, dans son conteneur (dossier ou racine) ;
 * - flux sur en-tête de dossier : zone `into` → en tête du dossier ; zone `before` → à la racine, juste avant.
 */
export function moveInDraft(draft: Draft, active: DragRef, over: DragRef, zone: HeaderZone = 'into'): Draft | null {
  if (!canDropOn(draft, active, over)) return null
  if (active.kind === 'smart' && over.kind === 'smart') {
    const from = draft.smartFeeds.indexOf(active.id)
    const to = draft.smartFeeds.indexOf(over.id)
    if (from === -1 || to === -1 || from === to) return null
    return { ...draft, smartFeeds: arrayMove(draft.smartFeeds, from, to) }
  }
  if (active.kind === 'cat') {
    if (over.kind === 'smart') return null
    const from = rootIndex(draft, active)
    const to = rootIndex(draft, over as RootItem)
    if (from === -1 || to === -1 || from === to) return null
    return { ...draft, root: arrayMove(draft.root, from, to) }
  }
  if (active.kind !== 'feed' || over.kind === 'smart') return null
  const source = containerOf(draft, active.id)
  if (source === undefined) return null
  if (over.kind === 'feed') {
    if (over.id === active.id) return null
    const target = containerOf(draft, over.id)
    if (target === undefined) return null
    if (target === null) {
      // Flux racine survolé : l'actif prend sa place dans la liste racine.
      const to = rootIndex(draft, over as RootItem)
      if (source === null) return { ...draft, root: arrayMove(draft.root, rootIndex(draft, active as RootItem), to) }
      const d = detachFeed(draft, active.id)
      const root = d.root.slice()
      root.splice(rootIndex(d, over as RootItem), 0, { kind: 'feed', id: active.id })
      return { ...d, root }
    }
    // Flux d'un dossier survolé : même sémantique qu'arrayMove (l'actif prend sa place).
    const index = draft.feeds.get(target)!.indexOf(over.id)
    if (source === target) {
      const from = draft.feeds.get(target)!.indexOf(active.id)
      if (from === index) return null
      const feeds = cloneFeeds(draft.feeds)
      feeds.set(target, arrayMove(feeds.get(target)!, from, index))
      return { ...draft, feeds }
    }
    const d = detachFeed(draft, active.id)
    d.feeds.get(target)!.splice(index, 0, active.id)
    return d
  }
  // En-tête de dossier
  if (zone === 'into') {
    if (source === over.id && draft.feeds.get(over.id)![0] === active.id) return null
    const d = detachFeed(draft, active.id)
    if (!d.feeds.has(over.id)) d.feeds.set(over.id, [])
    d.feeds.get(over.id)!.unshift(active.id)
    return d
  }
  // zone 'before' : à la racine, juste avant le dossier
  const catIndex = rootIndex(draft, over as RootItem)
  if (catIndex === -1) return null
  if (source === null && rootIndex(draft, active as RootItem) === catIndex - 1) return null
  const d = detachFeed(draft, active.id)
  const root = d.root.slice()
  root.splice(rootIndex(d, over as RootItem), 0, { kind: 'feed', id: active.id })
  return { ...d, root }
}

export type ReorderBody = Schemas['ReorderIn']

export interface ReorderChanges {
  body: ReorderBody
  /** Lignes locales à écrire dans Dexie pour un affichage immédiat. */
  categories: Category[]
  feeds: Feed[]
  smartFeeds: SmartFeed[]
}

function sameIds(a: number[] | undefined, b: number[] | undefined): boolean {
  const x = a ?? []
  const y = b ?? []
  return x.length === y.length && x.every((v, i) => v === y[i])
}

function sameRoot(a: RootItem[], b: RootItem[]): boolean {
  return a.length === b.length && a.every((v, i) => v.kind === b[i].kind && v.id === b[i].id)
}

/**
 * Compare l'ordre initial et le brouillon final : les listes modifiées sont renumérotées 0..n-1 et
 * renvoyées sous la forme attendue par POST /api/subscriptions/reorder, avec les lignes locales.
 * La racine renumérote ensemble dossiers et flux sans dossier (positions comparables).
 */
export function diffDraft(before: Draft, after: Draft, categories: Category[], feeds: Feed[], smartFeeds: SmartFeed[]): ReorderChanges | null {
  const catById = new Map(categories.map((c) => [c.id, c]))
  const feedById = new Map(feeds.map((f) => [f.id, f]))
  const smartById = new Map(smartFeeds.map((s) => [s.id, s]))
  const out: ReorderChanges = { body: { categories: [], feeds: [], smart_feeds: [] }, categories: [], feeds: [], smartFeeds: [] }

  if (!sameRoot(before.root, after.root)) {
    after.root.forEach((item, position) => {
      if (item.kind === 'cat') {
        const c = catById.get(item.id)
        if (!c) return
        out.body.categories!.push({ id: item.id, position })
        out.categories.push({ ...c, position })
      } else {
        const f = feedById.get(item.id)
        if (!f) return
        out.body.feeds!.push({ id: item.id, position, category_id: null })
        out.feeds.push({ ...f, position, category_id: null })
      }
    })
  }
  if (!sameIds(before.smartFeeds, after.smartFeeds)) {
    after.smartFeeds.forEach((id, position) => {
      const s = smartById.get(id)
      if (!s) return
      out.body.smart_feeds!.push({ id, position })
      out.smartFeeds.push({ ...s, position })
    })
  }
  const containers = new Set<number>([...before.feeds.keys(), ...after.feeds.keys()])
  for (const key of containers) {
    if (sameIds(before.feeds.get(key), after.feeds.get(key))) continue
    ;(after.feeds.get(key) ?? []).forEach((id, position) => {
      const f = feedById.get(id)
      if (!f) return
      out.body.feeds!.push({ id, position, category_id: key })
      out.feeds.push({ ...f, position, category_id: key })
    })
  }
  if (!out.body.categories!.length && !out.body.feeds!.length && !out.body.smart_feeds!.length) return null
  return out
}
