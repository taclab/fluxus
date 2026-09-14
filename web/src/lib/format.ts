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
