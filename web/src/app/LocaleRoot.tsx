import { useEffect } from 'react'

import { localePref } from '../i18n'
import { App } from './App'

/**
 * Racine de l'appli, remontée à chaque changement de langue : les textes calculés hors React
 * (dates, toasts, libellés de modules) sont recalculés avec le nouveau catalogue.
 */
export function LocaleRoot() {
  const locale = localePref.useValue()
  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])
  return <App key={locale} />
}
