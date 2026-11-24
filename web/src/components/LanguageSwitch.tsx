import clsx from 'clsx'

import { LOCALE_NAMES, LOCALES, localePref } from '../i18n'

/**
 * Choix de la langue hors des réglages (écran de connexion) : chaque langue nommée dans sa propre
 * langue. Le changement remonte l'appli (`LocaleRoot`) : à proposer avant toute saisie.
 */
export function LanguageSwitch({ className }: { className?: string }) {
  const current = localePref.useValue()
  return (
    <div className={clsx('flex items-center justify-center gap-1 text-xs text-muted', className)}>
      {LOCALES.map((l, i) => (
        <span key={l} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden>·</span>}
          <button
            type="button"
            lang={l}
            aria-pressed={l === current}
            onClick={() => localePref.set(l)}
            className={clsx('rounded px-1 hover:text-app', l === current && 'font-semibold text-app')}
          >
            {LOCALE_NAMES[l]}
          </button>
        </span>
      ))}
    </div>
  )
}
