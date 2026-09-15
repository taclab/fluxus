import { useEffect, useState, type FormEvent } from 'react'

import { getServerUrl, isCapacitor, setServerUrl, setToken } from '../../api/auth'
import { api, ApiError, reconfigureClient, unwrap } from '../../api/client'
import { Button, Field, Input } from '../../components/ui'

export function LoginPage() {
  const [serverUrl, setServer] = useState(getServerUrl())
  const [showServer, setShowServer] = useState(isCapacitor() || !getServerUrl())
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Statut du serveur interrogé sur l'adresse saisie (pas sur le client courant), avec un léger délai.
  useEffect(() => {
    let cancelled = false
    setSetupRequired(null)
    const base = serverUrl.trim().replace(/\/+$/, '')
    if (showServer && !/^https?:\/\/[^\s/]+/i.test(base)) return
    const timer = window.setTimeout(() => {
      fetch(`${base}/api/auth/status`, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { setup_required?: boolean }) => {
          if (!cancelled) setSetupRequired(Boolean(d.setup_required))
        })
        .catch(() => {
          if (!cancelled) setSetupRequired(false)
        })
    }, 400)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [serverUrl, showServer])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (setupRequired && password !== confirm) {
      setError('Les deux mots de passe ne correspondent pas')
      return
    }
    setBusy(true)
    try {
      if (showServer) {
        const previous = getServerUrl()
        setServerUrl(serverUrl)
        if (getServerUrl() !== previous) {
          // La base locale est nommée d'après le serveur : on recharge pour l'ouvrir sous le bon nom,
          // sans réutiliser le cache, l'outbox ni le curseur de sync de l'ancienne instance.
          window.location.reload()
          return
        }
        reconfigureClient()
      }
      if (setupRequired) {
        const res = unwrap(await api().POST('/api/auth/setup', { body: { password } }))
        setToken(res.access_token)
      } else if (isCapacitor()) {
        // Sur mobile : token d'appareil longue durée plutôt qu'un JWT qui expire.
        const name = `Android · ${new Date().toLocaleDateString('fr-FR')}`
        const res = unwrap(await api().POST('/api/auth/devices', { body: { name, password } }))
        setToken(res.token)
      } else {
        const res = unwrap(await api().POST('/api/auth/login', { body: { password } }))
        setToken(res.access_token)
      }
    } catch (err) {
      if (err instanceof ApiError) setError(err.message)
      else setError('Serveur injoignable. Vérifiez l’adresse.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center p-6 bg-app">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 bg-panel border border-app rounded-2xl p-7 shadow-sm">
        <div className="flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-10 rounded-xl" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Fluxus</h1>
            <p className="text-sm text-muted">
              {setupRequired === null ? 'Connexion au serveur…' : setupRequired ? 'Première connexion : choisissez un mot de passe' : 'Connectez-vous pour continuer'}
            </p>
          </div>
        </div>

        {showServer && (
          <Field label="Adresse du serveur" hint="Exemple : https://fluxus.mondomaine.fr">
            <Input
              type="url"
              value={serverUrl}
              onChange={(e) => setServer(e.target.value)}
              placeholder="https://…"
              autoCapitalize="off"
              autoCorrect="off"
              required
            />
          </Field>
        )}

        <Field label="Mot de passe">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            minLength={8}
            required
            autoComplete={setupRequired ? 'new-password' : 'current-password'}
          />
        </Field>

        {setupRequired && (
          <Field label="Confirmer le mot de passe">
            <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} minLength={8} required autoComplete="new-password" />
          </Field>
        )}

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={setupRequired === null}>
          {setupRequired ? 'Créer le compte' : 'Se connecter'}
        </Button>

        {!showServer && (
          <button type="button" onClick={() => setShowServer(true)} className="text-xs text-muted hover:text-app w-full text-center">
            Changer l’adresse du serveur
          </button>
        )}
      </form>
    </div>
  )
}
