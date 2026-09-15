/**
 * Ordre manuel de la barre latérale : modèle brouillon pendant un glisser-déposer, règles de
 * déplacement et calcul du nouvel ordre à envoyer au serveur. Aucune dépendance React.
 */

import type { Category, Feed, Schemas, SmartFeed } from '../../api/client'

/** Clé d'un conteneur de flux : identifiant de dossier, ou 'none' pour « sans dossier ». */
export type ContainerKey = number | 'none'
export const UNCATEGORIZED_ID = 'uncategorized'

export interface Draft {
  categories: number[]
  /** Flux par conteneur, dans l'ordre affiché. */
  feeds: Map<ContainerKey, number[]>
  smartFeeds: number[]
}

export type DragRef = { kind: 'cat'; id: number } | { kind: 'feed'; id: number } | { kind: 'smart'; id: number } | { kind: 'uncategorized' }

export function dragId(ref: DragRef): string {
  return ref.kind === 'uncategorized' ? UNCATEGORIZED_ID : `${ref.kind}:${ref.id}`
}

export function parseDragId(id: string | number): DragRef | null {
  const s = String(id)
  if (s === UNCATEGORIZED_ID) return { kind: 'uncategorized' }
  const [kind, raw] = s.split(':')
  const n = Number(raw)
  if (!Number.isInteger(n)) return null
  if (kind === 'cat' || kind === 'feed' || kind === 'smart') return { kind, id: n }
  return null
}

/** Cibles admises pour un élément saisi : un dossier ne survole jamais un flux, et inversement. */
export function canDropOn(active: DragRef, over: DragRef): boolean {
  if (active.kind === 'cat') return over.kind === 'cat'
  if (active.kind === 'smart') return over.kind === 'smart'
  if (active.kind === 'feed') return over.kind === 'feed' || over.kind === 'cat' || over.kind === 'uncategorized'
  return false
}

export function containerOf(feed: Pick<Feed, 'category_id'>): ContainerKey {
  return feed.category_id ?? 'none'
}

/** Tri d'affichage des flux : position manuelle, puis titre pour les égalités (anciennes données sans position). */
export function compareFeeds(a: Feed, b: Feed): number {
  return (a.position ?? 0) - (b.position ?? 0) || a.title.localeCompare(b.title, 'fr')
}

export function groupFeeds(feeds: Feed[]): Map<ContainerKey, Feed[]> {
  const byContainer = new Map<ContainerKey, Feed[]>()
  for (const f of feeds) {
    const key = containerOf(f)
    if (!byContainer.has(key)) byContainer.set(key, [])
    byContainer.get(key)!.push(f)
  }
  for (const list of byContainer.values()) list.sort(compareFeeds)
  return byContainer
}

export function buildDraft(categories: Category[], feeds: Feed[], smartFeeds: SmartFeed[]): Draft {
  const grouped = groupFeeds(feeds)
  const map = new Map<ContainerKey, number[]>()
  for (const [key, list] of grouped) map.set(key, list.map((f) => f.id))
  return { categories: categories.map((c) => c.id), feeds: map, smartFeeds: smartFeeds.map((s) => s.id) }
}

