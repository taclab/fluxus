import { describe, expect, it } from 'vitest'

import { category, feed, smartFeed } from '../../test/fixtures'
import { isViewMode, layoutOf, modesFor, resolveViewMode, sourceLabel, targetKey, targetOf } from './viewMode'

describe('resolveViewMode', () => {
  it('flux : sa présentation d’abord', () => {
    const r = resolveViewMode({ kind: 'feed', id: 5 }, { feed: feed(5, { view_mode: 'grid' }), category: category(1, { view_mode: 'list' }), fallback: 'compact' })
    expect(r).toMatchObject({ mode: 'grid', source: 'own', target: { kind: 'feed', id: 5 } })
  })

  it('flux sans présentation : celle de son dossier', () => {
    const r = resolveViewMode({ kind: 'feed', id: 5 }, { feed: feed(5), category: category(1, { title: 'Presse', view_mode: 'list' }), fallback: 'compact' })
    expect(r).toMatchObject({ mode: 'list', source: 'category', inheritedFrom: 'Presse' })
    expect(sourceLabel(r)).toBe('Hérité du dossier « Presse »')
  })

  it('flux et dossier sans présentation : le défaut', () => {
    const r = resolveViewMode({ kind: 'feed', id: 5 }, { feed: feed(5), category: category(1), fallback: 'magazine' })
    expect(r).toMatchObject({ mode: 'magazine', source: 'default', target: { kind: 'feed', id: 5 } })
  })

  it('dossier et flux intelligent : la leur, sinon le défaut', () => {
    expect(resolveViewMode({ kind: 'category', id: 1 }, { category: category(1, { view_mode: 'cards' }), fallback: 'compact' }).mode).toBe('cards')
    expect(resolveViewMode({ kind: 'smart', id: 2 }, { smartFeed: smartFeed(2, { view_mode: 'titles' }), fallback: 'compact' }).mode).toBe('titles')
    expect(resolveViewMode({ kind: 'smart', id: 2 }, { smartFeed: smartFeed(2), fallback: 'compact' })).toMatchObject({ mode: 'compact', source: 'default' })
  })

  it('vues transversales : toujours le défaut, et c’est lui que le menu modifie', () => {
    for (const kind of ['all', 'unread', 'starred', 'search'] as const) {
      const r = resolveViewMode({ kind }, { feed: feed(5, { view_mode: 'grid' }), fallback: 'list' })
      expect(r).toMatchObject({ mode: 'list', source: 'default', target: { kind: 'default' } })
    }
  })

  it('ignore une présentation inconnue (écrite par une version plus récente)', () => {
    const r = resolveViewMode({ kind: 'feed', id: 5 }, { feed: feed(5, { view_mode: 'mosaïque' as never }), fallback: 'compact' })
    expect(r).toMatchObject({ mode: 'compact', source: 'default' })
  })
})

describe('présentations', () => {
  it('la disposition se déduit de la présentation', () => {
    expect(layoutOf('compact')).toBe('split')
    expect(layoutOf('cards')).toBe('split')
    expect(layoutOf('grid')).toBe('full')
    expect(modesFor('split')).toEqual(['compact', 'cards'])
    expect(modesFor('full')).toEqual(['grid', 'magazine', 'list', 'titles'])
  })

  it('isViewMode ne reconnaît que les présentations de la table', () => {
    expect(isViewMode('titles')).toBe(true)
    expect(isViewMode('autre')).toBe(false)
    expect(isViewMode(null)).toBe(false)
  })

  it('cible du menu : l’objet affiché, ou le défaut', () => {
    expect(targetOf({ kind: 'category', id: 3 })).toEqual({ kind: 'category', id: 3 })
    expect(targetOf({ kind: 'feed' })).toEqual({ kind: 'default' })
    expect(targetKey({ kind: 'smart', id: 4 })).toBe('smart:4')
    expect(targetKey({ kind: 'default' })).toBe('default')
  })
})
