// node --test cloudflare/email-worker : le Worker signe comme le serveur (même vecteur que
// backend/tests/test_newsletter_parse.py) et choisit la bonne issue selon la réponse de Fluxus.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import worker, { sign } from './worker.js'

const VECTOR = '22c7dc70d412d831a39876e6f568def38f2beb553c83e4648bcdc150dc0aa6fa'
const env = { FLUXUS_URL: 'https://fluxus.example.org', FLUXUS_SECRET: 's3cr3t', FALLBACK_ADDRESS: 'moi@example.org' }

test('signature identique à celle du serveur', async () => {
  const body = new TextEncoder().encode('Subject: test\r\n\r\nhello')
  assert.equal(await sign('s3cr3t', '1764000000000', 'K7F3Q9X2ABCD@news.example.org', body), VECTOR)
})

function fakeMessage(raw = 'Subject: x\r\n\r\ny') {
  const calls = { forward: [], reject: [] }
  return {
    calls,
    to: 'k7f3q9x2abcd@news.example.org',
    rawSize: raw.length,
    raw: new Response(raw).body,
    forward: async (to) => calls.forward.push(to),
    setReject: (reason) => calls.reject.push(reason),
  }
}

async function deliver(status, { throws = false, message = fakeMessage() } = {}) {
  const seen = []
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), init })
    if (throws) throw new Error('réseau')
    return new Response('{}', { status })
  }
  await worker.email(message, env)
  return { calls: message.calls, seen }
}

test('202 : rien de plus', async () => {
  const { calls, seen } = await deliver(202)
  assert.equal(seen[0].url, 'https://fluxus.example.org/api/inbound/email')
  assert.equal(seen[0].init.headers['X-Fluxus-To'], 'k7f3q9x2abcd@news.example.org')
  assert.match(seen[0].init.headers['X-Fluxus-Signature'], /^[0-9a-f]{64}$/)
  assert.deepEqual(calls, { forward: [], reject: [] })
})

test('404 : adresse inconnue rejetée', async () => {
  const { calls } = await deliver(404)
  assert.deepEqual(calls, { forward: [], reject: ['Adresse inconnue'] })
})

test('erreur serveur ou réseau : renvoi vers le secours', async () => {
  assert.deepEqual((await deliver(503)).calls.forward, ['moi@example.org'])
  assert.deepEqual((await deliver(401)).calls.forward, ['moi@example.org'])
  assert.deepEqual((await deliver(0, { throws: true })).calls.forward, ['moi@example.org'])
})

test('message trop gros : secours sans appel à Fluxus', async () => {
  const message = fakeMessage()
  message.rawSize = 16 * 1024 * 1024
  const { calls, seen } = await deliver(202, { message })
  assert.equal(seen.length, 0)
  assert.deepEqual(calls.forward, ['moi@example.org'])
})
