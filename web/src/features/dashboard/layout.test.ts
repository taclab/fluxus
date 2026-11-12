import { describe, expect, it } from 'vitest'

import { category, feed, smartFeed } from '../../test/fixtures'
import { buildDefaultLayout, LAYOUT_VERSION, parseLayout, serializeLayout, viewForSource, widgetTitle, type AnyWidget, type NewsWidget } from './layout'

const parseWidgets = (widgets: unknown[]): AnyWidget[] => {
  const parsed = parseLayout(JSON.stringify({ version: 1, widgets }))
  if (parsed.kind !== 'ok') throw new Error(`disposition ${parsed.kind}`)
  return parsed.layout.widgets
}

describe('parseLayout', () => {
  it('ne lève jamais : vide, JSON invalide ou valeur non objet donnent « empty »', () => {
    for (const raw of [null, undefined, '', '   ', '{', '12', 'null']) expect(parseLayout(raw)).toEqual({ kind: 'empty' })
  })

  it('une version plus récente est rendue telle quelle, en lecture seule', () => {
    const parsed = parseLayout(JSON.stringify({ version: LAYOUT_VERSION + 1, widgets: [{ id: 'a', type: 'hologramme', width: 6, cible: 3 }] }))
    expect(parsed.kind).toBe('newer')
    if (parsed.kind === 'newer') expect(parsed.layout.widgets[0]).toMatchObject({ id: 'a', type: 'hologramme', cible: 3 })
  })

  it('borne le nombre d’articles et corrige une largeur inconnue', () => {
    const [big, small, none] = parseWidgets([
      { id: 'a', type: 'news', width: 5, limit: 100 },
      { id: 'b', type: 'starred', width: 2, limit: 1 },
      { id: 'c', type: 'summaries', width: 6, limit: 'beaucoup' },
    ])
    expect(big).toMatchObject({ limit: 30, width: 3 })
    expect(small).toMatchObject({ limit: 3, width: 2 })
    expect(none).toMatchObject({ limit: 8, width: 6 })
  })

  it('ramène une source invalide à « tous » et garde une source valide', () => {
    const [bad, good, smart] = parseWidgets([
      { id: 'a', type: 'news', source: { kind: 'category', id: 'x' } },
      { id: 'b', type: 'news', source: { kind: 'feed', id: 7 } },
      { id: 'c', type: 'news', source: { kind: 'smart', id: 2 }, unreadOnly: false, display: 'carousel' },
    ]) as NewsWidget[]
    expect(bad.source).toEqual({ kind: 'all' })
    expect(bad).toMatchObject({ unreadOnly: true, display: 'list' })
    expect(good.source).toEqual({ kind: 'feed', id: 7 })
    expect(smart).toMatchObject({ source: { kind: 'smart', id: 2 }, unreadOnly: false, display: 'carousel' })
  })

  it('écarte ce qui n’est pas un widget, donne un identifiant à celui qui n’en a pas, nettoie le titre', () => {
    const widgets = parseWidgets([null, 'texte', { width: 3 }, { type: 'starred', title: '  Mes favoris  ' }, { id: 'x', type: 'stats', title: '   ' }])
    expect(widgets).toHaveLength(2)
    expect(widgets[0].id).toBeTruthy()
    expect(widgets[0].title).toBe('Mes favoris')
    expect(widgets[1]).not.toHaveProperty('title')
  })

  it('conserve un widget de type inconnu avec ses champs', () => {
    const [w] = parseWidgets([{ id: 'm', type: 'météo', width: 2, ville: 'Paris' }])
    expect(w).toMatchObject({ id: 'm', type: 'météo', width: 2, ville: 'Paris' })
    expect(widgetTitle(w, new Map(), new Map())).toEqual({ title: 'Widget non pris en charge', orphan: false })
  })

  it('relit ce que serializeLayout a écrit', () => {
    const layout = buildDefaultLayout([category(1)], [feed(1, { category_id: 1, is_youtube: true })])
    const parsed = parseLayout(serializeLayout({ ...layout, updatedAt: '2026-11-12T10:00:00.000Z' }))
    expect(parsed).toEqual({ kind: 'ok', layout: { ...layout, updatedAt: '2026-11-12T10:00:00.000Z' } })
  })
})

describe('buildDefaultLayout', () => {
  const ids = (categories = [] as ReturnType<typeof category>[], feeds = [] as ReturnType<typeof feed>[]) => buildDefaultLayout(categories, feeds).widgets.map((w) => w.id)

  it('instance vide : vue d’ensemble, fil, favoris, résumés', () => {
    expect(ids()).toEqual(['default-stats', 'default-feed', 'default-starred', 'default-summaries'])
  })

  it('un widget par dossier, dans l’ordre de la barre latérale', () => {
    expect(ids([category(1, { position: 5 }), category(2, { position: 1 })])).toEqual(['default-stats', 'default-feed', 'default-cat-2', 'default-cat-1', 'default-starred', 'default-summaries'])
  })

  it('« sans catégorie » seulement s’il y a des dossiers et un flux hors dossier ; vidéos seulement avec un flux YouTube', () => {
    expect(ids([], [feed(1)])).not.toContain('default-uncategorized')
    expect(ids([category(1)], [feed(1)])).toContain('default-uncategorized')
    expect(ids([category(1)], [feed(1, { category_id: 1 })])).not.toContain('default-uncategorized')
    expect(ids([], [feed(1)])).not.toContain('default-youtube')
    expect(ids([], [feed(1, { is_youtube: true })])).toContain('default-youtube')
  })
})

describe('widgetTitle', () => {
  const news = (source: NewsWidget['source'], title?: string): NewsWidget => ({ id: 'w', type: 'news', width: 3, source, limit: 5, display: 'list', unreadOnly: true, ...(title ? { title } : {}) })

  it('dérive le titre de la cible', () => {
    expect(widgetTitle(news({ kind: 'category', id: 1 }), new Map([[1, category(1, { title: 'Presse' })]]), new Map())).toEqual({ title: 'Presse', orphan: false })
    expect(widgetTitle(news({ kind: 'smart', id: 2 }), new Map(), new Map(), new Map([[2, smartFeed(2, { name: 'IA' })]]))).toEqual({ title: 'IA', orphan: false })
  })

  it('signale une cible disparue, même avec un titre personnalisé', () => {
    expect(widgetTitle(news({ kind: 'feed', id: 9 }), new Map(), new Map())).toEqual({ title: 'Flux supprimé', orphan: true })
    expect(widgetTitle(news({ kind: 'feed', id: 9 }, 'Mon flux'), new Map(), new Map())).toEqual({ title: 'Mon flux', orphan: true })
  })
})

describe('viewForSource', () => {
  it('donne la vue de liste de la source', () => {
    expect(viewForSource({ kind: 'all' })).toEqual({ kind: 'all' })
    expect(viewForSource({ kind: 'category', id: 3 })).toEqual({ kind: 'category', id: 3 })
    expect(viewForSource({ kind: 'feed', id: 4 })).toEqual({ kind: 'feed', id: 4 })
    expect(viewForSource({ kind: 'smart', id: 5 })).toEqual({ kind: 'smart', id: 5 })
  })
})
