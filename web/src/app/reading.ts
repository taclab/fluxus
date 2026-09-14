/** Préférences typographiques de lecture (par appareil, comme le thème). Appliquées via des attributs sur <html>. */

export type ReadingFont = 'editorial' | 'literary' | 'journal' | 'modern' | 'legible' | 'system'
export type ReadingSize = 's' | 'm' | 'l' | 'xl'
export type ReadingWidth = 'narrow' | 'normal' | 'wide'

export interface ReadingPrefs {
  font: ReadingFont
  size: ReadingSize
  width: ReadingWidth
}

export const READING_FONTS: { id: ReadingFont; label: string; body: string; heading: string; description: string }[] = [
  { id: 'editorial', label: 'Éditorial', body: 'Source Serif 4', heading: 'Inter', description: 'Serif contemporain pour le texte, titres sans serif. Le réglage par défaut.' },
  { id: 'literary', label: 'Littéraire', body: 'Literata', heading: 'Literata', description: 'Serif de lecture longue, tout en serif, comme un livre numérique.' },
  { id: 'journal', label: 'Journal', body: 'Lora', heading: 'Montserrat', description: 'Serif chaleureux et titres géométriques, esprit magazine.' },
  { id: 'modern', label: 'Moderne', body: 'Inter', heading: 'Inter', description: 'Tout en sans serif, net et compact.' },
  { id: 'legible', label: 'Lisible', body: 'Atkinson Hyperlegible', heading: 'Atkinson Hyperlegible', description: 'Conçue pour distinguer chaque lettre, idéale en basse vision ou sur petit écran.' },
  { id: 'system', label: 'Système', body: 'system-ui', heading: 'system-ui', description: 'Les polices natives de l’appareil, sans téléchargement.' },
]

export const READING_SIZES: { id: ReadingSize; label: string; px: number }[] = [
  { id: 's', label: 'Petite', px: 16 },
  { id: 'm', label: 'Normale', px: 18 },
  { id: 'l', label: 'Grande', px: 20 },
  { id: 'xl', label: 'Très grande', px: 23 },
]

export const READING_WIDTHS: { id: ReadingWidth; label: string; rem: number }[] = [
  { id: 'narrow', label: 'Étroite', rem: 36 },
  { id: 'normal', label: 'Normale', rem: 44 },
  { id: 'wide', label: 'Large', rem: 56 },
]

const KEY = 'fluxus.reading'
const DEFAULTS: ReadingPrefs = { font: 'editorial', size: 'm', width: 'normal' }

export function getReadingPrefs(): ReadingPrefs {
  try {
    const raw = localStorage.getItem(KEY)
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

export function applyReadingPrefs(prefs: ReadingPrefs): void {
  localStorage.setItem(KEY, JSON.stringify(prefs))
  const root = document.documentElement
  root.dataset.readingFont = prefs.font
  root.dataset.readingSize = prefs.size
  root.dataset.readingWidth = prefs.width
  window.dispatchEvent(new Event('fluxus:reading'))
}
