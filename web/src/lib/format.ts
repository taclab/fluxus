import { format, isToday, isYesterday } from 'date-fns'
import { fr } from 'date-fns/locale'

export function relativeDate(iso: string): string {
  const d = new Date(iso)
  const diffMs = Date.now() - d.getTime()
  if (diffMs < 60_000) return "à l'instant"
  if (diffMs < 3600_000) return `${Math.floor(diffMs / 60_000)} min`
  if (diffMs < 24 * 3600_000) return `${Math.floor(diffMs / 3600_000)} h`
  if (isYesterday(d)) return 'hier'
  if (d.getFullYear() === new Date().getFullYear()) return format(d, 'd MMM', { locale: fr })
  return format(d, 'd MMM yyyy', { locale: fr })
}

/** Date passée précédée de « il y a » quand c'est une durée : « il y a 5 min », « à l'instant », « hier ». */
export function agoDate(iso: string): string {
  const r = relativeDate(iso)
  return /^\d/.test(r) ? `il y a ${r}` : r
}

/** Date à venir : « dans 25 min », « dans 3 h », « imminente ». */
export function untilDate(iso: string): string {
  const diff = Date.parse(iso) - Date.now()
  if (diff <= 60_000) return 'imminente'
  if (diff < 3600_000) return `dans ${Math.round(diff / 60_000)} min`
  if (diff < 48 * 3600_000) return `dans ${Math.round(diff / 3600_000)} h`
  return `dans ${Math.round(diff / (24 * 3600_000))} j`
}

export function fullDate(iso: string): string {
  const d = new Date(iso)
  if (isToday(d)) return `aujourd'hui à ${format(d, 'HH:mm')}`
  return format(d, "EEEE d MMMM yyyy 'à' HH:mm", { locale: fr })
}

export function hostname(url: string | null | undefined): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')} k`
  return `${(n / 1_000_000).toFixed(2)} M`
}

export function plural(n: number, singular: string, pluralForm?: string): string {
  return n <= 1 ? singular : pluralForm ?? `${singular}s`
}
