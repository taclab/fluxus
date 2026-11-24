import { afterEach, describe, expect, it, vi } from 'vitest'

import { agoDate, formatBytes, formatTokens, fullDate, relativeDate, untilDate } from '../lib/format'
import { getT, localePref, type Messages } from '.'

afterEach(() => {
  localePref.set('fr')
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Parcourt deux catalogues en parallèle et relève toute divergence de forme. */
function diff(fr: unknown, en: unknown, path = ''): string[] {
  if (typeof fr === 'function') {
    if (typeof en !== 'function') return [`${path} : fonction attendue`]
    return fr.length === en.length ? [] : [`${path} : ${fr.length} paramètre(s) en français, ${en.length} en anglais`]
  }
  if (typeof fr === 'string') {
    if (typeof en !== 'string') return [`${path} : texte attendu`]
    return en.trim() === '' && fr.trim() !== '' ? [`${path} : traduction vide`] : []
  }
  if (fr && typeof fr === 'object') {
    if (!en || typeof en !== 'object') return [`${path} : objet attendu`]
    const keys = new Set([...Object.keys(fr), ...Object.keys(en)])
    return [...keys].flatMap((k) =>
      k in fr && k in en ? diff((fr as Record<string, unknown>)[k], (en as Record<string, unknown>)[k], `${path}.${k}`) : [`${path}.${k} : clé absente d'un côté`],
    )
  }
  return []
}

describe('catalogues', () => {
  it('l’anglais a exactement la forme du français', () => {
    expect(diff(getT('fr'), getT('en'))).toEqual([])
  })

  it('les fonctions de message rendent du texte dans les deux langues', () => {
    const walk = (node: unknown): void => {
      if (typeof node === 'function') {
        const out = (node as (...a: unknown[]) => unknown)(...Array.from({ length: node.length }, () => 2))
        expect(typeof out === 'string' || typeof out === 'object').toBe(true)
      } else if (node && typeof node === 'object') Object.values(node).forEach(walk)
    }
    walk(getT('fr') satisfies Messages)
    walk(getT('en'))
  })
})

describe('langue de l’appareil', () => {
  it('suit le navigateur tant que rien n’est choisi', () => {
    localStorage.removeItem('fluxus.locale')
    vi.stubGlobal('navigator', { language: 'en-US' })
    expect(localePref.get()).toBe('en')
    vi.stubGlobal('navigator', { language: 'fr-CA' })
    localStorage.setItem('fluxus.locale', 'x') // valeur invalide : invalide le cache, repli sur le français
    expect(localePref.get()).toBe('fr')
  })

  it('le choix enregistré l’emporte', () => {
    vi.stubGlobal('navigator', { language: 'fr-FR' })
    localePref.set('en')
    expect(getT().common.cancel).toBe('Cancel')
    localePref.set('fr')
    expect(getT().common.cancel).toBe('Annuler')
  })
})

describe('formats selon la langue', () => {
  it('dates relatives', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-24T12:00:00'))
    const fiveMinAgo = new Date('2026-11-24T11:55:00').toISOString()
    const inTwoHours = new Date('2026-11-24T14:00:00').toISOString()
    expect(agoDate(fiveMinAgo)).toBe('il y a 5 min')
    expect(untilDate(inTwoHours)).toBe('dans 2 h')
    expect(relativeDate(new Date('2026-03-03T10:00:00').toISOString())).toBe('3 mars')
    expect(fullDate(new Date('2026-11-24T09:05:00').toISOString())).toBe("aujourd'hui à 09:05")
    localePref.set('en')
    expect(agoDate(fiveMinAgo)).toBe('5 min ago')
    expect(untilDate(inTwoHours)).toBe('in 2 h')
    expect(relativeDate(new Date('2026-03-03T10:00:00').toISOString())).toBe('3 Mar')
    expect(fullDate(new Date('2026-11-24T09:05:00').toISOString())).toBe('today at 09:05')
  })

  it('tailles et nombres', () => {
    expect(formatTokens(1500)).toBe('1,5 k')
    expect(formatBytes(1.5 * 1024 ** 3)).toBe('1,5 Go')
    localePref.set('en')
    expect(formatTokens(1500)).toBe('1.5 k')
    expect(formatBytes(1.5 * 1024 ** 3)).toBe('1.5 GB')
  })
})
