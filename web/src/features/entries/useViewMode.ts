/**
 * Présentation de la liste : lecture résolue d'une vue (Dexie), écriture optimiste et envoi au serveur.
 *
 * - le défaut global est gardé dans `meta` (clé `viewMode.default`), reçu à chaque synchronisation ;
 * - un choix est appliqué tout de suite dans Dexie, puis envoyé par `PUT /api/view-mode` ;
 * - hors ligne (ou en échec), il reste dans la file `viewMode.pending` et est rejoué avant la passe de
 *   synchronisation suivante ; `mergeFeeds` le réapplique tant qu'il n'est pas parti (dernier choix gagnant).
 */

import { useLiveQuery } from 'dexie-react-hooks'

import { ApiError, api, isOnline, unwrap, type SyncPull } from '../../api/client'
import type { View } from '../../app/view'
import { db, getMeta, setMeta } from '../../db/db'
import { knownEpoch } from '../../sync/engine'
import { DEFAULT_VIEW_MODE, isViewMode, resolveViewMode, targetKey, targetOf, type ResolvedViewMode, type ViewMode, type ViewModeTarget } from './viewMode'

const DEFAULT_KEY = 'viewMode.default'
const PENDING_KEY = 'viewMode.pending'

type ViewModeChoice = ViewMode | 'inherit'
type Pending = Record<string, ViewModeChoice>

async function readPending(): Promise<Pending> {
  try {
    const raw = await getMeta(PENDING_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Pending) : {}
  } catch {
    return {}
  }
}

async function writePending(p: Pending): Promise<void> {
  if (Object.keys(p).length) await setMeta(PENDING_KEY, JSON.stringify(p))
  else await db.meta.delete(PENDING_KEY)
}

function parseKey(key: string): ViewModeTarget | null {
  if (key === 'default') return { kind: 'default' }
  const [kind, raw] = key.split(':')
  const id = Number(raw)
  if ((kind === 'feed' || kind === 'category' || kind === 'smart') && Number.isSafeInteger(id) && id > 0) return { kind, id }
  return null
}

/** Applique un choix dans le cache local (sans passer par le serveur). */
async function applyLocal(target: ViewModeTarget, choice: ViewModeChoice): Promise<void> {
  const value = choice === 'inherit' ? null : choice
  switch (target.kind) {
    case 'default':
      if (value) await setMeta(DEFAULT_KEY, value)
      return
    case 'feed':
      await db.feeds.update(target.id, { view_mode: value })
      return
    case 'category':
      await db.categories.update(target.id, { view_mode: value })
      return
    case 'smart':
      await db.smartFeeds.update(target.id, { view_mode: value })
  }
}

async function send(target: ViewModeTarget, choice: ViewModeChoice): Promise<void> {
  // Époque connue : un choix fait avant un import de sauvegarde est refusé (409) plutôt qu'appliqué à
  // l'objet qui porte désormais le même identifiant.
  const epoch = await knownEpoch()
  unwrap(
    await api().PUT('/api/view-mode', {
      body: target.kind === 'default' ? { kind: 'default', view_mode: choice, epoch } : { kind: target.kind, id: target.id, view_mode: choice, epoch },
    }),
  )
}

let flushing: Promise<void> | null = null

/** Envoie les choix en attente ; ceux que le serveur refuse (vue supprimée) sont abandonnés. */
export function flushViewModes(): Promise<void> {
  if (!isOnline()) return Promise.resolve()
  flushing ??= (async () => {
    const pending = await readPending()
    for (const [key, choice] of Object.entries(pending)) {
      const target = parseKey(key)
      let done = target === null
      if (target) {
        try {
          await send(target, choice)
          done = true
        } catch (err) {
          // 4xx : la vue n'existe plus ou la valeur est refusée, inutile de réessayer. Sauf session
          // expirée (401), délai (408) ou limitation (429) : le choix reste en file pour la suite.
          done = err instanceof ApiError && err.status >= 400 && err.status < 500 && ![401, 408, 429].includes(err.status)
          if (!done) break
        }
      }
      if (done) {
        const latest = await readPending()
        // Un nouveau choix a pu être fait pendant l'envoi : il reste en file.
        if (latest[key] === choice) {
          delete latest[key]
          await writePending(latest)
        }
      }
    }
  })().finally(() => {
    flushing = null
  })
  return flushing
}

