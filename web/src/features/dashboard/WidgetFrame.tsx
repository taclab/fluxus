import clsx from 'clsx'
import { ArrowDown, ArrowUp, CheckCheck, ChevronRight, GripVertical, MoreHorizontal, Settings2, Trash2, Unlink } from 'lucide-react'
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'

import { IconButton, Spinner } from '../../components/ui'
import { WIDTHS, WIDTH_LABEL, type WidgetWidth } from './layout'

export interface WidgetActions {
  onMoveUp?: () => void
  onMoveDown?: () => void
  onResize?: (w: WidgetWidth) => void
  onConfigure?: () => void
  onRemove?: () => void
}

interface Props {
  widgetId: string
  widgetType: string
  title: string
  count?: number
  editing: boolean
  width: WidgetWidth
  orphan?: boolean
  loading?: boolean
  empty?: boolean
  emptyLabel?: string
  onOpen?: () => void
  onMarkAllRead?: () => void
  actions: WidgetActions
  /** Poignée dnd-kit : ref + attributs + écouteurs fournis par le parent triable. */
  handleProps?: ButtonHTMLAttributes<HTMLButtonElement> & { ref?: (el: HTMLElement | null) => void }
  dragging?: boolean
  children: ReactNode
}

export function WidgetFrame({ widgetId, widgetType, title, count, editing, width, orphan, loading, empty, emptyLabel, onOpen, onMarkAllRead, actions, handleProps, dragging, children }: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  return (
    <section
      data-widget
      data-widget-id={widgetId}
      data-widget-type={widgetType}
      className={clsx(
        'flex flex-col rounded-2xl border border-app bg-panel text-app shadow-sm min-h-[8rem]',
        editing && 'ring-1 ring-accent-200 dark:ring-accent-800/60',
        dragging && 'opacity-40',
      )}
    >
      <header className="flex items-center gap-1.5 px-4 py-2.5 border-b border-app min-h-[3rem]">
        {editing && (
          <button
            type="button"
            data-dnd-handle
            aria-label="Déplacer le widget"
            title="Glisser pour déplacer"
            className="touch-none cursor-grab active:cursor-grabbing -ml-2 rounded-md p-1 text-muted hover:text-app hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
            {...handleProps}
          >
            <GripVertical className="size-4" />
          </button>
        )}
        {onOpen && !orphan ? (
          <button type="button" onClick={onOpen} className="group flex items-center gap-1 min-w-0 text-left" title="Ouvrir">
            <h2 className="truncate text-sm font-semibold">{title}</h2>
            <ChevronRight className="size-3.5 text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
          </button>
        ) : (
          <h2 className={clsx('truncate text-sm font-semibold', orphan && 'text-muted italic')}>{title}</h2>
        )}
        {count !== undefined && count > 0 && <span className="text-xs tabular-nums text-muted shrink-0">{count > 999 ? '999+' : count}</span>}
        <div className="flex-1" />
        {onMarkAllRead && !editing && !orphan && (
          <IconButton label="Tout marquer comme lu" size="sm" onClick={onMarkAllRead} disabled={empty}>
            <CheckCheck className="size-4" />
          </IconButton>
        )}
        {editing && (
          <div className="relative" ref={menuRef}>
            <IconButton label="Options du widget" size="sm" onClick={() => setMenuOpen((o) => !o)} active={menuOpen}>
              <MoreHorizontal className="size-4" />
            </IconButton>
            {menuOpen && (
              <div role="menu" className="absolute right-0 top-full z-20 mt-1 w-56 rounded-xl border border-app bg-panel p-1 shadow-lg text-sm">
                <MenuItem icon={<ArrowUp className="size-4" />} label="Monter" onClick={actions.onMoveUp} close={() => setMenuOpen(false)} />
                <MenuItem icon={<ArrowDown className="size-4" />} label="Descendre" onClick={actions.onMoveDown} close={() => setMenuOpen(false)} />
                <div className="flex items-center gap-1 px-2 py-1.5 text-muted">
                  <span className="flex-1 text-xs">Largeur</span>
                  {WIDTHS.map((w) => (
                    <button
                      key={w}
                      type="button"
                      role="menuitemradio"
                      aria-checked={w === width}
                      data-width={w}
                      onClick={() => {
                        actions.onResize?.(w)
                        setMenuOpen(false)
                      }}
                      className={clsx('h-7 min-w-7 rounded-md px-1.5 text-xs font-medium', w === width ? 'bg-accent-600 text-white' : 'hover:bg-muted text-app')}
                    >
                      {WIDTH_LABEL[w]}
                    </button>
                  ))}
                </div>
                <MenuItem icon={<Settings2 className="size-4" />} label="Configurer…" onClick={actions.onConfigure} close={() => setMenuOpen(false)} />
                <MenuItem icon={<Trash2 className="size-4" />} label="Retirer" danger onClick={actions.onRemove} close={() => setMenuOpen(false)} />
              </div>
            )}
          </div>
        )}
      </header>

      <div className="flex-1 min-h-0">
        {orphan ? (
          <div className="flex flex-col items-center gap-3 p-6 text-center text-sm text-muted">
            <Unlink className="size-6" />
            <p>La cible de ce widget n’existe plus.</p>
            <div className="flex gap-2">
              {actions.onConfigure && (
                <button type="button" onClick={actions.onConfigure} className="rounded-lg border border-app px-3 py-1.5 text-app hover:bg-muted">
                  Changer la source
                </button>
              )}
              {actions.onRemove && (
                <button type="button" onClick={actions.onRemove} className="rounded-lg border border-app px-3 py-1.5 text-red-600 dark:text-red-400 hover:bg-muted">
                  Retirer
                </button>
              )}
            </div>
          </div>
        ) : loading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : empty ? (
          <p className="p-6 text-center text-sm text-muted">{emptyLabel ?? 'Rien de nouveau'}</p>
        ) : (
          children
        )}
      </div>
    </section>
  )
}

function MenuItem({ icon, label, onClick, close, danger }: { icon: ReactNode; label: string; onClick?: () => void; close: () => void; danger?: boolean }) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={!onClick}
      onClick={() => {
        onClick?.()
        close()
      }}
      className={clsx('flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-muted disabled:opacity-40', danger ? 'text-red-600 dark:text-red-400' : 'text-app')}
    >
      {icon}
      {label}
    </button>
  )
}
