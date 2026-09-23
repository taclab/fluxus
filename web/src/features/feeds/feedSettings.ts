/**
 * Réglages d'un flux partagés par la fenêtre « Modifier le flux » et l'écran Abonnements :
 * libellés des options et écritures serveur (une réponse serveur remplace la ligne Dexie).
 */

import { api, unwrap, type Feed, type Schemas } from '../../api/client'
import { db } from '../../db/db'
import { forgetPendingViewModes } from '../entries/useViewMode'

export type ExtractionMode = NonNullable<Schemas['FeedSettingsIn']['extraction_mode']>
/** Champs à modifier (les champs absents restent inchangés ; `clear_category` vaut faux par défaut). */
export type FeedChanges = Partial<Schemas['FeedSettingsIn']>
export type FeedUpdateChanges = Partial<Schemas['FeedUpdate']>

export const EXTRACTION_OPTIONS: { id: ExtractionMode; label: string; short: string }[] = [
  { id: 'auto', label: 'À la demande (bouton ou touche f)', short: 'À la demande' },
  { id: 'http', label: 'Toujours, en HTTP', short: 'Toujours (HTTP)' },
  { id: 'browser', label: 'Toujours, via le navigateur (sites JavaScript)', short: 'Toujours (navigateur)' },
  { id: 'none', label: 'Jamais (contenu du flux seulement)', short: 'Jamais' },
]

export const EXTRACTION_HINT =
  '« À la demande » : l’article complet est récupéré quand vous le demandez depuis le lecteur (HTTP puis navigateur si besoin). « Toujours » : chaque nouvel article est récupéré en arrière-plan, lisible hors ligne et mieux résumé.'

/** Intervalles fixes proposés (minutes) ; 0 = adaptatif. */
export const INTERVAL_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 0, label: 'Automatique (adaptatif)' },
  { minutes: 15, label: 'Toutes les 15 min' },
  { minutes: 30, label: 'Toutes les 30 min' },
  { minutes: 60, label: 'Toutes les heures' },
  { minutes: 180, label: 'Toutes les 3 h' },
  { minutes: 360, label: 'Toutes les 6 h' },
  { minutes: 720, label: 'Toutes les 12 h' },
  { minutes: 1440, label: 'Une fois par jour' },
]

export const INTERVAL_HINT =
  'Automatique : l’intervalle se resserre quand le flux publie beaucoup et s’espace quand il est calme (10 min à 24 h). Un intervalle fixe s’applique tel quel.'

export function formatMinutes(min: number): string {
  if (min < 60) return `${min} min`
  if (min % 1440 === 0) return min === 1440 ? '24 h' : `${min / 1440} j`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `${h} h ${m}` : `${h} h`
}

/** Intervalle affiché : « Auto · 45 min » ou « 3 h ». */
export function intervalLabel(feed: Feed): string {
  return feed.fetch_interval_fixed_min ? formatMinutes(feed.fetch_interval_fixed_min) : `Auto · ${formatMinutes(feed.fetch_interval_min)}`
}

/** Options d'intervalle, complétées par la valeur fixe courante si elle n'est pas dans la liste. */
export function intervalOptions(feed?: Feed): { minutes: number; label: string }[] {
  const fixed = feed?.fetch_interval_fixed_min
  if (!fixed || INTERVAL_OPTIONS.some((o) => o.minutes === fixed)) return INTERVAL_OPTIONS
  return [...INTERVAL_OPTIONS, { minutes: fixed, label: `Toutes les ${formatMinutes(fixed)}` }].sort((a, b) => a.minutes - b.minutes)
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
