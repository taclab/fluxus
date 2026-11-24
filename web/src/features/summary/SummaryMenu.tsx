import clsx from 'clsx'
import { AlignLeft, BookOpen, Brain, Check, List, Minus, Scale, SendHorizontal, Settings2, Smile, type LucideIcon } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { useT } from '../../i18n'
import { SUMMARY_STYLES, type SummaryRequest, type SummaryStyle } from './summaryStyles'

const ICONS: Record<SummaryStyle, LucideIcon> = {
  standard: AlignLeft,
  oneline: Minus,
  bullets: List,
  detailed: BookOpen,
  opinion: Scale,
  simple: Smile,
}

interface Props {
  /** Types déjà générés pour ce contenu : affichés sans nouvel appel. */
  ready: ReadonlySet<string>
  deep: boolean
  onDeepChange: (deep: boolean) => void
  onPick: (request: SummaryRequest) => void
  onClose: () => void
}

/** Menu des types de résumé : question libre, types, mode approfondi, lien vers les réglages. */
export function SummaryMenu({ ready, deep, onDeepChange, onPick, onClose }: Props) {
  const t = useT().reader.summary
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')

  const ask = (e: FormEvent) => {
    e.preventDefault()
    if (question.trim()) onPick({ question: question.trim() })
  }

  return (
    <div
      // Téléphone : feuille pleine largeur en haut de l'écran (ancré au bouton, le menu déborderait).
      className="fixed inset-x-3 top-[calc(env(safe-area-inset-top)+3.5rem)] z-40 max-h-[calc(100dvh-5rem)] overflow-y-auto rounded-2xl border border-app bg-panel shadow-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-1 sm:max-h-none sm:w-[22rem] sm:overflow-hidden"
      role="menu"
      aria-label={t.menuLabel}
      data-summary-menu
      onKeyDown={(e) => {
        // Échap ferme le menu seulement, pas le lecteur (raccourci global de la mise en page).
        if (e.key === 'Escape') {
          e.stopPropagation()
          onClose()
        }
      }}
    >
      <form onSubmit={ask} className="flex items-center gap-2 border-b border-app px-3 py-2">
        <input
          autoFocus
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={500}
          placeholder={t.askPlaceholder}
          aria-label={t.askPlaceholder}
          className="min-w-0 flex-1 bg-transparent py-1.5 text-sm text-app outline-none placeholder:text-muted"
          data-testid="summary-question"
        />
        <button type="submit" disabled={!question.trim()} aria-label={t.ask} title={t.ask} className="rounded-lg p-1.5 text-accent-600 hover:bg-muted disabled:opacity-40 dark:text-accent-300">
          <SendHorizontal className="size-4" />
        </button>
      </form>

      <ul className="py-1">
        {SUMMARY_STYLES.map((style) => {
          const Icon = ICONS[style]
          return (
            <li key={style}>
              <button
                type="button"
                role="menuitem"
                onClick={() => onPick({ style })}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted"
                data-summary-style={style}
              >
                <Icon className="size-4 shrink-0 text-muted" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-app">{t.styles[style].label}</span>
                  <span className="block truncate text-xs text-muted">{t.styles[style].hint}</span>
                </span>
                {ready.has(style) && (
                  <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted">
                    <Check className="size-3.5" /> {t.generated}
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      <div className="space-y-1 border-t border-app py-1">
        <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-muted">
          <Brain className={clsx('size-4 shrink-0', deep ? 'text-accent-600 dark:text-accent-300' : 'text-muted')} />
          <span className="min-w-0 flex-1">
            <span className="block text-sm text-app">{t.deep}</span>
            <span className="block text-xs text-muted">{t.deepHint}</span>
          </span>
          <input type="checkbox" checked={deep} onChange={(e) => onDeepChange(e.target.checked)} className="size-4 accent-accent-600" data-testid="summary-deep" />
        </label>
        <button type="button" role="menuitem" onClick={() => navigate('/settings#summaries')} className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted">
          <Settings2 className="size-4 shrink-0 text-muted" />
          <span className="min-w-0 flex-1">
            <span className="block text-sm text-app">{t.customize}</span>
            <span className="block text-xs text-muted">{t.customizeHint}</span>
          </span>
        </button>
      </div>
    </div>
  )
}
