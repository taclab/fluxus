import { ChevronLeft, Download, ListChecks, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { getServerUrl, isDeviceToken, getToken, setToken } from '../../api/auth'
import { api, apiUrl, authHeaders, unwrap, type DeviceOut, type Schemas, type SettingsOut, type StatsOut } from '../../api/client'
import type { Theme } from '../../app/theme'
import { useTheme } from '../../app/useTheme'
import { TypographyPicker } from '../../components/TypographyPicker'
import { useConfirm, useToast } from '../../components/feedback'
import { Button, Field, IconButton, Input, Select, Spinner } from '../../components/ui'
import { clearDatabase, db } from '../../db/db'
import { resetDashboardLayout } from '../dashboard/useDashboardLayout'
import { BackupSection } from './BackupSection'
import { setViewMode, useDefaultViewMode } from '../entries/useViewMode'
import { LAYOUT_GROUPS, VIEW_MODE_SPECS, modesFor, type ListLayout } from '../entries/viewMode'
import { saveBlob } from '../../lib/download'
import { formatBytes, formatTokens, relativeDate } from '../../lib/format'
import { pushOutbox, resetAndSync, syncNow } from '../../sync/engine'

const ANTHROPIC_MODELS = [
  { id: 'claude-opus-5', label: 'Claude Opus 5 (recommandé)' },
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5 (moins cher)' },
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5 (le plus rapide)' },
]

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOut | null>(null)
  const [stats, setStats] = useState<StatsOut | null>(null)
  const [devices, setDevices] = useState<DeviceOut[]>([])
  const [autoUsage, setAutoUsage] = useState<{ tokens_today: number; budget: number; count: number } | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [theme, setTheme] = useTheme()
  const [pwd, setPwd] = useState({ current: '', next: '', confirm: '', revokeDevices: false })
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const confirm = useConfirm()
  const navigate = useNavigate()

  async function load() {
    try {
      const [s, st, d, sf] = await Promise.all([api().GET('/api/settings'), api().GET('/api/settings/stats'), api().GET('/api/auth/devices'), api().GET('/api/smart-feeds')])
      const settingsData = unwrap(s)
      setSettings(settingsData)
      setValues(settingsData.values)
      setStats(unwrap(st))
      setDevices(unwrap(d))
      const smart = unwrap(sf)
      setAutoUsage({ tokens_today: smart.tokens_today, budget: smart.budget, count: smart.items.reduce((a, i) => a + i.auto_today, 0) })
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Impossible de charger les paramètres', 'error')
    }
  }

  useEffect(() => {
    void load()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function set(key: string, value: string) {
    setValues((v) => {
      const next = { ...v, [key]: value }
      // Changer de fournisseur remet un modèle cohérent si l'actuel appartient à l'autre famille.
      if (key === 'llm_provider') {
        const model = v.llm_model ?? ''
        if (value === 'openai' && (!model || model.startsWith('claude'))) next.llm_model = 'gpt-4.1-mini'
        if (value === 'anthropic' && !model.startsWith('claude')) next.llm_model = 'claude-opus-5'
      }
      return next
    })
  }

  async function save() {
    setSaving(true)
    try {
      const patch: Record<string, string> = {}
      for (const [k, v] of Object.entries(values)) {
        if (settings && settings.values[k] !== v) patch[k] = v
      }
      if (Object.keys(patch).length) {
        const updated = unwrap(await api().PATCH('/api/settings', { body: { values: patch } }))
        setSettings(updated)
        setValues(updated.values)
      }
      toast('Paramètres enregistrés', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setSaving(false)
    }
  }

  async function importOpml(file: File) {
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch(apiUrl('/api/opml/import'), { method: 'POST', headers: authHeaders(), body: form })
      if (!res.ok) throw new Error((await res.json()).detail ?? 'Import impossible')
      const data = (await res.json()) as Schemas['OpmlImportResult']
      toast(`${data.imported} flux importés, ${data.skipped} ignorés`, 'success')
      window.setTimeout(() => void syncNow(), 3000)
      window.setTimeout(() => void syncNow(), 15000)
      void load()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    }
  }

  async function exportOpml() {
    try {
      const res = await fetch(apiUrl('/api/opml/export'), { headers: authHeaders() })
      saveBlob(await res.blob(), 'fluxus.opml')
    } catch {
      toast('Export impossible', 'error')
    }
  }

  async function changePassword() {
    if (pwd.next !== pwd.confirm) {
      toast('Les mots de passe ne correspondent pas', 'error')
      return
    }
    try {
      const res = unwrap(
        await api().POST('/api/auth/password', { body: { current_password: pwd.current, new_password: pwd.next, revoke_devices: pwd.revokeDevices } }),
      )
      // Les anciennes sessions sont invalidées : on garde celle-ci avec le jeton renvoyé.
      setToken(res.access_token)
      toast(pwd.revokeDevices ? 'Mot de passe modifié, appareils déconnectés' : 'Mot de passe modifié, autres sessions déconnectées', 'success')
      setPwd({ current: '', next: '', confirm: '', revokeDevices: false })
      void load()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    }
  }

  async function revoke(id: number) {
    try {
      unwrap(await api().DELETE('/api/auth/devices/{device_id}', { params: { path: { device_id: id } } }))
      setDevices((d) => d.filter((x) => x.id !== id))
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    }
  }

  async function logout() {
    // La déconnexion vide la base locale : ce qui n'est pas encore parti vers le serveur serait perdu.
    await pushOutbox().catch(() => undefined)
    const pending = await db.outbox.count()
    if (pending > 0) {
      const ok = await confirm({
        title: 'Se déconnecter ?',
        message: `${pending} ${pending > 1 ? 'changements n’ont pas pu être envoyés' : 'changement n’a pas pu être envoyé'} au serveur (lu, favori) et ${pending > 1 ? 'seront perdus' : 'sera perdu'}.`,
        confirmLabel: 'Se déconnecter quand même',
        danger: true,
      })
      if (!ok) return
    }
    setToken(null)
    await clearDatabase()
    navigate('/')
  }

  const provider = values.llm_provider ?? 'anthropic'

  return (
    <div className="h-full overflow-y-auto bg-app">
      <header className="sticky top-0 z-10 flex items-center gap-2 h-14 px-2 border-b border-app bg-panel">
        <IconButton label="Retour" onClick={() => navigate('/')}>
          <ChevronLeft className="size-5" />
        </IconButton>
        <h1 className="font-semibold">Paramètres</h1>
        <div className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => void save()} loading={saving} disabled={!settings}>
          Enregistrer
        </Button>
      </header>

      {!settings ? (
        <div className="flex justify-center p-10">
          <Spinner />
        </div>
      ) : (
        <div className="mx-auto max-w-2xl px-4 py-6 space-y-8">
          <Section title="Résumés" description="Les résumés ne sont produits qu’à votre demande. La clé est chiffrée sur le serveur.">
            <Field label="Fournisseur">
              <Select value={provider} onChange={(e) => set('llm_provider', e.target.value)}>
                <option value="anthropic">Anthropic (Claude)</option>
                <option value="openai">OpenAI ou API compatible</option>
              </Select>
            </Field>
            {provider === 'anthropic' ? (
              <>
                <Field label="Clé API Anthropic" hint={settings.secrets_set.llm_api_key_anthropic ? 'Une clé est enregistrée. Saisissez-en une autre pour la remplacer.' : 'Obtenue sur console.anthropic.com'}>
                  <Input type="password" value={values.llm_api_key_anthropic ?? ''} onChange={(e) => set('llm_api_key_anthropic', e.target.value)} placeholder="sk-ant-…" autoComplete="off" />
                </Field>
                <Field label="Modèle">
                  <Select value={ANTHROPIC_MODELS.some((m) => m.id === values.llm_model) ? values.llm_model : 'custom'} onChange={(e) => e.target.value !== 'custom' && set('llm_model', e.target.value)}>
                    {ANTHROPIC_MODELS.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                    <option value="custom">Autre…</option>
                  </Select>
                </Field>
                <Field label="Identifiant du modèle">
                  <Input value={values.llm_model ?? ''} onChange={(e) => set('llm_model', e.target.value)} />
                </Field>
                <Field label="Effort de raisonnement" hint="« Faible » suffit pour un résumé et coûte le moins de tokens.">
                  <Select value={values.llm_effort ?? 'low'} onChange={(e) => set('llm_effort', e.target.value)}>
                    <option value="low">Faible (rapide, économique)</option>
                    <option value="medium">Moyen</option>
                    <option value="high">Élevé</option>
                  </Select>
                </Field>
              </>
            ) : (
              <>
                <Field label="Clé API OpenAI" hint={settings.secrets_set.llm_api_key_openai ? 'Une clé est enregistrée.' : undefined}>
                  <Input type="password" value={values.llm_api_key_openai ?? ''} onChange={(e) => set('llm_api_key_openai', e.target.value)} placeholder="sk-…" autoComplete="off" />
                </Field>
                <Field label="Modèle">
                  <Input value={values.llm_model ?? ''} onChange={(e) => set('llm_model', e.target.value)} placeholder="gpt-4.1-mini" />
                </Field>
                <Field label="URL de base (optionnel)" hint="Pour un service compatible OpenAI (Mistral, Groq, Ollama distant…).">
                  <Input value={values.openai_base_url ?? ''} onChange={(e) => set('openai_base_url', e.target.value)} placeholder="https://api.mistral.ai/v1" />
                </Field>
              </>
            )}
            <Field label="Langue des résumés">
              <Select value={values.llm_language ?? 'fr'} onChange={(e) => set('llm_language', e.target.value)}>
                <option value="fr">Français</option>
                <option value="en">Anglais</option>
                <option value="es">Espagnol</option>
                <option value="de">Allemand</option>
              </Select>
            </Field>
            {stats && (
              <p className="text-xs text-muted">
                {stats.summaries} résumés produits · {formatTokens(stats.summary_input_tokens)} tokens en entrée · {formatTokens(stats.summary_output_tokens)} en sortie
              </p>
            )}

            <div className="rounded-xl border border-app p-4 space-y-3">
              <h3 className="text-sm font-semibold text-app">Résumés automatiques des flux intelligents</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Modèle dédié" hint="Un modèle économique suffit pour des résumés en volume.">
                  <Input value={values.llm_auto_model ?? ''} onChange={(e) => set('llm_auto_model', e.target.value)} placeholder={provider === 'anthropic' ? 'claude-sonnet-5' : 'gpt-4.1-mini'} />
                </Field>
                <Field label="Budget quotidien (tokens)" hint="0 désactive les résumés automatiques. Jour en UTC.">
                  <Input type="number" min={0} step={10000} value={values.llm_auto_daily_token_budget ?? '200000'} onChange={(e) => set('llm_auto_daily_token_budget', e.target.value)} />
                </Field>
              </div>
              {autoUsage && (
                <p className="text-xs text-muted">
                  Aujourd’hui : {autoUsage.count} résumé{autoUsage.count > 1 ? 's' : ''} automatique{autoUsage.count > 1 ? 's' : ''} · {formatTokens(autoUsage.tokens_today)} / {formatTokens(autoUsage.budget)} tokens
                  {autoUsage.budget > 0 && autoUsage.tokens_today >= autoUsage.budget && <span className="ml-2 font-medium text-amber-600 dark:text-amber-400">budget atteint</span>}
                </p>
              )}
            </div>
          </Section>

          <Section
            title="Affichage par défaut"
            description="Présentation des vues Tous, Non lus, Favoris et Recherche, et de tout flux ou dossier sans présentation propre. Chaque flux ou dossier se règle depuis le menu Présentation de sa vue ou depuis l’écran Abonnements ; un flux sans réglage suit son dossier. Synchronisé entre appareils."
          >
            <DefaultViewModePicker />
          </Section>

          <Section title="Lecture" description="Combinaison de polices, taille et largeur de colonne pour les articles. Réglage propre à cet appareil, aussi accessible par le bouton Aa du lecteur.">
            <TypographyPicker />
          </Section>

          <Section title="Apparence">
            <Field label="Thème">
              <Select
                value={theme}
                onChange={(e) => setTheme(e.target.value as Theme)}
              >
                <option value="system">Automatique (système)</option>
                <option value="light">Clair</option>
                <option value="dark">Sombre</option>
              </Select>
            </Field>
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                onClick={async () => {
                  try {
                    await resetDashboardLayout()
                    toast('Tableau de bord réinitialisé', 'success')
                  } catch (err) {
                    toast(err instanceof Error ? err.message : 'Erreur', 'error')
                  }
                }}
              >
                Réinitialiser le tableau de bord
              </Button>
              <span className="text-xs text-muted">Revient à la disposition automatique : un widget par catégorie.</span>
            </div>
          </Section>

          <Section title="Abonnements" description="Réglages flux par flux (pause, article complet, résumés automatiques, intervalle, présentation) dans l’écran Abonnements ; import et export au format OPML, compatible avec tous les lecteurs.">
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => navigate('/abonnements')} data-testid="open-subscriptions">
                <ListChecks className="size-4" /> Gérer les abonnements
              </Button>
              <Button onClick={() => fileRef.current?.click()}>
                <Upload className="size-4" /> Importer un OPML
              </Button>
              <input ref={fileRef} type="file" accept=".opml,.xml,text/xml,text/x-opml" className="hidden" onChange={(e) => e.target.files?.[0] && void importOpml(e.target.files[0])} />
              <Button onClick={() => void exportOpml()}>
                <Download className="size-4" /> Exporter
              </Button>
              <Button
                variant="ghost"
                onClick={async () => {
                  const r = unwrap(await api().POST('/api/feeds/refresh-all'))
                  toast(`${r.queued} flux en cours de rafraîchissement`)
                  window.setTimeout(() => void syncNow(), 5000)
                }}
              >
                Tout rafraîchir
              </Button>
            </div>
            {stats && (
              <p className="text-xs text-muted">
                {stats.feeds} flux · {stats.entries} articles · {stats.unread} non lus · {stats.starred} favoris
                {stats.jobs_failed > 0 && ` · ${stats.jobs_failed} tâches en échec`}
                {stats.disk_free_bytes != null && ` · ${formatBytes(stats.disk_free_bytes)} libres sur le disque du serveur`}
              </p>
            )}
            {stats?.disk_low && (
              <p role="alert" className="text-xs font-medium text-red-600 dark:text-red-400">
                Espace disque presque épuisé : la base s’arrête quand le disque est plein. Libérez de la place sur le serveur.
              </p>
            )}
          </Section>

          <Section title="Sauvegarde et migration" description="Pour changer de serveur ou garder une copie : exportez toute la base ici, puis importez le fichier sur l’autre instance (ou sur celle-ci pour la restaurer).">
            <BackupSection onImported={() => void load()} />
          </Section>

          <Section title="Hors-ligne" description="Les articles des N derniers jours sont conservés sur chaque appareil avec leur contenu complet ; les favoris le sont toujours.">
            <Field label="Horizon de conservation (jours)" hint="S’applique à la première synchronisation d’un appareil et au ménage local. Enregistrer pour appliquer.">
              <Select value={values.sync_prefetch_days ?? '30'} onChange={(e) => set('sync_prefetch_days', e.target.value)}>
                {['7', '14', '30', '60', '90', '180'].map((d) => (
                  <option key={d} value={d}>
                    {d} jours
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void syncNow()}>Synchroniser maintenant</Button>
              <Button variant="ghost" onClick={() => void resetAndSync()}>
                Resynchroniser tout
              </Button>
            </div>
          </Section>

          <Section title="Appareils" description="Tokens longue durée utilisés par l’application mobile.">
            {devices.length === 0 && <p className="text-sm text-muted">Aucun appareil appairé.</p>}
            <ul className="divide-y divide-app rounded-lg border border-app">
              {devices.map((d) => (
                <li key={d.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <span className="flex-1 truncate">{d.name}</span>
                  <span className="text-xs text-muted">{d.last_seen_at ? `vu ${relativeDate(d.last_seen_at)}` : 'jamais utilisé'}</span>
                  <IconButton label="Révoquer" size="sm" onClick={() => void revoke(d.id)}>
                    <Trash2 className="size-4" />
                  </IconButton>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Sécurité">
            <Field label="Mot de passe actuel">
              <Input type="password" value={pwd.current} onChange={(e) => setPwd({ ...pwd, current: e.target.value })} autoComplete="current-password" />
            </Field>
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="Nouveau mot de passe">
                <Input type="password" value={pwd.next} onChange={(e) => setPwd({ ...pwd, next: e.target.value })} minLength={8} autoComplete="new-password" />
              </Field>
              <Field label="Confirmer">
                <Input type="password" value={pwd.confirm} onChange={(e) => setPwd({ ...pwd, confirm: e.target.value })} minLength={8} autoComplete="new-password" />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={pwd.revokeDevices} onChange={(e) => setPwd({ ...pwd, revokeDevices: e.target.checked })} className="size-4 accent-accent-600" />
              Déconnecter aussi les appareils mobiles (à réappairer)
            </label>
            <Button onClick={() => void changePassword()} disabled={!pwd.current || pwd.next.length < 8}>
              Changer le mot de passe
            </Button>
          </Section>

          <Section title="Session">
            <p className="text-sm text-muted">
              Serveur : <span className="font-mono text-xs">{getServerUrl() || window.location.origin}</span>
              {isDeviceToken(getToken()) && ' · token d’appareil'}
            </p>
            <Button variant="danger" onClick={() => void logout()}>
              Se déconnecter et effacer les données locales
            </Button>
          </Section>
        </div>
      )}
    </div>
  )
}

const LAYOUT_HINTS: Record<ListLayout, string> = {
  split: 'Liste à gauche, article à droite.',
  full: 'Articles sur toute la largeur, lecture dans une fenêtre ; ← → pour changer d’article.',
}

/** Présentation par défaut : deux familles (article à côté / pleine largeur), avec un schéma de chacune. */
function DefaultViewModePicker() {
  const current = useDefaultViewMode()
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {LAYOUT_GROUPS.map((g) => (
        <div key={g.layout} className="rounded-xl border border-app p-3">
          <div className="flex gap-3">
            <LayoutSketch layout={g.layout} />
            <span className="min-w-0">
              <span className="block font-medium text-app">{g.label}</span>
              <span className="block text-xs text-muted mt-0.5">{LAYOUT_HINTS[g.layout]}</span>
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-2" role="radiogroup" aria-label={g.label}>
            {modesFor(g.layout).map((id) => {
              const { Icon, label } = VIEW_MODE_SPECS[id]
              const active = id === current
              return (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-testid={`default-view-${id}`}
                  onClick={() => void setViewMode({ kind: 'default' }, id)}
                  className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm transition-colors ${active ? 'border-accent-500 bg-accent-50 font-medium text-app dark:bg-accent-900/20' : 'border-app text-muted hover:bg-muted'}`}
                >
                  <Icon className="size-4" />
                  {label}
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

function LayoutSketch({ layout }: { layout: ListLayout }) {
  const bar = 'rounded-[2px] bg-current opacity-40'
  return (
    <span aria-hidden className="grid h-14 w-20 shrink-0 grid-cols-6 gap-[3px] rounded-md border border-app bg-app p-1.5 text-muted">
      {layout === 'split' ? (
        <>
          <span className="col-span-2 flex flex-col gap-[3px]">
            <span className={`h-2 ${bar}`} />
            <span className={`h-2 ${bar}`} />
            <span className={`h-2 ${bar}`} />
            <span className={`h-2 ${bar}`} />
          </span>
          <span className="col-span-4 rounded-[3px] border border-current opacity-50" />
        </>
      ) : (
        <>
          {Array.from({ length: 6 }).map((_, i) => (
            <span key={i} className={`h-[18px] ${bar} ${i === 2 || i === 3 ? 'invisible' : ''}`} />
          ))}
          <span className="col-span-4 col-start-2 -mt-[19px] h-[28px] rounded-[3px] border-2 border-accent-500 bg-app" />
        </>
      )}
    </span>
  )
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="space-y-4 rounded-2xl border border-app bg-panel p-5">
      <div>
        <h2 className="font-semibold text-app">{title}</h2>
        {description && <p className="text-sm text-muted mt-0.5">{description}</p>}
      </div>
      {children}
    </section>
  )
}