/** Choix depuis l'en-tête d'une vue, l'écran Abonnements ou les paramètres. */
export async function setViewMode(target: ViewModeTarget, choice: ViewModeChoice): Promise<void> {
  await applyLocal(target, choice)
  const pending = await readPending()
  pending[targetKey(target)] = choice
  await writePending(pending)
  // Un envoi déjà en cours a lu la file avant ce choix : on l'attend, puis on relance.
  if (flushing) await flushing
  await flushViewModes()
}

/** Choix envoyés par un autre chemin (fenêtre du flux, action groupée) : la file ne doit plus les écraser. */
export async function forgetPendingViewModes(targets: ViewModeTarget[]): Promise<void> {
  const pending = await readPending()
  let changed = false
  for (const t of targets) {
    const key = targetKey(t)
    if (key in pending) {
      delete pending[key]
      changed = true
    }
  }
  if (changed) await writePending(pending)
}

/**
 * Appelé par la synchronisation, dans la transaction qui remplace flux, dossiers et flux intelligents
 * (tables `feeds`, `categories`, `smartFeeds`, `meta`) : enregistre le défaut serveur puis réapplique
 * les choix pas encore envoyés, sans instant intermédiaire où l'ancienne valeur réapparaîtrait.
 */
export async function mergeViewModes(page: SyncPull): Promise<void> {
  const pending = await readPending()
  if (!('default' in pending) && isViewMode(page.default_view_mode)) await setMeta(DEFAULT_KEY, page.default_view_mode)
  for (const [key, choice] of Object.entries(pending)) {
    const target = parseKey(key)
    if (target) await applyLocal(target, choice)
  }
}

/**
 * Reprise, une seule fois, des anciennes préférences d'appareil (disposition + présentation globales) :
 * si le serveur a encore le défaut d'origine, la présentation active de cet appareil devient le défaut.
 */
export async function migrateLegacyDisplay(serverDefault: string | undefined): Promise<void> {
  const keys = ['fluxus.listLayout', 'fluxus.density', 'fluxus.fullDensity']
  if (keys.every((k) => localStorage.getItem(k) === null)) return
  const layout = localStorage.getItem('fluxus.listLayout') === 'full' ? 'full' : 'split'
  const raw = layout === 'full' ? localStorage.getItem('fluxus.fullDensity') ?? 'grid' : localStorage.getItem('fluxus.density') ?? 'compact'
  keys.forEach((k) => localStorage.removeItem(k))
  if ((serverDefault ?? DEFAULT_VIEW_MODE) === DEFAULT_VIEW_MODE && isViewMode(raw) && raw !== DEFAULT_VIEW_MODE) await setViewMode({ kind: 'default' }, raw)
}

export function useDefaultViewMode(): ViewMode {
  const raw = useLiveQuery(() => getMeta(DEFAULT_KEY), [])
  return isViewMode(raw) ? raw : DEFAULT_VIEW_MODE
}

/** Présentation résolue de la vue ; `undefined` le temps de la première lecture Dexie. */
export function useViewMode(view: View): ResolvedViewMode | undefined {
  return useLiveQuery(async () => {
    const rawDefault = await getMeta(DEFAULT_KEY)
    const fallback = isViewMode(rawDefault) ? rawDefault : DEFAULT_VIEW_MODE
    const target = targetOf(view)
    if (target.kind === 'default') return resolveViewMode(view, { fallback })
    if (target.kind === 'feed') {
      const feed = await db.feeds.get(target.id)
      const category = feed?.category_id ? await db.categories.get(feed.category_id) : undefined
      return resolveViewMode(view, { feed, category, fallback })
    }
    if (target.kind === 'category') return resolveViewMode(view, { category: await db.categories.get(target.id), fallback })
    return resolveViewMode(view, { smartFeed: await db.smartFeeds.get(target.id), fallback })
  }, [view.kind, view.id])
}
