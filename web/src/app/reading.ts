/** Préférences typographiques de lecture (par appareil, comme le thème). Appliquées via des attributs sur <html>. */

import { createDevicePref } from './devicePref'

export type ReadingFont = 'editorial' | 'literary' | 'journal' | 'modern' | 'legible' | 'system'
export type ReadingSize = 's' | 'm' | 'l' | 'xl'
export type ReadingWidth = 'narrow' | 'normal' | 'wide'

export interface ReadingPrefs {
  font: ReadingFont
  size: ReadingSize
  width: ReadingWidth
}

/** `body`/`heading` : noms affichés ; `bodyCss`/`headingCss` : familles CSS réelles (fontsource ajoute « Variable »). */
/** Libellés et descriptions : `t.app.typography` (catalogues de traduction). */
export const READING_FONTS: { id: ReadingFont; body: string; heading: string; bodyCss: string; headingCss: string }[] = [
  { id: 'editorial', body: 'Source Serif 4', heading: 'Inter', bodyCss: "'Source Serif 4 Variable', Georgia, serif", headingCss: "'Inter Variable', system-ui, sans-serif" },
  { id: 'literary', body: 'Literata', heading: 'Literata', bodyCss: "'Literata Variable', Georgia, serif", headingCss: "'Literata Variable', Georgia, serif" },
  { id: 'journal', body: 'Lora', heading: 'Montserrat', bodyCss: "'Lora Variable', Georgia, serif", headingCss: "'Montserrat Variable', system-ui, sans-serif" },
  { id: 'modern', body: 'Inter', heading: 'Inter', bodyCss: "'Inter Variable', system-ui, sans-serif", headingCss: "'Inter Variable', system-ui, sans-serif" },
  { id: 'legible', body: 'Atkinson Hyperlegible', heading: 'Atkinson Hyperlegible', bodyCss: "'Atkinson Hyperlegible', system-ui, sans-serif", headingCss: "'Atkinson Hyperlegible', system-ui, sans-serif" },
  { id: 'system', body: 'system-ui', heading: 'system-ui', bodyCss: 'system-ui, -apple-system, sans-serif', headingCss: 'system-ui, -apple-system, sans-serif' },
]

export const READING_SIZES: { id: ReadingSize; px: number }[] = [
  { id: 's', px: 16 },
  { id: 'm', px: 18 },
  { id: 'l', px: 20 },
  { id: 'xl', px: 23 },
]

export const READING_WIDTHS: { id: ReadingWidth; rem: number }[] = [
  { id: 'narrow', rem: 36 },
  { id: 'normal', rem: 44 },
  { id: 'wide', rem: 56 },
]

const KEY = 'fluxus.reading'
const DEFAULTS: ReadingPrefs = { font: 'editorial', size: 'm', width: 'normal' }

function parseReadingPrefs(raw: string | null): ReadingPrefs {
  try {
    if (!raw) return DEFAULTS
    const p = JSON.parse(raw) as Partial<ReadingPrefs>
    return {
      font: READING_FONTS.some((f) => f.id === p.font) ? (p.font as ReadingFont) : DEFAULTS.font,
      size: READING_SIZES.some((s) => s.id === p.size) ? (p.size as ReadingSize) : DEFAULTS.size,
      width: READING_WIDTHS.some((w) => w.id === p.width) ? (p.width as ReadingWidth) : DEFAULTS.width,
    }
  } catch {
    return DEFAULTS
  }
}

/** Même clé que `public/theme-init.js`, qui applique la typographie avant le premier rendu. */
export const readingPref = createDevicePref<ReadingPrefs>(KEY, parseReadingPrefs, JSON.stringify)

export function getReadingPrefs(): ReadingPrefs {
  return readingPref.get()
}

export function applyReadingPrefs(prefs: ReadingPrefs): void {
  readingPref.set(prefs)
  const root = document.documentElement
  root.dataset.readingFont = prefs.font
  root.dataset.readingSize = prefs.size
  root.dataset.readingWidth = prefs.width
}
