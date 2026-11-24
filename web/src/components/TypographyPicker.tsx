import clsx from 'clsx'
import { READING_FONTS, READING_SIZES, READING_WIDTHS, type ReadingPrefs } from '../app/reading'
import { useReadingPrefs } from '../app/useReadingPrefs'
import { useT } from '../i18n'

/** Choix de la combinaison de polices, de la taille et de la largeur de colonne (Paramètres → Lecture). */
export function TypographyPicker() {
  const t = useT().app.typography
  const [prefs, update] = useReadingPrefs()
  return (
    <div className="space-y-4">
      <div className="grid gap-2 grid-cols-2 sm:grid-cols-3" role="radiogroup" aria-label={t.fontsLabel}>
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
                <span className="text-2xl font-bold leading-none text-app" style={{ fontFamily: f.headingCss }}>
                  A
                </span>
                <span className="text-2xl leading-none text-app" style={{ fontFamily: f.bodyCss }}>
                  a
                </span>
                <span className="ml-auto text-sm font-medium text-app">{t.fonts[f.id].label}</span>
              </span>
              <span className="mt-1.5 text-xs text-muted leading-snug">{t.fonts[f.id].description}</span>
              <span className="mt-1 text-[11px] text-muted">
                {f.body}
                {f.heading !== f.body && t.headings(f.heading)}
              </span>
            </button>
          )
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Segmented label={t.sizeLabel} value={prefs.size} options={READING_SIZES.map((s) => ({ id: s.id, label: t.sizes[s.id] }))} onChange={(size) => update({ size: size as ReadingPrefs['size'] })} />
        <Segmented label={t.widthLabel} value={prefs.width} options={READING_WIDTHS.map((w) => ({ id: w.id, label: t.widths[w.id] }))} onChange={(width) => update({ width: width as ReadingPrefs['width'] })} />
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
