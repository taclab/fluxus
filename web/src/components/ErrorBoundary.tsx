import { Component, type ErrorInfo, type ReactNode } from 'react'

import { getT } from '../i18n'

interface State {
  error: Error | null
}

/** Dernier rempart : une erreur de rendu affiche un message et un bouton de rechargement au lieu d'une page blanche. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Erreur de rendu', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    const t = getT().app.errorBoundary
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-app p-8 text-center text-app">
        <p className="text-lg font-semibold">{t.title}</p>
        <p className="max-w-md text-sm text-muted">{this.state.error.message}</p>
        <div className="flex gap-2">
          <button type="button" onClick={() => window.location.reload()} className="rounded-lg bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700">
            {t.reload}
          </button>
          <button type="button" onClick={() => (window.location.href = '/')} className="rounded-lg border border-app px-4 py-2 text-sm font-medium hover:bg-muted">
            {t.home}
          </button>
        </div>
      </div>
    )
  }
}
