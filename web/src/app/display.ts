/**
 * Disposition de la consultation, propre à l'appareil (localStorage) :
 * - `split` : liste à gauche, article à droite (défaut) ;
 * - `full`  : liste sur toute la largeur, article dans une fenêtre centrée (← → pour naviguer).
 * Se change uniquement dans Paramètres → Affichage.
 */

import { useSyncExternalStore } from 'react'

export type ListLayout = 'split' | 'full'

const KEY = 'fluxus.listLayout'
const EVENT = 'fluxus:display'

export function getListLayout(): ListLayout {
  return localStorage.getItem(KEY) === 'full' ? 'full' : 'split'
}

export function setListLayout(layout: ListLayout): void {
  localStorage.setItem(KEY, layout)
  window.dispatchEvent(new Event(EVENT))
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb)
  window.addEventListener('storage', cb)
  return () => {
    window.removeEventListener(EVENT, cb)
    window.removeEventListener('storage', cb)
  }
}

export function useListLayout(): ListLayout {
  return useSyncExternalStore(subscribe, getListLayout, () => 'split')
}
