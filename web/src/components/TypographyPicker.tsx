import clsx from 'clsx'
import { useEffect, useState } from 'react'

import { applyReadingPrefs, getReadingPrefs, READING_FONTS, READING_SIZES, READING_WIDTHS, type ReadingPrefs } from '../app/reading'

export function useReadingPrefs(): [ReadingPrefs, (p: Partial<ReadingPrefs>) => void] {
  const [prefs, setPrefs] = useState<ReadingPrefs>(getReadingPrefs())
  useEffect(() => {
    const handler = () => setPrefs(getReadingPrefs())
    window.addEventListener('fluxus:reading', handler)
    return () => window.removeEventListener('fluxus:reading', handler)
  }, [])
  const update = (p: Partial<ReadingPrefs>) => applyReadingPrefs({ ...getReadingPrefs(), ...p })
  return [prefs, update]
}

/** Choix de la combinaison de polices, de la taille et de la largeur de colonne. `compact` pour le popover du lecteur. */
export function TypographyPicker({ compact }: { compact?: boolean }) {
  const [prefs, update] = useReadingPrefs()
  return (
    <div className={clsx('space-y-4', compact && 'space-y-3')}>
      <div className={clsx('grid gap-2', compact ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3')} role="radiogroup" aria-label="Combinaison de polices">
        {READING_FONTS.map((f) => {
          const active = prefs.font === f.id
          return (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={active}
              data-reading-font-option={f.id}
              onClick={() => update({ font: f.id })}
              className={clsx(
                'flex flex-col rounded-xl border p-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400',
                active ? 'border-accent-500 bg-accent-50 dark:bg-accent-900/20 ring-1 ring-accent-500' : 'border-app hover:bg-muted',
              )}
            >
              <span className="flex items-baseline gap-2">
                <span className={clsx('text-2xl leading-none text-app', compact && 'text-xl')} style={{ fontFamily: `'${f.heading}', ${f.id === 'system' ? 'system-ui, sans-serif' : 'serif'}` }}>
                  A
                </span>
                <span className={clsx('text-2xl leading-none text-app', compact && 'text-xl')} style={{ fontFamily: `'${f.body}', ${f.id === 'system' ? 'system-ui, sans-serif' : 'serif'}` }}>
                  a
                </span>
                <span className="ml-auto text-sm font-medium text-app">{f.label}</span>
              </span>
              {!compact && <span className="mt-1.5 text-xs text-muted leading-snug">{f.description}</span>}
              {!compact && (
                <span className="mt-1 text-[11px] text-muted">
                  {f.body}
                  {f.heading !== f.body && ` · titres ${f.heading}`}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <div className={clsx('grid gap-3', compact ? 'grid-cols-1' : 'sm:grid-cols-2')}>
        <Segmented label="Taille du texte" value={prefs.size} options={READING_SIZES.map((s) => ({ id: s.id, label: compact ? `${s.px}` : s.label }))} onChange={(size) => update({ size: size as ReadingPrefs['size'] })} />
        <Segmented label="Largeur de colonne" value={prefs.width} options={READING_WIDTHS.map((w) => ({ id: w.id, label: w.label }))} onChange={(width) => update({ width: width as ReadingPrefs['width'] })} />
      </div>
    </div>
  )
}

function Segmented({ label, value, options, onChange }: { label: string; value: string; options: { id: string; label: string }[]; onChange: (id: string) => void }) {
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
      <div className="flex rounded-lg border border-app bg-muted p-0.5" role="radiogroup" aria-label={label}>
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={value === o.id}
            onClick={() => onChange(o.id)}
            className={clsx('flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors', value === o.id ? 'bg-panel text-app shadow-sm' : 'text-muted hover:text-app')}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}
