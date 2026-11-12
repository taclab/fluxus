import { describe, expect, it } from 'vitest'

import { category, feed, smartFeed } from '../../test/fixtures'
import { buildDraft, canDropOn, containerOf, diffDraft, moveInDraft, parseDragId, type DragRef } from './sidebarOrder'

const categories = [category(1, { title: 'A', position: 0 }), category(2, { title: 'B', position: 2 })]
const feeds = [
  feed(10, { category_id: 1, position: 0 }),
  feed(11, { category_id: 1, position: 1 }),
  feed(20, { category_id: 2, position: 0 }),
  feed(30, { position: 1 }),
  feed(31, { position: 3 }),
]
const smartFeeds = [smartFeed(1), smartFeed(2)]
const draft = () => buildDraft(categories, feeds, smartFeeds)

const cat = (id: number): DragRef => ({ kind: 'cat', id })
const fd = (id: number): DragRef => ({ kind: 'feed', id })
const smart = (id: number): DragRef => ({ kind: 'smart', id })
const rootIds = (d: { root: { kind: string; id: number }[] }) => d.root.map((r) => `${r.kind}:${r.id}`)

describe('buildDraft', () => {
  it('mélange dossiers et flux sans dossier à la racine, par position', () => {
    expect(rootIds(draft())).toEqual(['cat:1', 'feed:30', 'cat:2', 'feed:31'])
  })

  it('range les flux de chaque dossier par position et donne une liste vide à un dossier sans flux', () => {
    const d = buildDraft([...categories, category(3, { position: 9 })], feeds, smartFeeds)
    expect(d.feeds.get(1)).toEqual([10, 11])
    expect(d.feeds.get(2)).toEqual([20])
    expect(d.feeds.get(3)).toEqual([])
  })

  it('départage deux positions égales par le titre', () => {
    const d = buildDraft([category(1, { title: 'Zèbre' }), category(2, { title: 'Abeille' })], [], [])
    expect(rootIds(d)).toEqual(['cat:2', 'cat:1'])
  })
})

describe('parseDragId', () => {
  it('relit un identifiant de glisser-déposer et refuse le reste', () => {
    expect(parseDragId('feed:12')).toEqual({ kind: 'feed', id: 12 })
    expect(parseDragId('autre:12')).toBeNull()
    expect(parseDragId('feed:x')).toBeNull()
  })
})

describe('canDropOn', () => {
  it('un dossier ne survole que la racine, un flux intelligent que ses pairs', () => {
    const d = draft()
    expect(canDropOn(d, cat(2), fd(30))).toBe(true)
    expect(canDropOn(d, cat(2), fd(10))).toBe(false)
    expect(canDropOn(d, smart(1), fd(30))).toBe(false)
    expect(canDropOn(d, fd(30), smart(1))).toBe(false)
  })
})

