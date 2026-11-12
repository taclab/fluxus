/** Contextes et hooks des notifications et de la confirmation ; les fournisseurs sont dans `ui.tsx`
 * (un fichier de composants n'exporte que des composants, sinon le rechargement à chaud se désactive). */

import { createContext, useContext, type ReactNode } from 'react'

export type ToastKind = 'info' | 'error' | 'success'

export const ToastContext = createContext<(message: string, kind?: ToastKind) => void>(() => {})

export function useToast() {
  return useContext(ToastContext)
}

export interface ConfirmOptions {
  title: string
  message?: ReactNode
  confirmLabel?: string
  danger?: boolean
}

export const ConfirmContext = createContext<(options: ConfirmOptions) => Promise<boolean>>(async () => false)

/** Confirmation dans l'application, jamais `window.confirm` (voir `ConfirmProvider`). */
export function useConfirm() {
  return useContext(ConfirmContext)
}
