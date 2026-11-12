import { beforeEach, describe, expect, it } from 'vitest'

import type { SummaryOut } from '../api/client'
import { db, toLocalEntry } from '../db/db'
import { entry } from '../test/fixtures'
import { applyRetention, mergeEntries } from './merge'

const NOW = Date.parse('2026-11-12T12:00:00Z')
const DAY = 24 * 3600 * 1000
const iso = (daysAgo: number) => new Date(NOW - daysAgo * DAY).toISOString()
const summary = (id: number, entryId: number) => ({ id, entry_id: entryId, text_md: 'résumé' }) as SummaryOut
const localIds = async () => (await db.entries.toCollection().primaryKeys()).sort((a, b) => a - b)

beforeEach(async () => {
  await Promise.all([db.entries.clear(), db.summaries.clear(), db.outbox.clear()])
})

describe('mergeEntries', () => {
  it('écrit la page avec les index dérivés', async () => {
    await mergeEntries({ entries: [entry(1, { read: true, starred: true, published_at: iso(2), updated_at: iso(1) })], summaries: [summary(7, 1)] })
    expect(await db.entries.get(1)).toMatchObject({ readFlag: 1, starredFlag: 1, publishedTs: NOW - 2 * DAY, updatedTs: NOW - DAY })
    expect(await db.summaries.get(7)).toMatchObject({ entry_id: 1 })
  })

  it('la version du serveur remplace la version locale', async () => {
    await db.entries.put(toLocalEntry(entry(1, { read: true, title: 'Ancien titre' })))
    await mergeEntries({ entries: [entry(1, { read: false, title: 'Nouveau titre' })], summaries: [] })
    expect(await db.entries.get(1)).toMatchObject({ read: false, readFlag: 0, title: 'Nouveau titre' })
  })

  it('un changement encore dans la file hors ligne garde son état lu et favori, le reste suit le serveur', async () => {
    await db.entries.put(toLocalEntry(entry(1, { read: true, starred: true, title: 'Ancien titre' })))
    await db.outbox.add({ entry_id: 1, read: true, at: iso(0) })
    await mergeEntries({ entries: [entry(1, { read: false, starred: false, title: 'Nouveau titre' }), entry(2, { read: false })], summaries: [] })
    expect(await db.entries.get(1)).toMatchObject({ read: true, readFlag: 1, starred: true, starredFlag: 1, title: 'Nouveau titre' })
    expect(await db.entries.get(2)).toMatchObject({ read: false, readFlag: 0 })
  })
})

describe('applyRetention', () => {
  it('purge les articles lus plus vieux que la rétention, avec leur résumé', async () => {
    await mergeEntries({
      entries: [
        entry(1, { read: true, published_at: iso(40), updated_at: iso(35) }),
        entry(2, { read: true, published_at: iso(10), updated_at: iso(9) }),
      ],
      summaries: [summary(1, 1), summary(2, 2)],
    })
    await applyRetention(30, NOW)
    expect(await localIds()).toEqual([2])
    expect((await db.summaries.toArray()).map((s) => s.entry_id)).toEqual([2])
  })

  it('garde les non lus et les favoris, quel que soit leur âge', async () => {
    await mergeEntries({
      entries: [
        entry(1, { read: false, published_at: iso(400), updated_at: iso(400) }),
        entry(2, { read: true, starred: true, published_at: iso(400), updated_at: iso(400) }),
      ],
      summaries: [],
    })
    await applyRetention(30, NOW)
    expect(await localIds()).toEqual([1, 2])
  })

  it('garde un vieil article dont l’état a changé depuis moins d’un jour (il est peut-être ouvert)', async () => {
    await mergeEntries({
      entries: [
        entry(1, { read: true, published_at: iso(400), updated_at: iso(0.5) }),
        entry(2, { read: true, published_at: iso(400), updated_at: iso(2) }),
      ],
      summaries: [],
    })
    await applyRetention(30, NOW)
    expect(await localIds()).toEqual([1])
  })
})