describe('moveInDraft', () => {
  it('dossier sur un flux sans dossier : permutation à la racine', () => {
    expect(rootIds(moveInDraft(draft(), cat(2), fd(30))!)).toEqual(['cat:1', 'cat:2', 'feed:30', 'feed:31'])
  })

  it('dossier sur un flux rangé dans un dossier : refusé', () => {
    expect(moveInDraft(draft(), cat(1), fd(10))).toBeNull()
  })

  it('flux sur un flux d’un autre dossier : prend sa place', () => {
    const d = moveInDraft(draft(), fd(11), fd(20))!
    expect(d.feeds.get(1)).toEqual([10])
    expect(d.feeds.get(2)).toEqual([11, 20])
  })

  it('flux sur un flux du même dossier : permutation, rien si la place est la même', () => {
    expect(moveInDraft(draft(), fd(11), fd(10))!.feeds.get(1)).toEqual([11, 10])
    expect(moveInDraft(draft(), fd(10), fd(10))).toBeNull()
  })

  it('flux sur l’en-tête d’un dossier : en tête du dossier, rien s’il y est déjà', () => {
    const d = moveInDraft(draft(), fd(20), cat(1), 'into')!
    expect(d.feeds.get(1)).toEqual([20, 10, 11])
    expect(d.feeds.get(2)).toEqual([])
    expect(moveInDraft(draft(), fd(10), cat(1), 'into')).toBeNull()
  })

  it('flux sur le bord supérieur d’un en-tête : sort du dossier, juste avant lui à la racine', () => {
    const d = moveInDraft(draft(), fd(10), cat(2), 'before')!
    expect(rootIds(d)).toEqual(['cat:1', 'feed:30', 'feed:10', 'cat:2', 'feed:31'])
    expect(d.feeds.get(1)).toEqual([11])
    expect(containerOf(d, 10)).toBeNull()
    expect(moveInDraft(draft(), fd(30), cat(2), 'before')).toBeNull()
  })

  it('flux sans dossier sur un flux rangé : entre dans le dossier et quitte la racine', () => {
    const d = moveInDraft(draft(), fd(30), fd(11))!
    expect(d.feeds.get(1)).toEqual([10, 30, 11])
    expect(rootIds(d)).toEqual(['cat:1', 'cat:2', 'feed:31'])
  })

  it('flux rangé sur un flux sans dossier : prend sa place à la racine', () => {
    const d = moveInDraft(draft(), fd(10), fd(31))!
    expect(rootIds(d)).toEqual(['cat:1', 'feed:30', 'cat:2', 'feed:10', 'feed:31'])
    expect(d.feeds.get(1)).toEqual([11])
  })

  it('flux intelligents : permutation entre eux seulement', () => {
    expect(moveInDraft(draft(), smart(2), smart(1))!.smartFeeds).toEqual([2, 1])
    expect(moveInDraft(draft(), smart(1), fd(30))).toBeNull()
  })

  it('ne modifie pas le brouillon de départ', () => {
    const d = draft()
    moveInDraft(d, fd(11), fd(20))
    moveInDraft(d, fd(10), cat(2), 'before')
    expect(d).toEqual(draft())
  })
})

describe('diffDraft', () => {
  it('ne renvoie rien si l’ordre n’a pas changé', () => {
    expect(diffDraft(draft(), draft(), categories, feeds, smartFeeds)).toBeNull()
  })

  it('renumérote ensemble dossiers et flux sans dossier quand la racine change', () => {
    const after = moveInDraft(draft(), cat(2), fd(30))!
    const changes = diffDraft(draft(), after, categories, feeds, smartFeeds)!
    expect(changes.body.categories).toEqual([
      { id: 1, position: 0 },
      { id: 2, position: 1 },
    ])
    expect(changes.body.feeds).toEqual([
      { id: 30, position: 2, category_id: null },
      { id: 31, position: 3, category_id: null },
    ])
    expect(changes.body.smart_feeds).toEqual([])
  })

  it('renumérote les deux dossiers touchés par un changement de dossier, et eux seuls', () => {
    const after = moveInDraft(draft(), fd(11), fd(20))!
    const changes = diffDraft(draft(), after, categories, feeds, smartFeeds)!
    expect(changes.body.categories).toEqual([])
    expect(changes.body.feeds).toHaveLength(3)
    expect(changes.body.feeds).toEqual(
      expect.arrayContaining([
        { id: 10, position: 0, category_id: 1 },
        { id: 11, position: 0, category_id: 2 },
        { id: 20, position: 1, category_id: 2 },
      ]),
    )
    expect(changes.feeds.find((f) => f.id === 11)).toMatchObject({ category_id: 2, position: 0 })
  })

  it('un flux sorti de son dossier part avec category_id nul', () => {
    const after = moveInDraft(draft(), fd(10), cat(2), 'before')!
    const changes = diffDraft(draft(), after, categories, feeds, smartFeeds)!
    expect(changes.body.feeds).toEqual(expect.arrayContaining([{ id: 10, position: 2, category_id: null }, { id: 11, position: 0, category_id: 1 }]))
    expect(changes.body.categories).toEqual([
      { id: 1, position: 0 },
      { id: 2, position: 3 },
    ])
  })
})
