import { beforeEach, describe, expect, it, vi } from 'vitest'

import { reconfigureClient } from '../../api/client'
import { db, getMeta, setMeta } from '../../db/db'
import { flushDashboardLayout } from './useDashboardLayout'

const LAYOUT = 'dashboard.layout'
const DIRTY = 'dashboard.layout.dirty'
const VERSION = 'dashboard.layout.version'
const layout = (id: string) => JSON.stringify({ version: 1, updatedAt: '2026-11-12T10:00:00.000Z', widgets: [{ id, type: 'starred', width: 3, limit: 8 }] })
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const conflict = (serverJson: string, version: number) => json(409, { detail: { dashboard_layout: serverJson, dashboard_layout_version: version } })

let answers: (() => Response)[]
let sentBases: number[]

beforeEach(async () => {
  await db.meta.clear()
  answers = []
  sentBases = []
  vi.stubGlobal('navigator', { onLine: true })
  vi.stubGlobal('fetch', async (request: Request) => {
    sentBases.push(((await request.json()) as { dashboard_layout_base_version: number }).dashboard_layout_base_version)
    return answers.shift()!()
  })
  reconfigureClient()
})

async function pending(json: string, base: number) {
  await setMeta(LAYOUT, json)
  await setMeta(DIRTY, '1')
  await setMeta(VERSION, String(base))
}

describe('envoi de la disposition', () => {
  it('envoi accepté : le drapeau tombe, la version avance', async () => {
    await pending(layout('a'), 3)
    answers = [() => json(200, { values: {}, secrets_set: {}, dashboard_layout_version: 4 })]
    await flushDashboardLayout()
    expect(sentBases).toEqual([3])
    expect(await getMeta(DIRTY)).toBeUndefined()
    expect(await getMeta(VERSION)).toBe('4')
  })

  it('refus parce que le serveur a déjà cette disposition : ce n’est pas un conflit', async () => {
    // Cas de l'envoi à la fermeture de l'onglet : l'écriture est partie, sa réponse n'a jamais été lue.
    await pending(layout('a'), 3)
    answers = [() => conflict(layout('a'), 4)]
    await flushDashboardLayout()
    expect(await getMeta(LAYOUT)).toBe(layout('a'))
    expect(await getMeta(DIRTY)).toBeUndefined()
    expect(await getMeta(VERSION)).toBe('4')
  })

  it('refus à cause de notre écriture précédente : renvoi sur la nouvelle version', async () => {
    await pending(layout('a'), 3)
    answers = [() => json(200, { values: {}, secrets_set: {}, dashboard_layout_version: 4 })]
    await flushDashboardLayout() // « a » est maintenant connue comme envoyée par cet appareil
    await pending(layout('b'), 3) // version locale restée en arrière (réponse perdue)
    answers = [() => conflict(layout('a'), 4), () => json(200, { values: {}, secrets_set: {}, dashboard_layout_version: 5 })]
    await flushDashboardLayout()
    expect(sentBases).toEqual([3, 3, 4])
    expect(await getMeta(LAYOUT)).toBe(layout('b'))
    expect(await getMeta(DIRTY)).toBeUndefined()
    expect(await getMeta(VERSION)).toBe('5')
  })

  it('vrai conflit : la disposition d’un autre appareil remplace la locale', async () => {
    await pending(layout('local'), 3)
    answers = [() => conflict(layout('autre-appareil'), 7)]
    await flushDashboardLayout()
    expect(await getMeta(LAYOUT)).toBe(layout('autre-appareil'))
    expect(await getMeta(DIRTY)).toBeUndefined()
    expect(await getMeta(VERSION)).toBe('7')
  })
})
