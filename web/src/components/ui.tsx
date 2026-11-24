import clsx from 'clsx'
import { Loader2, X } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'

import { useT } from '../i18n'
import { ConfirmContext, ToastContext, type ConfirmOptions, type ToastKind } from './feedback'

/* ---------- Boutons ---------- */

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

export function Button({
  variant = 'secondary',
  size = 'md',
  loading,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:opacity-50 disabled:cursor-not-allowed',
        size === 'sm' ? 'h-8 px-3 text-sm' : 'h-10 px-4 text-sm',
        variant === 'primary' && 'bg-accent-600 text-white hover:bg-accent-700',
        variant === 'secondary' && 'bg-muted text-app hover:bg-slate-200 dark:hover:bg-slate-700 border border-app',
        variant === 'ghost' && 'text-muted hover:bg-muted hover:text-app',
        variant === 'danger' && 'bg-red-600 text-white hover:bg-red-700',
        className,
      )}
    >
      {loading && <Loader2 className="size-4 animate-spin" />}
      {children}
    </button>
  )
}

export function IconButton({
  label,
  active,
  className,
  children,
  size = 'md',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; active?: boolean; size?: Size }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className={clsx(
        'inline-flex items-center justify-center rounded-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:opacity-40',
        size === 'sm' ? 'size-8' : 'size-9',
        active ? 'text-accent-600 dark:text-accent-300 bg-accent-50 dark:bg-accent-900/30' : 'text-muted hover:text-app hover:bg-muted',
        className,
      )}
    >
      {children}
    </button>
  )
}

/* ---------- Champs ---------- */

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        'h-10 w-full rounded-lg border border-app bg-panel px-3 text-sm text-app placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent-400',
        className,
      )}
    />
  )
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={clsx(
        'h-10 w-full rounded-lg border border-app bg-panel px-3 text-sm text-app focus:outline-none focus:ring-2 focus:ring-accent-400',
        className,
      )}
    >
      {children}
    </select>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-app">{label}</span>
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx('size-5 animate-spin text-muted', className)} />
}

/* ---------- Dialogue ---------- */

/** Fenêtres ouvertes, dans l'ordre d'ouverture. */
const openDialogs: symbol[] = []

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  width = 'max-w-md',
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
  width?: string
}) {
  const t = useT().app.ui
  useEffect(() => {
    if (!open) return
    // Fenêtres empilées (confirmation au-dessus d'un éditeur) : Échap ne ferme que celle du dessus.
    const id = Symbol('dialog')
    openDialogs.push(id)
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || openDialogs[openDialogs.length - 1] !== id) return
      e.stopImmediatePropagation()
      onClose()
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      openDialogs.splice(openDialogs.indexOf(id), 1)
    }
  }, [open, onClose])

  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center safe-area sm:p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      <div
        className={clsx(
          'relative w-full bg-panel text-app rounded-t-2xl sm:rounded-2xl shadow-xl border border-app max-h-[92vh] flex flex-col',
          width,
        )}
        data-dialog
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-app">
          <h2 className="text-base font-semibold">{title}</h2>
          <IconButton label={t.close} onClick={onClose} size="sm">
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="px-5 py-4 overflow-y-auto">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-app flex justify-end gap-2">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

/* ---------- Notifications ---------- */

interface Toast {
  id: number
  message: string
  kind: ToastKind
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const counter = useRef(0)
  const push = useCallback((message: string, kind: ToastKind = 'info') => {
    const id = ++counter.current
    setToasts((t) => [...t, { id, message, kind }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 6000 : 3500)
  }, [])
  const value = useMemo(() => push, [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-[calc(1rem+var(--sab))] left-1/2 -translate-x-1/2 z-[60] flex flex-col gap-2 items-center pointer-events-none px-4 w-full max-w-md">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              'pointer-events-auto rounded-lg px-4 py-2.5 text-sm shadow-lg border',
              t.kind === 'error' && 'bg-red-600 text-white border-red-700',
              t.kind === 'success' && 'bg-emerald-600 text-white border-emerald-700',
              t.kind === 'info' && 'bg-slate-900 text-white border-slate-700 dark:bg-slate-100 dark:text-slate-900',
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

/* ---------- Confirmation ---------- */

/** Confirmation dans l'application : `window.confirm` peut être bloqué en silence par le navigateur
 * (« empêcher ce site d'ouvrir des boîtes de dialogue », extensions) et renvoie alors faux. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const t = useT().app.ui
  const [pending, setPending] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null)
  const ask = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...options, resolve })), [])
  const answer = useCallback(
    (ok: boolean) =>
      setPending((p) => {
        p?.resolve(ok)
        return null
      }),
    [],
  )
  const cancel = useCallback(() => answer(false), [answer])
  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      <Dialog
        open={pending !== null}
        onClose={cancel}
        title={pending?.title ?? ''}
        footer={
          <>
            <Button variant="ghost" onClick={cancel}>
              {t.cancel}
            </Button>
            <Button variant={pending?.danger ? 'danger' : 'primary'} onClick={() => answer(true)} autoFocus data-testid="confirm-ok">
              {pending?.confirmLabel ?? t.confirm}
            </Button>
          </>
        }
      >
        {pending?.message && <p className="text-sm text-muted whitespace-pre-line">{pending.message}</p>}
      </Dialog>
    </ConfirmContext.Provider>
  )
}

/* ---------- Divers ---------- */

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      {icon && <div className="text-muted/60">{icon}</div>}
      <p className="text-base font-medium text-app">{title}</p>
      {hint && <p className="text-sm text-muted max-w-xs">{hint}</p>}
      {action}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.4rem] h-5 items-center justify-center rounded border border-app bg-muted px-1 text-[11px] font-mono text-muted">
      {children}
    </kbd>
  )
}
