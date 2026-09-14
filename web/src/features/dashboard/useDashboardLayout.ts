/**
 * Chargement et sauvegarde de la disposition du tableau de bord.
 *
 * - cache local Dexie `meta['dashboard.layout']` : affichage immédiat, hors-ligne compris ;
 * - serveur `settings.dashboard_layout` : référence partagée entre appareils ;
 * - réconciliation par `updatedAt` embarqué dans le JSON ; écriture locale immédiate, PATCH avec délai.
 */

import { useLiveQuery } from 'dexie-react-hooks'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, apiUrl, authHeaders, isOnline, unwrap, type Category, type Feed } from '../../api/client'
import { db, getMeta, setMeta } from '../../db/db'
import { buildDefaultLayout, parseLayout, serializeLayout, type AnyWidget, type DashboardLayout, type ParsedLayout } from './layout'

const META_KEY = 'dashboard.layout'
const DIRTY_KEY = 'dashboard.layout.dirty'
const SAVE_DELAY_MS = 1000

let saveTimer: number | null = null

async function patchServer(json: string): Promise<boolean> {
  try {
    unwrap(await api().PATCH('/api/settings', { body: { values: { dashboard_layout: json } } }))
    return true
  } catch {
    return false
  }
}

/** Envoie au serveur une disposition locale en attente. Appelé par le moteur de sync et à `online`. */
export async function flushDashboardLayout(): Promise<void> {
  if (!isOnline()) return
  const dirty = await getMeta(DIRTY_KEY)
  if (dirty !== '1') return
  const json = await getMeta(META_KEY)
  if (json === undefined) {
    await db.meta.delete(DIRTY_KEY)
    return
  }
  if (await patchServer(json)) await db.meta.delete(DIRTY_KEY)
}

/** Décide quelle version garder. Fonction pure, testable. */
export function reconcile(local: ParsedLayout, localDirty: boolean, server: ParsedLayout): 'keep-local' | 'take-server' | 'push-local' | 'none' {
  if (server.kind === 'newer') return 'take-server'
  // Serveur vide : soit on y pousse une modification locale en attente, soit c'est une réinitialisation
  // faite ailleurs et le cache local doit être effacé.
  if (server.kind === 'empty') return local.kind === 'ok' && localDirty ? 'push-local' : local.kind === 'ok' ? 'take-server' : 'none'
  if (local.kind !== 'ok') return 'take-server'
  const l = Date.parse(local.layout.updatedAt)
  const s = Date.parse(server.layout.updatedAt)
  if (s > l) return 'take-server'
  if (l > s) return localDirty ? 'push-local' : 'take-server'
  return 'keep-local'
}

export interface DashboardLayoutState {
  layout: DashboardLayout
  /** Aucun layout stocké : disposition dérivée des catégories, recalculée à chaque rendu. */
  isDefault: boolean
  /** Écrit par une version plus récente de l'application : affichage seul. */
  readOnly: boolean
  loading: boolean
  save: (widgets: AnyWidget[]) => void
  reset: () => Promise<void>
}

export function useDashboardLayout(categories: Category[], feeds: Feed[], notify?: (message: string) => void): DashboardLayoutState {
  const cachedRaw = useLiveQuery(() => getMeta(META_KEY), [])
  const [serverChecked, setServerChecked] = useState(false)
  const [readOnly, setReadOnly] = useState(false)
  const notifyRef = useRef(notify)
  notifyRef.current = notify

  // Réconciliation avec le serveur, une fois par montage quand on est en ligne.
  useEffect(() => {
    let cancelled = false
    async function run() {
      if (!isOnline()) return
      try {
        const settings = unwrap(await api().GET('/api/settings'))
        if (cancelled) return
        const server = parseLayout(settings.values.dashboard_layout)
        const localRaw = await getMeta(META_KEY)
        const local = parseLayout(localRaw)
        const dirty = (await getMeta(DIRTY_KEY)) === '1'
        const decision = reconcile(local, dirty, server)
        if (server.kind === 'newer') setReadOnly(true)
        if (decision === 'take-server') {
          if (server.kind === 'empty') await db.meta.delete(META_KEY)
          else await setMeta(META_KEY, settings.values.dashboard_layout)
          await db.meta.delete(DIRTY_KEY)
          if (dirty) notifyRef.current?.('Disposition mise à jour depuis un autre appareil')
        } else if (decision === 'push-local' && localRaw) {
          if (await patchServer(localRaw)) await db.meta.delete(DIRTY_KEY)
        }
      } catch {
        /* hors-ligne ou serveur injoignable : on garde le cache */
      } finally {
        if (!cancelled) setServerChecked(true)
      }
    }
    void run()
    const onOnline = () => void flushDashboardLayout()
    window.addEventListener('online', onOnline)
    return () => {
      cancelled = true
      window.removeEventListener('online', onOnline)
    }
  }, [])

  // Fermeture de l'onglet pendant le délai d'écriture : envoi immédiat.
  useEffect(() => {
    const onHide = () => {
      if (saveTimer === null) return
      window.clearTimeout(saveTimer)
      saveTimer = null
      void getMeta(META_KEY).then((json) => {
        if (!json) return
        void fetch(apiUrl('/api/settings'), {
          method: 'PATCH',
          keepalive: true,
          headers: { ...authHeaders(), 'content-type': 'application/json' },
          body: JSON.stringify({ values: { dashboard_layout: json } }),
        })
      })
    }
    window.addEventListener('pagehide', onHide)
    return () => window.removeEventListener('pagehide', onHide)
  }, [])

  const parsed = useMemo(() => parseLayout(cachedRaw ?? null), [cachedRaw])
  const isDefault = parsed.kind === 'empty'
  const layout = useMemo(() => (parsed.kind === 'empty' ? buildDefaultLayout(categories, feeds) : parsed.layout), [parsed, categories, feeds])

  const save = useCallback(
    (widgets: AnyWidget[]) => {
      if (readOnly) return
      const next: DashboardLayout = { version: 1, updatedAt: new Date().toISOString(), widgets }
      const json = serializeLayout(next)
      void (async () => {
        await setMeta(META_KEY, json)
        await setMeta(DIRTY_KEY, '1')
      })()
      if (saveTimer !== null) window.clearTimeout(saveTimer)
      saveTimer = window.setTimeout(() => {
        saveTimer = null
        void (async () => {
          if (await patchServer(json)) {
            // Ne pas effacer le drapeau si une écriture plus récente a eu lieu entre-temps.
            const current = await getMeta(META_KEY)
            if (current === json) await db.meta.delete(DIRTY_KEY)
          }
        })()
      }, SAVE_DELAY_MS)
    },
    [readOnly],
  )

  const reset = useCallback(async () => {
    if (saveTimer !== null) {
      window.clearTimeout(saveTimer)
      saveTimer = null
    }
    await db.meta.delete(META_KEY)
    await db.meta.delete(DIRTY_KEY)
    await patchServer('')
  }, [])

  return { layout, isDefault, readOnly, loading: cachedRaw === undefined && !serverChecked, save, reset }
}
