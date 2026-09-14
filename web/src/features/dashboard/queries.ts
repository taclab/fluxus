/** Requêtes Dexie des widgets : tout passe par les index, le tri en mémoire est borné à feeds × limit. */

import type { Feed, StatsOut, SummaryOut } from '../../api/client'
import { api, isOnline, unwrap } from '../../api/client'
import { db, getMeta, setMeta, type LocalEntry } from '../../db/db'

export interface LatestOptions {
  unreadOnly: boolean
  limit: number
}

function latestForFeed(feedId: number, { unreadOnly, limit }: LatestOptions): Promise<LocalEntry[]> {
  const coll = unreadOnly
    ? db.entries.where('[feed_id+readFlag+publishedTs]').between([feedId, 0, -Infinity], [feedId, 0, Infinity])
    : db.entries.where('[feed_id+publishedTs]').between([feedId, -Infinity], [feedId, Infinity])
  return coll.reverse().limit(limit).toArray()
}

/** Derniers articles de plusieurs flux : une requête indexée par flux, fusion bornée. */
export async function latestForFeeds(feedIds: number[], opts: LatestOptions): Promise<LocalEntry[]> {
  if (feedIds.length === 0) return []
  if (feedIds.length === 1) return latestForFeed(feedIds[0], opts)
  const perFeed = await Promise.all(feedIds.map((id) => latestForFeed(id, opts)))
  return perFeed
    .flat()
    .sort((a, b) => b.publishedTs - a.publishedTs)
    .slice(0, opts.limit)
}

export function latestAll({ unreadOnly, limit }: LatestOptions): Promise<LocalEntry[]> {
  const coll = unreadOnly ? db.entries.where('[readFlag+publishedTs]').between([0, -Infinity], [0, Infinity]) : db.entries.orderBy('publishedTs')
  return coll.reverse().limit(limit).toArray()
}

export function latestStarred(limit: number): Promise<LocalEntry[]> {
  return db.entries.where('[starredFlag+publishedTs]').between([1, -Infinity], [1, Infinity]).reverse().limit(limit).toArray()
}

export interface SummaryWithEntry {
  summary: SummaryOut
  entry: LocalEntry
}

/** Derniers résumés, joints à leur article (les articles purgés par la rétention sont ignorés). */
export async function latestSummaries(limit: number): Promise<SummaryWithEntry[]> {
  const summaries = await db.summaries.orderBy('id').reverse().limit(limit * 2).toArray()
  const entries = await db.entries.bulkGet(summaries.map((s) => s.entry_id))
  const out: SummaryWithEntry[] = []
  const seen = new Set<number>()
  summaries.forEach((s, i) => {
    const e = entries[i]
    if (e && !seen.has(e.id)) {
      seen.add(e.id)
      out.push({ summary: s, entry: e })
    }
  })
  return out.slice(0, limit)
}

export function startOfToday(): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** Articles lus dont le dernier changement date d'aujourd'hui (approximation acceptable de « lus aujourd'hui »). */
export function readTodayCount(): Promise<number> {
  return db.entries.where('[readFlag+updatedTs]').between([1, startOfToday()], [1, Infinity]).count()
}

export function feedsInError(feeds: Feed[]): Feed[] {
  return feeds.filter((f) => !f.disabled && f.error_count > 2)
}

export function youtubeFeedIds(feeds: Feed[], categoryId: number | null): number[] {
  return feeds.filter((f) => f.is_youtube && (categoryId == null || f.category_id === categoryId)).map((f) => f.id)
}

export function categoryFeedIds(feeds: Feed[], categoryId: number): number[] {
  return feeds.filter((f) => f.category_id === categoryId).map((f) => f.id)
}

const STATS_KEY = 'dashboard.stats'
const STATS_TTL_MS = 10 * 60 * 1000

/** Statistiques serveur (tokens…), rafraîchies au plus toutes les 10 min, servies depuis le cache hors-ligne. */
export async function fetchStatsCached(force = false): Promise<StatsOut | null> {
  const raw = await getMeta(STATS_KEY)
  const cached = raw ? (JSON.parse(raw) as { at: number; stats: StatsOut }) : null
  if (!force && cached && Date.now() - cached.at < STATS_TTL_MS) return cached.stats
  if (!isOnline()) return cached?.stats ?? null
  try {
    const stats = unwrap(await api().GET('/api/settings/stats'))
    await setMeta(STATS_KEY, JSON.stringify({ at: Date.now(), stats }))
    return stats
  } catch {
    return cached?.stats ?? null
  }
}
