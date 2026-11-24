/**
 * Réglages d'un flux partagés par la fenêtre « Modifier le flux » et l'écran Abonnements :
 * libellés des options et écritures serveur (une réponse serveur remplace la ligne Dexie).
 */

import { api, unwrap, type Feed, type Schemas } from '../../api/client'
import { db } from '../../db/db'
import { getT } from '../../i18n'
import { resyncIfPulling } from '../../sync/engine'
import { forgetPendingViewModes } from '../entries/useViewMode'

export type ExtractionMode = NonNullable<Schemas['FeedSettingsIn']['extraction_mode']>
/** Champs à modifier (les champs absents restent inchangés ; `clear_category` vaut faux par défaut). */
export type FeedChanges = Partial<Schemas['FeedSettingsIn']>
export type FeedUpdateChanges = Partial<Schemas['FeedUpdate']>

/** Libellés lus dans la langue courante à chaque usage (accesseurs), jamais figés au chargement. */
function extractionOption(id: ExtractionMode, key: 'auto' | 'http' | 'browser' | 'none'): { id: ExtractionMode; label: string; short: string } {
  return {
    id,
    get label() {
      return getT().feeds.extraction[`${key}Label`]
    },
    get short() {
      return getT().feeds.extraction[`${key}Short`]
    },
  }
}

export const EXTRACTION_OPTIONS: { id: ExtractionMode; label: string; short: string }[] = [
  extractionOption('auto', 'auto'),
  extractionOption('http', 'http'),
  extractionOption('browser', 'browser'),
  extractionOption('none', 'none'),
]

export function extractionHint(): string {
  return getT().feeds.extraction.hint
}

export interface IntervalOption {
  minutes: number
  /** « Toutes les 15 min », « Automatique (adaptatif) ». */
  label: string
  /** Forme courte pour un tableau : « 15 min », « 24 h » (vide pour l'automatique). */
  short: string
}

function intervalOption(minutes: number): IntervalOption {
  return {
    minutes,
    get label() {
      const t = getT().feeds.interval
      if (minutes === 0) return t.auto
      if (minutes === 60) return t.hourly
      if (minutes === 1440) return t.daily
      return t.every(formatMinutes(minutes))
    },
    get short() {
      return minutes === 0 ? '' : formatMinutes(minutes)
    },
  }
}

/** Intervalles fixes proposés (minutes) ; 0 = adaptatif. */
export const INTERVAL_OPTIONS: IntervalOption[] = [0, 15, 30, 60, 180, 360, 720, 1440].map(intervalOption)

export function intervalHint(): string {
  return getT().feeds.interval.hint
}

export function formatMinutes(min: number): string {
  const t = getT().feeds.interval
  if (min < 60) return t.minutes(min)
  if (min % 1440 === 0) return min === 1440 ? t.hours(24) : t.days(min / 1440)
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? t.hoursMinutes(h, m) : t.hours(h)
}

/** Intervalle affiché : « Auto · 45 min » ou « 3 h ». */
export function intervalLabel(feed: Feed): string {
  return feed.fetch_interval_fixed_min ? formatMinutes(feed.fetch_interval_fixed_min) : getT().feeds.interval.autoValue(formatMinutes(feed.fetch_interval_min))
}

/** Options d'intervalle, complétées par la valeur fixe courante si elle n'est pas dans la liste. */
export function intervalOptions(feed?: Feed): IntervalOption[] {
  const fixed = feed?.fetch_interval_fixed_min
  if (!fixed || INTERVAL_OPTIONS.some((o) => o.minutes === fixed)) return INTERVAL_OPTIONS
  return [...INTERVAL_OPTIONS, intervalOption(fixed)].sort((a, b) => a.minutes - b.minutes)
}

/** Réglages simples appliqués tout de suite dans Dexie (la réponse serveur fait foi ensuite). */
function optimisticPatch(changes: FeedChanges): Partial<Feed> {
  const patch: Partial<Feed> = {}
  if (changes.disabled != null) patch.disabled = changes.disabled
  if (changes.extraction_mode != null) patch.extraction_mode = changes.extraction_mode
  if (changes.auto_summary != null) patch.auto_summary = changes.auto_summary
  if (changes.summary_daily_cap != null) patch.summary_daily_cap = changes.summary_daily_cap
  if (changes.view_mode != null) patch.view_mode = changes.view_mode === 'inherit' ? null : changes.view_mode
  if (changes.fetch_interval_fixed_min != null) patch.fetch_interval_fixed_min = changes.fetch_interval_fixed_min || null
  return patch
}

export async function updateFeed(id: number, changes: FeedUpdateChanges): Promise<Feed> {
  const before = await db.feeds.get(id)
  const patch = optimisticPatch(changes)
  if (before && Object.keys(patch).length) await db.feeds.update(id, patch)
  try {
    const updated = unwrap(await api().PUT('/api/feeds/{feed_id}', { params: { path: { feed_id: id } }, body: { clear_category: false, ...changes } }))
    if (changes.view_mode) await forgetPendingViewModes([{ kind: 'feed', id }])
    await db.feeds.put(updated)
    resyncIfPulling()
    return updated
  } catch (err) {
    if (before) await db.feeds.put(before)
    throw err
  }
}

export async function updateFeeds(ids: number[], changes: FeedChanges): Promise<Feed[]> {
  const out = unwrap(await api().POST('/api/feeds/bulk', { body: { feed_ids: ids, changes: { clear_category: false, ...changes } } }))
  if (changes.view_mode) await forgetPendingViewModes(ids.map((id) => ({ kind: 'feed', id })))
  await db.feeds.bulkPut(out.feeds)
  resyncIfPulling()
  return out.feeds
}

/** Désabonnement : serveur puis nettoyage local (articles et résumés du flux). */
export async function deleteFeeds(ids: number[]): Promise<void> {
  for (const id of ids) {
    unwrap(await api().DELETE('/api/feeds/{feed_id}', { params: { path: { feed_id: id } } }))
    await db.transaction('rw', db.feeds, db.entries, db.summaries, async () => {
      const entryIds = await db.entries.where('feed_id').equals(id).primaryKeys()
      await db.summaries.where('entry_id').anyOf(entryIds).delete()
      await db.entries.bulkDelete(entryIds)
      await db.feeds.delete(id)
    })
  }
}
