/**
 * Fenêtre de lecture centrée (disposition « pleine largeur ») : contient le lecteur habituel, avec
 * les boutons précédent/suivant et le compteur de position. Pas de `data-dialog` : les raccourcis
 * clavier globaux (m, s, r…) doivent rester actifs pendant la lecture ; ← → sont déclarées ici,
 * et seulement ici, pour n'être capturées que lorsque la fenêtre est ouverte.
 */

import clsx from 'clsx'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { useKeyboard } from '../../app/useKeyboard'

interface Props {
  entryId: number
  index: number // position 0-based dans la liste affichée, -1 si inconnue
  total: number
  onClose: () => void
  onMove: (delta: 1 | -1) => void
  children: ReactNode
}

export function ReaderModal({ entryId, index, total, onClose, onMove, children }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const hasPrev = index > 0
  const hasNext = index !== -1 && index < total - 1

  const arrows = useMemo(() => ({ ArrowLeft: () => onMove(-1), ArrowRight: () => onMove(1) }), [onMove])
  useKeyboard(arrows)

  // Le fond ne défile pas ; le focus va dans la fenêtre.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    box.current?.focus({ preventScroll: true })
    return () => {
      document.body.style.overflow = previous
    }
  }, [])
  // À la fermeture seulement (pas à chaque changement d'article), le focus revient à la ligne du dernier
  // article lu, via le contrat `data-entry-focus` posé par EntryList sur l'élément d'ouverture de chaque ligne.
  const lastId = useRef(entryId)
  useEffect(() => {
    lastId.current = entryId
  }, [entryId])
  useEffect(
    () => () => {
      document.querySelector<HTMLElement>(`[data-entry-id="${lastId.current}"] [data-entry-focus]`)?.focus({ preventScroll: true })
    },
    [],
  )

  const navButton = (delta: 1 | -1, enabled: boolean, extra?: string) => (
    <button
      type="button"
      onClick={() => onMove(delta)}
      disabled={!enabled}
      aria-label={delta === 1 ? 'Article suivant (→)' : 'Article précédent (←)'}
      title={delta === 1 ? 'Article suivant (→)' : 'Article précédent (←)'}
      data-testid={delta === 1 ? 'modal-next' : 'modal-prev'}
      className={clsx(
        'flex size-11 items-center justify-center rounded-full border border-app bg-panel text-app shadow-lg transition-opacity hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400',
        extra,
      )}
    >
      {delta === 1 ? <ChevronRight className="size-5" /> : <ChevronLeft className="size-5" />}
    </button>
  )

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center safe-area" role="dialog" aria-modal="true" aria-label="Lecture de l’article" data-testid="reader-modal">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={onClose} />
      <div className="hidden md:block absolute left-4 top-1/2 -translate-y-1/2">{navButton(-1, hasPrev)}</div>
      <div className="hidden md:block absolute right-4 top-1/2 -translate-y-1/2">{navButton(1, hasNext)}</div>
      <div
        ref={box}
        tabIndex={-1}
        className="relative flex flex-col overflow-hidden bg-app outline-none inset-0 h-full w-full md:inset-auto md:h-[calc(100vh-3rem)] md:w-[min(64rem,calc(100vw-9rem))] md:rounded-2xl md:border md:border-app md:shadow-2xl"
      >
        <div className="flex-1 min-h-0">{children}</div>
        <div className="flex h-10 shrink-0 items-center justify-center gap-3 border-t border-app bg-panel px-3 text-xs text-muted">
          <span className="md:hidden">{navButton(-1, hasPrev, 'size-8 shadow-none')}</span>
          {index !== -1 && total > 0 && (
            <span className="tabular-nums" data-testid="modal-position">
              {index + 1} / {total}
            </span>
          )}
          <span className="hidden md:inline">← → pour changer d’article · Échap pour fermer</span>
          <span className="md:hidden">{navButton(1, hasNext, 'size-8 shadow-none')}</span>
        </div>
      </div>
    </div>,
    document.body,
  )
}
