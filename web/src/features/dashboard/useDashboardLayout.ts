/**
 * Chargement et sauvegarde de la disposition du tableau de bord.
 *
 * - cache local Dexie `meta['dashboard.layout']` : affichage immédiat, hors-ligne compris ;
 * - serveur `settings.dashboard_layout` : référence partagée entre appareils ;
 * - réconciliation par la **version serveur** (`dashboard_layout_version`, incrémentée à chaque écriture) :
 *   chaque PATCH annonce la version sur laquelle il se fonde ; le serveur répond 409 si un autre appareil
 *   a écrit entre-temps, et le client adopte alors la disposition serveur ;
 * - écriture locale immédiate, PATCH avec délai.
 */

import { useLiveQuery } from 'dexie-react-hooks'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, ApiError, apiUrl, authHeaders, isOnline, unwrap, type Category, type Feed } from '../../api/client'
import { db, getMeta, setMeta } from '../../db/db'
import { buildDefaultLayout, parseLayout, serializeLayout, type AnyWidget, type DashboardLayout, type ParsedLayout } from './layout'

const META_KEY = 'dashboard.layout'
const DIRTY_KEY = 'dashboard.layout.dirty'
/** Version serveur sur laquelle repose le cache local (0 = jamais reçue). */
const VERSION_KEY = 'dashboard.layout.version'
const SAVE_DELAY_MS = 1000

let saveTimer: number | null = null
let pendingJson: string | null = null
/** Copie synchrone de la version connue, pour l'envoi `keepalive` à la fermeture de l'onglet. */
let knownVersion = 0

async function getKnownVersion(): Promise<number> {
  const raw = await getMeta(VERSION_KEY)
  const n = raw ? Number(raw) : 0
  knownVersion = Number.isFinite(n) ? n : 0
  return knownVersion
}

async function setKnownVersion(version: number): Promise<void> {
  knownVersion = version
  await setMeta(VERSION_KEY, String(version))
}

type PatchOutcome = { kind: 'ok'; version: number } | { kind: 'conflict'; serverJson: string; version: number } | { kind: 'error' }

/** Dispositions envoyées par cet appareil (les dernières) : un 409 qui renvoie l'une d'elles n'est
 * pas l'écriture d'un autre appareil mais la nôtre, partie par un autre chemin d'envoi. */
const sentByThisDevice: string[] = []
function rememberSent(json: string): void {
  sentByThisDevice.push(json)
  if (sentByThisDevice.length > 8) sentByThisDevice.shift()
}

async function patchOnce(json: string): Promise<PatchOutcome> {
  const base = await getKnownVersion()
  rememberSent(json)
  try {
    const out = unwrap(await api().PATCH('/api/settings', { body: { values: { dashboard_layout: json }, dashboard_layout_base_version: base } }))
    await setKnownVersion(out.dashboard_layout_version)
    return { kind: 'ok', version: out.dashboard_layout_version }
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const d = (err.detail as { detail?: { dashboard_layout?: string; dashboard_layout_version?: number } } | undefined)?.detail
      if (d && typeof d.dashboard_layout_version === 'number') return { kind: 'conflict', serverJson: d.dashboard_layout ?? '', version: d.dashboard_layout_version }
    }
    return { kind: 'error' }
  }
}

let sending: Promise<unknown> = Promise.resolve()

/**
 * Envoie la disposition en annonçant la version de base ; `conflict` = un autre appareil a écrit
 * entre-temps. Trois chemins envoient (délai d'écriture, moteur de sync, fermeture de l'onglet) : les
 * envois passent un par un, et un refus dû à notre propre écriture précédente n'est pas un conflit.
 */
function patchServer(json: string): Promise<PatchOutcome> {
  const run = sending.then(async (): Promise<PatchOutcome> => {
    const out = await patchOnce(json)
    if (out.kind !== 'conflict') return out
    if (out.serverJson === json) {
      // Le serveur a déjà cette disposition (envoi à la fermeture de l'onglet, dont la réponse n'a pas été lue).
      await setKnownVersion(out.version)
      return { kind: 'ok', version: out.version }
    }
    if (sentByThisDevice.includes(out.serverJson)) {
      // Il a une de nos dispositions précédentes : on repart de sa version.
      await setKnownVersion(out.version)
      return patchOnce(json)
    }
    return out
  })
  sending = run.catch(() => undefined)
  return run
}

/** Adopte la disposition serveur (conflit ou version plus récente) et abandonne la locale. */
async function adoptServer(json: string, version: number): Promise<void> {
  if (json) await setMeta(META_KEY, json)
  else await db.meta.delete(META_KEY)
  await db.meta.delete(DIRTY_KEY)
  await setKnownVersion(version)
}

let onConflict: ((message: string) => void) | undefined

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
  const out = await patchServer(json)
  if (out.kind === 'ok') {
    // Le drapeau ne tombe que si le JSON envoyé est encore celui en mémoire (pas d'écriture pendant la requête).
    if ((await getMeta(META_KEY)) === json) await db.meta.delete(DIRTY_KEY)
  } else if (out.kind === 'conflict') {
    await adoptServer(out.serverJson, out.version)
    onConflict?.('Disposition mise à jour depuis un autre appareil')
  }
}

