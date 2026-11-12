/**
 * Préférence propre à l'appareil (localStorage) : une seule fabrique pour toutes (thème, typographie,
 * dossiers repliés…), avec validation de la valeur stockée, abonnement React et synchronisation
 * entre onglets (événement `storage`).
 */

import { useSyncExternalStore } from 'react'

export interface DevicePref<T> {
  get(): T
  set(value: T): void
  /** Hook : valeur courante, mise à jour à chaque `set` (y compris depuis un autre onglet). */
  useValue(): T
}

const EVENT = 'fluxus:pref'

/** `parse` valide la valeur stockée (ou son absence) ; `serialize` l'écrit (`JSON.stringify` pour un objet). */
export function createDevicePref<T>(key: string, parse: (raw: string | null) => T, serialize: (value: T) => string = String): DevicePref<T> {
  // La valeur n'est recalculée que si la chaîne stockée change : `useSyncExternalStore` exige une
  // référence stable, et un objet reconstruit à chaque lecture relancerait le rendu sans fin.
  let cachedRaw: string | null | undefined
  let cached: T
  const read = (): string | null => {
    try {
      return localStorage.getItem(key)
    } catch {
      return null // stockage indisponible (navigation privée stricte) : valeur par défaut
    }
  }
  const get = () => {
    const raw = read()
    if (raw !== cachedRaw || cachedRaw === undefined) {
      cachedRaw = raw
      cached = parse(raw)
    }
    return cached
  }
  const set = (value: T) => {
    try {
      localStorage.setItem(key, serialize(value))
    } catch {
      return
    }
    window.dispatchEvent(new CustomEvent(EVENT, { detail: key }))
  }
  const subscribe = (cb: () => void) => {
    const onPref = (e: Event) => {
      if ((e as CustomEvent<string>).detail === key) cb()
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === key) cb()
    }
    window.addEventListener(EVENT, onPref)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(EVENT, onPref)
      window.removeEventListener('storage', onStorage)
    }
  }
  const useValue = () => useSyncExternalStore(subscribe, get, get)
  return { get, set, useValue }
}

/** Analyseur : la valeur stockée doit appartenir à la liste, sinon repli sur `fallback`. */
export function oneOf<T extends string>(values: readonly T[], fallback: T): (raw: string | null) => T {
  return (raw) => (raw !== null && (values as readonly string[]).includes(raw) ? (raw as T) : fallback)
}

/** Analyseur : liste d'identifiants entiers en JSON, vide si la valeur est absente ou invalide. */
export function idList(raw: string | null): number[] {
  try {
    const value: unknown = JSON.parse(raw ?? '[]')
    return Array.isArray(value) ? value.filter((v): v is number => Number.isInteger(v)) : []
  } catch {
    return []
  }
}
