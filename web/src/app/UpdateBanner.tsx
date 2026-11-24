import { RefreshCw, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useRegisterSW } from 'virtual:pwa-register/react'

import { isCapacitor } from '../api/auth'
import { Button, IconButton } from '../components/ui'
import { useT } from '../i18n'

// Un onglet ou une application installée peut rester ouvert des jours : on revérifie le service worker.
const UPDATE_CHECK_MS = 60 * 60 * 1000

/** Enregistre le service worker et propose de recharger quand une nouvelle version est prête,
 * plutôt que de recharger en pleine lecture. Dans l'APK, la nouvelle version vient avec l'APK :
 * elle s'applique sans demander. */
export function UpdateBanner() {
  const t = useT().app.update
  const [dismissed, setDismissed] = useState(false)
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_url, registration) {
      if (!registration) return
      window.setInterval(() => {
        if (navigator.onLine) void registration.update()
      }, UPDATE_CHECK_MS)
    },
  })

  useEffect(() => {
    if (needRefresh && isCapacitor()) void updateServiceWorker(true)
  }, [needRefresh, updateServiceWorker])

  if (!needRefresh || dismissed || isCapacitor()) return null
  return (
    <div role="status" className="fixed bottom-[calc(1rem+var(--sab))] right-[calc(1rem+var(--sar))] z-[60] flex items-center gap-3 rounded-lg border border-app bg-panel px-4 py-2.5 text-sm text-app shadow-lg" data-testid="update-banner">
      <span>{t.available}</span>
      <Button variant="primary" size="sm" onClick={() => void updateServiceWorker(true)}>
        <RefreshCw className="size-4" /> {t.reload}
      </Button>
      <IconButton label={t.later} size="sm" onClick={() => setDismissed(true)}>
        <X className="size-4" />
      </IconButton>
    </div>
  )
}
