import { ChevronLeft, Download, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { getServerUrl, isDeviceToken, getToken, setToken } from '../../api/auth'
import { api, apiUrl, authHeaders, unwrap, type DeviceOut, type SettingsOut, type StatsOut } from '../../api/client'
import type { Theme } from '../../app/theme'
import { useTheme } from '../../components/ThemeToggle'
import { Button, Field, IconButton, Input, Select, Spinner, useToast } from '../../components/ui'
import { clearDatabase } from '../../db/db'
import { formatTokens, relativeDate } from '../../lib/format'
import { resetAndSync, syncNow } from '../../sync/engine'

const ANTHROPIC_MODELS = [
  { id: 'claude-opus-5', label: 'Claude Opus 5 (recommandé)' },
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5 (moins cher)' },
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5 (le plus rapide)' },
]

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOut | null>(null)
  const [stats, setStats] = useState<StatsOut | null>(null)
  const [devices, setDevices] = useState<DeviceOut[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [theme, setTheme] = useTheme()
  const [pwd, setPwd] = useState({ current: '', next: '', confirm: '' })
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const navigate = useNavigate()

  async function load() {
    try {
      const [s, st, d] = await Promise.all([api().GET('/api/settings'), api().GET('/api/settings/stats'), api().GET('/api/auth/devices')])
      const settingsData = unwrap(s)
      setSettings(settingsData)
      setValues(settingsData.values)
      setStats(unwrap(st))
      setDevices(unwrap(d))
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
      const data = (await res.json()) as { imported: number; skipped: number; categories_created: number }
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
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = 'fluxus.opml'
      a.click()
      URL.revokeObjectURL(a.href)
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
      unwrap(await api().POST('/api/auth/password', { body: { current_password: pwd.current, new_password: pwd.next } }))
      toast('Mot de passe modifié', 'success')
      setPwd({ current: '', next: '', confirm: '' })
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
    setToken(null)
    await clearDatabase()
    navigate('/')
  }

  const provider = values.llm_provider ?? 'anthropic'

  return (
    <div className="h-full overflow-y-auto bg-app">
      <header className="sticky top-0 z-10 flex items-center gap-2 h-14 px-2 border-b border-app bg-panel">
        <Link to="/">
          <IconButton label="Retour">
            <ChevronLeft className="size-5" />
          </IconButton>
        </Link>
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
          </Section>

          <Section title="Abonnements" description="Import et export au format OPML, compatible avec tous les lecteurs.">
            <div className="flex flex-wrap gap-2">
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
              </p>
            )}
          </Section>

          <Section title="Hors-ligne" description="Les articles des 30 derniers jours sont conservés sur cet appareil, avec leur contenu complet.">
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
