/**
 * Traduction de l'interface. Le catalogue français est la référence : chaque catalogue anglais a
 * son type (`typeof fr`), une clé manquante ou en trop casse la compilation. Un message paramétré
 * est une fonction (`t.feeds.unread(n)`), y compris pour les pluriels.
 *
 * - Dans un composant : `const t = useT()`.
 * - Ailleurs (module sans React, toast, erreur) : `getT()` au moment de l'appel, jamais au
 *   chargement du module (la langue peut changer ensuite).
 *
 * Changer de langue remonte toute l'arborescence (`App`, clé = langue) : un texte calculé hors
 * React est donc lui aussi recalculé.
 */

import * as common from './messages/common'
import * as app from './messages/app'
import * as reader from './messages/reader'
import * as entries from './messages/entries'
import * as feeds from './messages/feeds'
import * as subscriptions from './messages/subscriptions'
import * as smartfeeds from './messages/smartfeeds'
import * as dashboard from './messages/dashboard'
import * as settings from './messages/settings'
import { getLocale, localePref, type Locale } from './locale'

const catalogs = {
  fr: {
    common: common.fr,
    app: app.fr,
    reader: reader.fr,
    entries: entries.fr,
    feeds: feeds.fr,
    subscriptions: subscriptions.fr,
    smartfeeds: smartfeeds.fr,
    dashboard: dashboard.fr,
    settings: settings.fr,
  },
  en: {
    common: common.en,
    app: app.en,
    reader: reader.en,
    entries: entries.en,
    feeds: feeds.en,
    subscriptions: subscriptions.en,
    smartfeeds: smartfeeds.en,
    dashboard: dashboard.en,
    settings: settings.en,
  },
} satisfies Record<Locale, unknown>

export type Messages = (typeof catalogs)['fr']

// Vérifie que l'anglais a exactement la forme du français (chaque espace de noms est typé à part).
const _check: Messages = catalogs.en
void _check

export function getT(locale: Locale = getLocale()): Messages {
  return catalogs[locale]
}

export function useT(): Messages {
  return catalogs[localePref.useValue()]
}

export { compareText, getLocale, LOCALE_NAMES, LOCALES, localePref, localeTag, type Locale } from './locale'