/** Revient à la disposition automatique : efface le cache local et la valeur serveur. */
export async function resetDashboardLayout(): Promise<void> {
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
  await db.meta.delete(META_KEY)
  await db.meta.delete(DIRTY_KEY)
  const out = await patchServer('')
  if (out.kind === 'conflict') {
    await adoptServer(out.serverJson, out.version)
    onConflict?.('Disposition mise à jour depuis un autre appareil')
  } else if (out.kind === 'error') {
    throw new Error('Réinitialisation impossible : serveur injoignable')
  }
}

/**
 * Décide quelle disposition garder au montage. Fonction pure, testable.
 * `localBase` est la version serveur sur laquelle repose le cache local ; `serverVersion` la version courante.
 */
export function reconcile(local: ParsedLayout, localDirty: boolean, localBase: number, server: ParsedLayout, serverVersion: number): 'keep-local' | 'take-server' | 'push-local' | 'none' {
  if (server.kind === 'newer') return 'take-server'
  if (localDirty) {
    // Modification locale en attente : envoyable seulement si personne n'a écrit depuis notre base.
    return localBase === serverVersion ? 'push-local' : 'take-server'
  }
  if (serverVersion !== localBase) return 'take-server'
  if (local.kind !== 'ok' && server.kind === 'ok') return 'take-server'
  return local.kind === 'ok' || server.kind === 'ok' ? 'keep-local' : 'none'
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
  useEffect(() => {
    notifyRef.current = notify
  })

  // Réconciliation avec le serveur, une fois par montage quand on est en ligne.
  useEffect(() => {
    let cancelled = false
    async function run() {
      try {
        // Hors ligne : on s'en tient au cache, ou à la disposition automatique s'il n'y en a pas
        // (le `finally` lève l'état de chargement).
        if (!isOnline()) return
        const settings = unwrap(await api().GET('/api/settings'))
        if (cancelled) return
        const server = parseLayout(settings.values.dashboard_layout)
        const serverVersion = settings.dashboard_layout_version
        const localRaw = await getMeta(META_KEY)
        const local = parseLayout(localRaw)
        const dirty = (await getMeta(DIRTY_KEY)) === '1'
        const localBase = await getKnownVersion()
        const decision = reconcile(local, dirty, localBase, server, serverVersion)
        if (server.kind === 'newer') setReadOnly(true)
        if (decision === 'take-server') {
          await adoptServer(settings.values.dashboard_layout, serverVersion)
          // Même contenu que le cache : c'est notre écriture, envoyée à la fermeture de l'onglet.
          if (dirty && settings.values.dashboard_layout !== localRaw) notifyRef.current?.('Disposition mise à jour depuis un autre appareil')
        } else if (decision === 'push-local' && localRaw) {
          const out = await patchServer(localRaw)
          if (out.kind === 'ok') {
            if ((await getMeta(META_KEY)) === localRaw) await db.meta.delete(DIRTY_KEY)
          } else if (out.kind === 'conflict') {
            await adoptServer(out.serverJson, out.version)
            notifyRef.current?.('Disposition mise à jour depuis un autre appareil')
          }
        } else if (decision === 'keep-local' || decision === 'none') {
          await setKnownVersion(serverVersion)
        }
      } catch {
        /* hors-ligne ou serveur injoignable : on garde le cache */
      } finally {
        if (!cancelled) setServerChecked(true)
      }
    }
    onConflict = (m) => notifyRef.current?.(m)
    void run()
    const onOnline = () => void flushDashboardLayout()
    window.addEventListener('online', onOnline)
    return () => {
      cancelled = true
      onConflict = undefined
      window.removeEventListener('online', onOnline)
    }
  }, [])

  // Fermeture de l'onglet pendant le délai d'écriture : envoi immédiat.
  useEffect(() => {
    const onHide = () => {
      if (saveTimer === null || !pendingJson) return
      window.clearTimeout(saveTimer)
      saveTimer = null
      rememberSent(pendingJson)
      // Synchrone : pas de lecture Dexie ici, l'onglet peut disparaître avant sa résolution.
      void fetch(apiUrl('/api/settings'), {
        method: 'PATCH',
        keepalive: true,
        headers: { ...authHeaders(), 'content-type': 'application/json' },
        body: JSON.stringify({ values: { dashboard_layout: pendingJson }, dashboard_layout_base_version: knownVersion }),
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
      pendingJson = json
      void (async () => {
        await setMeta(META_KEY, json)
        await setMeta(DIRTY_KEY, '1')
      })()
      if (saveTimer !== null) window.clearTimeout(saveTimer)
      saveTimer = window.setTimeout(() => {
        saveTimer = null
        pendingJson = null
        void (async () => {
          const out = await patchServer(json)
          if (out.kind === 'ok') {
            // Ne pas effacer le drapeau si une écriture plus récente a eu lieu entre-temps.
            const current = await getMeta(META_KEY)
            if (current === json) await db.meta.delete(DIRTY_KEY)
          } else if (out.kind === 'conflict') {
            await adoptServer(out.serverJson, out.version)
            notifyRef.current?.('Disposition mise à jour depuis un autre appareil')
          }
        })()
      }, SAVE_DELAY_MS)
    },
    [readOnly],
  )

  return { layout, isDefault, readOnly, loading: cachedRaw === undefined && !serverChecked, save, reset: resetDashboardLayout }
}
