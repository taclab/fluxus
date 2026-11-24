/**
 * Types de résumé proposés au lecteur et choix du résumé affiché. Les consignes vivent côté serveur
 * (`summarize/prompts.STYLES`) ; ici, l'ordre du menu et les règles d'affichage, sans React.
 */

import type { SummaryOut } from '../../api/client'

/** Types du menu, dans l'ordre d'affichage (« question » se demande par le champ libre). */
export const SUMMARY_STYLES = ['standard', 'oneline', 'bullets', 'detailed', 'opinion', 'simple'] as const
export type SummaryStyle = (typeof SUMMARY_STYLES)[number]

export function isSummaryStyle(value: string | null | undefined): value is SummaryStyle {
  return (SUMMARY_STYLES as readonly string[]).includes(value ?? '')
}

/** Une demande : un type, ou une question libre. */
export interface SummaryRequest {
  style?: SummaryStyle
  question?: string
}

/** Identité d'un résumé côté lecteur : même type et même question = même résumé (le plus récent l'emporte). */
export function summaryKey(s: Pick<SummaryOut, 'style' | 'question'>): string {
  return s.style === 'question' ? `question:${(s.question ?? '').trim()}` : s.style
}

export function requestKey(r: SummaryRequest): string | null {
  const question = r.question?.trim()
  if (question) return `question:${question}`
  return r.style ?? null // sans type : celui des réglages, connu seulement à la réponse
}

/** Résumés distincts d'un article, le plus récent d'abord (un seul par type ou question). */
export function distinctSummaries(summaries: readonly SummaryOut[]): SummaryOut[] {
  const latest = new Map<string, SummaryOut>()
  for (const s of summaries) {
    const key = summaryKey(s)
    const current = latest.get(key)
    if (!current || s.created_at > current.created_at || (s.created_at === current.created_at && s.id > current.id)) latest.set(key, s)
  }
  return [...latest.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : b.id - a.id))
}

/** Résumé à afficher : celui choisi s'il existe, sinon le plus récent. */
export function pickSummary(summaries: readonly SummaryOut[], selectedKey: string | null): SummaryOut | null {
  const list = distinctSummaries(summaries)
  return (selectedKey && list.find((s) => summaryKey(s) === selectedKey)) || list[0] || null
}
