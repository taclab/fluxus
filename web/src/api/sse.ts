/** Lecture d'un flux SSE obtenu par POST (EventSource ne sait pas faire de POST). */

import { getT } from '../i18n'
import { apiUrl, authHeaders } from './client'

export interface SseEvent {
  event: string
  data: string
}

export async function* postSse(path: string, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  const response = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { ...authHeaders(), Accept: 'text/event-stream' },
    signal,
  })
  if (!response.ok || !response.body) {
    let message = getT().common.httpError(response.status)
    try {
      const body = await response.json()
      if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* corps non JSON */
    }
    throw new Error(message)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    // Le serveur (sse-starlette) sépare les lignes par \r\n : on normalise avant de découper.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n')
    let index: number
    while ((index = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      const evt = parseBlock(raw)
      if (evt) yield evt
    }
  }
  if (buffer.trim()) {
    const evt = parseBlock(buffer)
    if (evt) yield evt
  }
}

function parseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith(':')) continue // commentaire / ping
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (data.length === 0) return null
  return { event, data: data.join('\n') }
}
