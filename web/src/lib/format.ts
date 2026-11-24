import { getT, localeTag } from '../i18n'

// Intl plutôt qu'une bibliothèque de dates, dans la langue de l'interface (« 3 oct. » / « 3 Oct »).
// Formateurs créés à la demande et gardés par langue.
const formatters = new Map<string, Intl.DateTimeFormat>()
function fmt(name: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const tag = localeTag()
  const key = `${tag}:${name}`
  let f = formatters.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(tag, options)
    formatters.set(key, f)
  }
  return f
}
const DAY_MONTH = () => fmt('dm', { day: 'numeric', month: 'short' })
const DAY_MONTH_YEAR = () => fmt('dmy', { day: 'numeric', month: 'short', year: 'numeric' })
const LONG_DATE = () => fmt('long', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
const LONG_DAY = () => fmt('longday', { weekday: 'long', day: 'numeric', month: 'long' })
const TIME = () => fmt('time', { hour: '2-digit', minute: '2-digit' })

/** Heure locale (« 14:05 »). */
export function timeOf(d: Date): string {
  return TIME().format(d)
}

/** Date courte locale (« 24/11/2026 »). */
export function shortDate(d: Date = new Date()): string {
  return d.toLocaleDateString(localeTag())
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

function isYesterday(d: Date): boolean {
  const y = new Date()
  y.setDate(y.getDate() - 1)
  return sameDay(d, y)
}

/** « jeudi 29 octobre » (en-tête du tableau de bord). */
export function longDay(d: Date = new Date()): string {
  return LONG_DAY().format(d)
}

export function relativeDate(iso: string): string {
  const t = getT().common.date
  const d = new Date(iso)
  const diffMs = Date.now() - d.getTime()
  if (diffMs < 60_000) return t.justNow
  if (diffMs < 3600_000) return t.minutes(Math.floor(diffMs / 60_000))
  if (diffMs < 24 * 3600_000) return t.hours(Math.floor(diffMs / 3600_000))
  if (isYesterday(d)) return t.yesterday
  if (d.getFullYear() === new Date().getFullYear()) return DAY_MONTH().format(d)
  return DAY_MONTH_YEAR().format(d)
}

/** Date passée précédée de « il y a » quand c'est une durée : « il y a 5 min », « à l'instant », « hier ». */
export function agoDate(iso: string): string {
  const r = relativeDate(iso)
  return /^\d/.test(r) ? getT().common.date.ago(r) : r
}

/** Date à venir : « dans 25 min », « dans 3 h », « imminente ». */
export function untilDate(iso: string): string {
  const t = getT().common.date
  const diff = Date.parse(iso) - Date.now()
  if (diff <= 60_000) return t.imminent
  if (diff < 3600_000) return t.inMinutes(Math.round(diff / 60_000))
  if (diff < 48 * 3600_000) return t.inHours(Math.round(diff / 3600_000))
  return t.inDays(Math.round(diff / (24 * 3600_000)))
}

export function fullDate(iso: string): string {
  const t = getT().common.date
  const d = new Date(iso)
  if (sameDay(d, new Date())) return t.todayAt(TIME().format(d))
  return t.dateAt(LONG_DATE().format(d), TIME().format(d))
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
  const t = getT().common.size
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '').replace('.', t.decimal)} ${t.thousands}`
  return `${(n / 1_000_000).toFixed(2).replace('.', t.decimal)} ${t.millions}`
}

/** Taille en Go ou en Mo (unités binaires, comme `df -h`). */
export function formatBytes(n: number): string {
  const t = getT().common.size
  const gib = n / 1024 ** 3
  if (gib >= 1) return `${gib.toFixed(gib >= 10 ? 0 : 1).replace('.', t.decimal)} ${t.gigabytes}`
  return `${Math.round(n / 1024 ** 2)} ${t.megabytes}`
}
