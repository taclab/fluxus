import { describe, expect, it } from 'vitest'

import type { SummaryOut } from '../../api/client'
import { distinctSummaries, isSummaryStyle, pickSummary, requestKey, summaryKey } from './summaryStyles'

let nextId = 1
function summary(style: string, created_at: string, question: string | null = null): SummaryOut {
  return { id: nextId++, entry_id: 1, kind: 'article', style, question, provider: 'fake', model: 'm', text_md: style, input_tokens: 1, output_tokens: 1, created_at }
}

describe('types de résumé', () => {
  it('reconnaît les types du menu, pas la question ni un type inconnu', () => {
    expect(isSummaryStyle('detailed')).toBe(true)
    expect(isSummaryStyle('question')).toBe(false)
    expect(isSummaryStyle(undefined)).toBe(false)
  })

  it('identifie un résumé par son type, ou par sa question', () => {
    expect(summaryKey(summary('bullets', '2026-11-24T10:00:00Z'))).toBe('bullets')
    expect(summaryKey(summary('question', '2026-11-24T10:00:00Z', ' Qui ? '))).toBe('question:Qui ?')
    expect(requestKey({ question: '  Qui ? ' })).toBe('question:Qui ?')
    expect(requestKey({ style: 'oneline' })).toBe('oneline')
    expect(requestKey({})).toBeNull()
  })

  it('garde le plus récent par type ou question, le plus récent en tête', () => {
    const old = summary('standard', '2026-11-24T08:00:00Z')
    const fresh = summary('standard', '2026-11-24T09:00:00Z')
    const q1 = summary('question', '2026-11-24T10:00:00Z', 'A ?')
    const q2 = summary('question', '2026-11-24T07:00:00Z', 'B ?')
    expect(distinctSummaries([old, q2, fresh, q1])).toEqual([q1, fresh, q2])
  })

  it('affiche le résumé choisi, sinon le plus récent', () => {
    const std = summary('standard', '2026-11-24T08:00:00Z')
    const one = summary('oneline', '2026-11-24T09:00:00Z')
    expect(pickSummary([std, one], 'standard')).toBe(std)
    expect(pickSummary([std, one], null)).toBe(one)
    expect(pickSummary([std, one], 'detailed')).toBe(one)
    expect(pickSummary([], null)).toBeNull()
  })
})
