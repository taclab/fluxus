/**
 * Époque de synchronisation connue de ce cache. Elle change à chaque import de sauvegarde sur le
 * serveur : les identifiants du cache désignent alors d'autres contenus. Module sans dépendance vers
 * le client d'API (qui l'annonce dans chaque écriture) ni vers le moteur de sync.
 */

import { getMeta, setMeta } from '../db/db'

export const EPOCH_KEY = 'sync.epoch'
/** Époque d'une instance jamais restaurée (valeur du serveur tant qu'aucun import n'a eu lieu). */
export const INITIAL_EPOCH = '0'
/** Époque marquée périmée après un 409 : ne correspond à aucune époque serveur, donc toute écriture
 * envoyée d'ici la prochaine passe est refusée, et la passe vide le cache. */
export const STALE_EPOCH = 'périmée'
/** En-tête des écritures et code du refus (`maintenance.epoch_guard` côté serveur). */
export const EPOCH_HEADER = 'X-Fluxus-Epoch'
export const STALE_EPOCH_CODE = 'stale_epoch'
/** Émis sur `window` quand le serveur refuse une écriture pour époque périmée : le moteur resynchronise. */
export const STALE_EPOCH_EVENT = 'fluxus:stale-epoch'

/** Époque connue de ce cache, à joindre à toute écriture rejouable (file hors ligne). */
export async function knownEpoch(): Promise<string | null> {
  return (await getMeta(EPOCH_KEY)) ?? null
}

/** Marque l'époque périmée (pas effacée : une écriture sans époque passerait sur une instance jamais restaurée). */
export async function markEpochStale(): Promise<void> {
  await setMeta(EPOCH_KEY, STALE_EPOCH)
}