function arrayMove<T>(list: T[], from: number, to: number): T[] {
  const next = list.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

function findContainer(draft: Draft, feedId: number): ContainerKey | null {
  for (const [key, ids] of draft.feeds) if (ids.includes(feedId)) return key
  return null
}

function cloneFeeds(feeds: Map<ContainerKey, number[]>): Map<ContainerKey, number[]> {
  return new Map([...feeds].map(([k, v]) => [k, v.slice()]))
}

/** Place `feedId` à `index` dans `target`, en le retirant de son conteneur actuel. */
function placeFeed(draft: Draft, feedId: number, target: ContainerKey, index: number): Draft | null {
  const source = findContainer(draft, feedId)
  if (source === null) return null
  const feeds = cloneFeeds(draft.feeds)
  const sourceList = feeds.get(source)!
  const from = sourceList.indexOf(feedId)
  if (source === target && from === index) return null
  sourceList.splice(from, 1)
  if (!feeds.has(target)) feeds.set(target, [])
  const targetList = feeds.get(target)!
  targetList.splice(Math.min(index, targetList.length), 0, feedId)
  return { ...draft, feeds }
}

/**
 * Applique un survol : renvoie le nouveau brouillon, ou null si rien ne change.
 * - dossier sur dossier, flux intelligent sur flux intelligent : permutation ;
 * - flux sur flux : prend la place du flux survolé, dans son conteneur ;
 * - flux sur en-tête de dossier ou sur « sans dossier » : passe en tête de ce conteneur.
 */
export function moveInDraft(draft: Draft, active: DragRef, over: DragRef): Draft | null {
  if (!canDropOn(active, over)) return null
  if (active.kind === 'cat' && over.kind === 'cat') {
    const from = draft.categories.indexOf(active.id)
    const to = draft.categories.indexOf(over.id)
    if (from === -1 || to === -1 || from === to) return null
    return { ...draft, categories: arrayMove(draft.categories, from, to) }
  }
  if (active.kind === 'smart' && over.kind === 'smart') {
    const from = draft.smartFeeds.indexOf(active.id)
    const to = draft.smartFeeds.indexOf(over.id)
    if (from === -1 || to === -1 || from === to) return null
    return { ...draft, smartFeeds: arrayMove(draft.smartFeeds, from, to) }
  }
  if (active.kind !== 'feed') return null
  if (over.kind === 'feed') {
    if (over.id === active.id) return null
    const target = findContainer(draft, over.id)
    if (target === null) return null
    // Index du survolé avant retrait de l'actif : même sémantique qu'arrayMove (l'actif prend sa place).
    return placeFeed(draft, active.id, target, draft.feeds.get(target)!.indexOf(over.id))
  }
  const target: ContainerKey = over.kind === 'cat' ? over.id : 'none'
  const source = findContainer(draft, active.id)
  if (source === target && draft.feeds.get(target)![0] === active.id) return null
  return placeFeed(draft, active.id, target, 0)
}

export type ReorderBody = Schemas['ReorderIn']

export interface ReorderChanges {
  body: ReorderBody
  /** Lignes locales à écrire dans Dexie pour un affichage immédiat. */
  categories: Category[]
  feeds: Feed[]
  smartFeeds: SmartFeed[]
}

function sameList(a: number[] | undefined, b: number[] | undefined): boolean {
  if (!a || !b) return a === b || ((a?.length ?? 0) === 0 && (b?.length ?? 0) === 0)
  return a.length === b.length && a.every((v, i) => v === b[i])
}

/**
 * Compare l'ordre initial et le brouillon final : les listes modifiées sont renumérotées 0..n-1 et
 * renvoyées sous la forme attendue par POST /api/subscriptions/reorder, avec les lignes locales.
 */
export function diffDraft(before: Draft, after: Draft, categories: Category[], feeds: Feed[], smartFeeds: SmartFeed[]): ReorderChanges | null {
  const catById = new Map(categories.map((c) => [c.id, c]))
  const feedById = new Map(feeds.map((f) => [f.id, f]))
  const smartById = new Map(smartFeeds.map((s) => [s.id, s]))
  const out: ReorderChanges = { body: { categories: [], feeds: [], smart_feeds: [] }, categories: [], feeds: [], smartFeeds: [] }

  if (!sameList(before.categories, after.categories)) {
    after.categories.forEach((id, position) => {
      const c = catById.get(id)
      if (!c) return
      out.body.categories!.push({ id, position })
      out.categories.push({ ...c, position })
    })
  }
  if (!sameList(before.smartFeeds, after.smartFeeds)) {
    after.smartFeeds.forEach((id, position) => {
      const s = smartById.get(id)
      if (!s) return
      out.body.smart_feeds!.push({ id, position })
      out.smartFeeds.push({ ...s, position })
    })
  }
  const containers = new Set<ContainerKey>([...before.feeds.keys(), ...after.feeds.keys()])
  for (const key of containers) {
    if (sameList(before.feeds.get(key), after.feeds.get(key))) continue
    const category_id = key === 'none' ? null : key
    ;(after.feeds.get(key) ?? []).forEach((id, position) => {
      const f = feedById.get(id)
      if (!f) return
      out.body.feeds!.push({ id, position, category_id })
      out.feeds.push({ ...f, position, category_id })
    })
  }
  if (!out.body.categories!.length && !out.body.feeds!.length && !out.body.smart_feeds!.length) return null
  return out
}
