import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { getToken, isCapacitor } from '../api/auth'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { ConfirmProvider, ToastProvider } from '../components/ui'
import { LoginPage } from '../features/auth/LoginPage'
import { SubscriptionsPage } from '../features/feeds/SubscriptionsPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { startAutoSync } from '../sync/engine'
import { Layout } from './Layout'
import { applyTheme, getTheme, watchSystemTheme } from './theme'
import { UpdateBanner } from './UpdateBanner'

function useAuthed(): boolean {
  const [authed, setAuthed] = useState(Boolean(getToken()))
  useEffect(() => {
    const handler = () => setAuthed(Boolean(getToken()))
    window.addEventListener('fluxus:auth', handler)
    window.addEventListener('storage', handler)
    return () => {
      window.removeEventListener('fluxus:auth', handler)
      window.removeEventListener('storage', handler)
    }
  }, [])
  return authed
}

export function App() {
  const authed = useAuthed()

  useEffect(() => {
    applyTheme(getTheme())
    return watchSystemTheme()
  }, [])

  useEffect(() => {
    if (!authed) return
    return startAutoSync()
  }, [authed])

  // Stockage persistant : sans lui, le navigateur peut purger IndexedDB (articles hors ligne, file de
  // modifications) quand l'espace manque. Chrome l'accorde sans demander à une appli installée ;
  // Firefox pose la question une fois. Demandé après connexion, pas sur l'écran d'accueil.
  useEffect(() => {
    if (!authed || isCapacitor() || !navigator.storage?.persist) return
    void navigator.storage.persisted().then((done) => (done ? undefined : navigator.storage.persist()))
  }, [authed])

  // Android : le bouton retour matériel remonte l'historique au lieu de quitter l'appli.
  useEffect(() => {
    if (!isCapacitor()) return
    let remove: (() => void) | undefined
    void import('@capacitor/app').then(({ App: CapApp }) => {
      void CapApp.addListener('backButton', ({ canGoBack }) => {
        if (canGoBack) window.history.back()
        else void CapApp.exitApp()
      }).then((h) => {
        remove = () => void h.remove()
      })
    })
    return () => remove?.()
  }, [])

  return (
    <ErrorBoundary>
    <ToastProvider>
      <ConfirmProvider>
      <BrowserRouter>
        {authed ? (
          <Routes>
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/abonnements" element={<SubscriptionsPage />} />
            <Route path="/all" element={<Layout />} />
            <Route path="/feed/:id" element={<Layout />} />
            <Route path="/category/:id" element={<Layout />} />
            <Route path="/smart/:id" element={<Layout />} />
            <Route path="/unread" element={<Layout />} />
            <Route path="/starred" element={<Layout />} />
            <Route path="/search" element={<Layout />} />
            <Route path="*" element={<Layout />} />
          </Routes>
        ) : (
          <LoginPage />
        )}
      </BrowserRouter>
      <UpdateBanner />
      </ConfirmProvider>
    </ToastProvider>
    </ErrorBoundary>
  )
}
