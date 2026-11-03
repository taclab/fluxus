/**
 * Requêtes de liste et de comptage sur les articles locaux, partagées par la liste et les widgets.
 *
 * Un article local porte tout son HTML (`summary_html`, `content_html` des favoris) : lire les objets
 * coûte cher (désérialisation de plusieurs dizaines de Mo sur quelques milliers d'articles). On choisit
 * donc les articles sur les seules clés d'index (curseurs de clés, `primaryKeys`) et on ne charge que
 * ceux qui seront affichés (`bulkGet`).
 */

import { db, type LocalEntry } from './db'

export interface LatestOptions {
  unreadOnly: boolean
  limit: number
}

type Newest = [publishedTs: number, id: number]
type EntryCollection = ReturnType<typeof db.entries.toCollection>

/** Couples (date, id) des `limit` plus récents d'un intervalle d'index terminé par `publishedTs`. */
async function newestKeys(coll: EntryCollection, limit: number): Promise<Newest[]> {
  const out: Newest[] = []
  await coll
    .reverse()
    .limit(limit)
    .eachKey((key, cursor) => {
      const parts = key as number[]
      out.push([parts[parts.length - 1], cursor.primaryKey as number])
    })
  return out
}

/** Articles dans l'ordre des ids, sans ceux supprimés entre la lecture des clés et celle des objets. */
async function load(ids: number[]): Promise<LocalEntry[]> {
  return (await db.entries.bulkGet(ids)).filter((e): e is LocalEntry => e !== undefined)
}

function feedRange(feedId: number, unreadOnly: boolean): EntryCollection {
  return unreadOnly
    ? db.entries.where('[feed_id+readFlag+publishedTs]').between([feedId, 0, -Infinity], [feedId, 0, Infinity])
    : db.entries.where('[feed_id+publishedTs]').between([feedId, -Infinity], [feedId, Infinity])
}

/** Derniers articles de plusieurs flux : clés des `limit` plus récents de chacun, fusion, puis lecture.
 * Une seule transaction : hors transaction, Dexie en ouvre une par requête, et c'est le premier coût. */
export async function latestForFeeds(feedIds: number[], { unreadOnly, limit }: LatestOptions): Promise<LocalEntry[]> {
  if (feedIds.length === 0) return []
  if (feedIds.length === 1) return feedRange(feedIds[0], unreadOnly).reverse().limit(limit).toArray()
  return db.transaction('r', db.entries, async () => {
    const perFeed = await Promise.all(feedIds.map((id) => newestKeys(feedRange(id, unreadOnly), limit)))
    const top = perFeed
      .flat()
      .sort((a, b) => b[0] - a[0] || b[1] - a[1])
      .slice(0, limit)
    return load(top.map(([, id]) => id))
  })
}

/** Ids de tous les articles (ou des non lus), du plus récent au plus ancien : clés seules. */
function idsByDate(unreadOnly: boolean): Promise<number[]> {
  const coll = unreadOnly ? db.entries.where('[readFlag+publishedTs]').between([0, -Infinity], [0, Infinity]) : db.entries.orderBy('publishedTs')
  return coll.reverse().primaryKeys() as Promise<number[]>
}

/** Au-delà, lire les correspondances d'un flux intelligent coûte plus que parcourir les clés par date. */
const SMART_DIRECT_MAX = 500

/** Derniers articles d'un flux intelligent. L'index multiEntry ne se compose pas avec la date : peu de
 * correspondances, on les lit et on trie ; beaucoup, on croise leurs ids avec l'ordre chronologique des
 * clés et on ne lit que les `limit` retenus. */
export function latestForSmartFeed(smartFeedId: number, opts: LatestOptions): Promise<LocalEntry[]> {
  return db.transaction('r', db.entries, () => smartLatest(smartFeedId, opts))
}

async function smartLatest(smartFeedId: number, { unreadOnly, limit }: LatestOptions): Promise<LocalEntry[]> {
  const matchIds = (await db.entries.where('smart_feed_ids').equals(smartFeedId).primaryKeys()) as number[]
  if (matchIds.length === 0) return []
  if (matchIds.length <= SMART_DIRECT_MAX) {
    const rows = (await load(matchIds)).filter((e) => !unreadOnly || e.readFlag === 0)
    return rows.sort((a, b) => b.publishedTs - a.publishedTs || b.id - a.id).slice(0, limit)
  }
  const matches = new Set(matchIds)
  const ids: number[] = []
  for (const id of await idsByDate(unreadOnly)) {
    if (!matches.has(id)) continue
    ids.push(id)
    if (ids.length === limit) break
  }
  return load(ids)
}

export interface UnreadCounts {
  byFeed: Map<number, number>
  bySmartFeed: Map<number, number>
  /** Non lus favoris (en-tête de la vue Favoris). */
  starred: number
  total: number
}

export const EMPTY_UNREAD: UnreadCounts = { byFeed: new Map(), bySmartFeed: new Map(), starred: 0, total: 0 }

function sameMap(a: Map<number, number>, b: Map<number, number>): boolean {
  if (a.size !== b.size) return false
  for (const [k, v] of a) if (b.get(k) !== v) return false
  return true
}

/** Mêmes compteurs : la synchro réécrit souvent des articles sans rien changer aux non lus. */
export function sameUnreadCounts(a: UnreadCounts, b: UnreadCounts): boolean {
  return a.total === b.total && a.starred === b.starred && sameMap(a.byFeed, b.byFeed) && sameMap(a.bySmartFeed, b.bySmartFeed)
}

/** Tous les compteurs de non lus sans lire un seul article : comptes natifs de l'index composé par flux,
 * listes de clés croisées avec les non lus pour les flux intelligents et les favoris. */
export function unreadCounts(): Promise<UnreadCounts> {
  // Une seule transaction pour les quelque deux cents requêtes (un compte par flux) : sinon une chacune.
  return db.transaction('r', db.entries, db.feeds, db.smartFeeds, countAll)
}

async function countAll(): Promise<UnreadCounts> {
  const [feedIds, smartIds] = await Promise.all([db.feeds.toCollection().primaryKeys(), db.smartFeeds.toCollection().primaryKeys()])
  const [feedCounts, unreadIds, starredIds, smartMatches] = await Promise.all([
    Promise.all(feedIds.map((id) => db.entries.where('[feed_id+readFlag+publishedTs]').between([id, 0, -Infinity], [id, 0, Infinity]).count())),
    db.entries.where('readFlag').equals(0).primaryKeys(),
    db.entries.where('starredFlag').equals(1).primaryKeys(),
    Promise.all(smartIds.map((id) => db.entries.where('smart_feed_ids').equals(id).primaryKeys())),
  ])
  const unread = new Set(unreadIds)
  const countUnread = (ids: number[]) => ids.reduce((n, id) => (unread.has(id) ? n + 1 : n), 0)
  const byFeed = new Map<number, number>()
  feedIds.forEach((id, i) => feedCounts[i] > 0 && byFeed.set(id, feedCounts[i]))
  const bySmartFeed = new Map<number, number>()
  smartIds.forEach((id, i) => {
    const n = countUnread(smartMatches[i])
    if (n > 0) bySmartFeed.set(id, n)
  })
  return { byFeed, bySmartFeed, starred: countUnread(starredIds), total: unread.size }
}
