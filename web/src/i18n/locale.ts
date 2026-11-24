/**
 * Langue de l'interface : préférence propre à l'appareil, comme le thème. Par défaut, celle du
 * navigateur (français si elle commence par `fr`, anglais sinon ; français hors navigateur).
 */

import { createDevicePref, oneOf } from '../app/devicePref'

export const LOCALES = ['fr', 'en'] as const
export type Locale = (typeof LOCALES)[number]

/** Nom de chaque langue dans cette langue (sélecteur des réglages). */
export const LOCALE_NAMES: Record<Locale, string> = { fr: 'Français', en: 'English' }

/** Balise BCP 47 pour `Intl` et `localeCompare`. */
export const LOCALE_TAGS: Record<Locale, string> = { fr: 'fr-FR', en: 'en-GB' }

function detect(): Locale {
  if (typeof navigator === 'undefined' || !navigator.language) return 'fr'
  return navigator.language.toLowerCase().startsWith('fr') ? 'fr' : 'en'
}

const parseStored = oneOf(LOCALES, 'fr')

export const localePref = createDevicePref<Locale>('fluxus.locale', (raw) => (raw === null ? detect() : parseStored(raw)))

export function getLocale(): Locale {
  return localePref.get()
}

/** Balise BCP 47 de la langue courante. */
export function localeTag(): string {
  return LOCALE_TAGS[getLocale()]
}

/** Comparaison de libellés selon la langue courante (tri alphabétique). */
export function compareText(a: string, b: string, options?: Intl.CollatorOptions): number {
  return a.localeCompare(b, localeTag(), options)
}
