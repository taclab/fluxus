/**
 * Disposition de la consultation, propre à l'appareil :
 * - `split` : liste à gauche, article à droite (défaut) ;
 * - `full`  : liste sur toute la largeur, article dans une fenêtre centrée (← → pour naviguer).
 * Se change uniquement dans Paramètres → Affichage.
 */

import { createDevicePref, oneOf } from './devicePref'

export type ListLayout = 'split' | 'full'

export const LIST_LAYOUT = createDevicePref<ListLayout>('fluxus.listLayout', oneOf(['split', 'full'], 'split'))

export const getListLayout = LIST_LAYOUT.get
export const setListLayout = LIST_LAYOUT.set
export function useListLayout(): ListLayout {
  return LIST_LAYOUT.useValue()
}
