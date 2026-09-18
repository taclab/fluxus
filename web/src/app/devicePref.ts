/**
 * Préférence propre à l'appareil (localStorage) : une seule fabrique pour toutes (disposition,
 * présentation de la liste…), avec validation de la valeur stockée, abonnement React et
 * synchronisation entre onglets (événement `storage`).
 */

import { useSyncExternalStore } from 'react'

export interface DevicePref<T extends string> {
  get(): T
  set(value: T): void
  /** Hook : valeur courante, mise à jour à chaque `set` (y compris depuis un autre onglet). */
  useValue(): T
}

const EVENT = 'fluxus:pref'

export function createDevicePref<T extends string>(key: string, parse: (raw: string | null) => T): DevicePref<T> {
  const get = () => parse(localStorage.getItem(key))
  const set = (value: T) => {
    localStorage.setItem(key, value)
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
