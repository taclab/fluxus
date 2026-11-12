import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createDevicePref, idList, oneOf } from './devicePref'

beforeEach(() => localStorage.clear())

describe('createDevicePref', () => {
  it('valide la valeur stockée et se replie sur le défaut', () => {
    const pref = createDevicePref('test.theme', oneOf(['light', 'dark', 'system'] as const, 'system'))
    expect(pref.get()).toBe('system')
    localStorage.setItem('test.theme', 'fluo')
    expect(pref.get()).toBe('system')
    pref.set('dark')
    expect(pref.get()).toBe('dark')
    expect(localStorage.getItem('test.theme')).toBe('dark')
  })

  it('rend la même référence tant que la valeur stockée ne change pas (objet ou liste)', () => {
    const pref = createDevicePref<number[]>('test.ids', idList, JSON.stringify)
    pref.set([3, 1])
    const first = pref.get()
    expect(first).toEqual([3, 1])
    expect(pref.get()).toBe(first)
    pref.set([3])
    expect(pref.get()).not.toBe(first)
  })

  it('prévient ses abonnés à chaque écriture, pas ceux des autres préférences', () => {
    const a = createDevicePref('test.a', oneOf(['x', 'y'] as const, 'x'))
    const b = createDevicePref('test.b', oneOf(['x', 'y'] as const, 'x'))
    const seen: string[] = []
    const listener = (e: Event) => seen.push((e as CustomEvent<string>).detail)
    window.addEventListener('fluxus:pref', listener)
    a.set('y')
    window.removeEventListener('fluxus:pref', listener)
    expect(seen).toEqual(['test.a'])
    expect(b.get()).toBe('x')
  })

  it('un stockage indisponible ne lève pas', () => {
    const pref = createDevicePref('test.ko', oneOf(['x', 'y'] as const, 'x'))
    const broken = vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(pref.get()).toBe('x')
    broken.mockRestore()
  })
})

describe('idList', () => {
  it('ne garde que des entiers et tolère une valeur corrompue', () => {
    expect(idList('[1, "2", 3.5, 4]')).toEqual([1, 4])
    expect(idList('{"a":1}')).toEqual([])
    expect(idList('pas du json')).toEqual([])
    expect(idList(null)).toEqual([])
  })
})
