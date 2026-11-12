import { beforeEach, describe, expect, it, vi } from 'vitest'

import { db, getMeta, setMeta } from '../db/db'
import { EPOCH_HEADER, EPOCH_KEY, STALE_EPOCH, STALE_EPOCH_EVENT } from '../sync/epoch'
import { api, reconfigureClient } from './client'

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const markRead = () => api().POST('/api/entries/mark-read', { body: { all: false, starred_only: false, read: true, entry_ids: [1] } })

let sent: Request[]
let answer: () => Response

beforeEach(async () => {
  await db.meta.clear()
  sent = []
  answer = () => json(200, { updated: 1 })
  vi.stubGlobal('fetch', async (request: Request) => {
    sent.push(request)
    return answer()
  })
  reconfigureClient() // le client garde le fetch présent à sa création
})

describe('annonce de l’époque', () => {
  it('toute écriture porte l’époque du cache, une lecture non', async () => {
    await setMeta(EPOCH_KEY, 'abc-123')
    await markRead()
    await api().GET('/api/feeds')
    expect(sent[0].headers.get(EPOCH_HEADER)).toBe('abc-123')
    expect(sent[1].headers.get(EPOCH_HEADER)).toBeNull()
  })

  it('un appareil sans époque connue n’annonce rien', async () => {
    await markRead()
    expect(sent[0].headers.get(EPOCH_HEADER)).toBeNull()
  })

  it('une époque périmée est encodée (en-tête ASCII) et reste refusable', async () => {
    await setMeta(EPOCH_KEY, STALE_EPOCH)
    await markRead()
    expect(decodeURIComponent(sent[0].headers.get(EPOCH_HEADER)!)).toBe(STALE_EPOCH)
  })

  it('un refus pour époque périmée marque le cache et demande une resynchronisation', async () => {
    await setMeta(EPOCH_KEY, 'abc-123')
    const asked = vi.fn()
    window.addEventListener(STALE_EPOCH_EVENT, asked)
    answer = () => json(409, { detail: { code: 'stale_epoch', message: 'La base du serveur a été remplacée' } })
    const { response } = await markRead()
    window.removeEventListener(STALE_EPOCH_EVENT, asked)
    expect(response.status).toBe(409)
    expect(await getMeta(EPOCH_KEY)).toBe(STALE_EPOCH)
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('un autre 409 (doublon, conflit de disposition) ne touche pas à l’époque', async () => {
    await setMeta(EPOCH_KEY, 'abc-123')
    answer = () => json(409, { detail: 'Ce flux est déjà abonné' })
    await markRead()
    expect(await getMeta(EPOCH_KEY)).toBe('abc-123')
  })
})
