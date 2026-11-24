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
import { LOCALE_NAMES, LOCALES, localePref, useT, type Locale } from '../../i18n'
import { pushOutbox, resetAndSync, syncNow } from '../../sync/engine'

const ANTHROPIC_MODELS = [
  { id: 'claude-opus-5', name: 'Claude Opus 5', note: 'modelRecommended' },
  { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', note: 'modelCheaper' },
  { id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5', note: 'modelFastest' },
] as const

const SUMMARY_LANGUAGES = ['fr', 'en', 'es', 'de'] as const

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
  const t = useT()
  const ts = t.settings
  const locale = localePref.useValue()

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
      toast(err instanceof Error ? err.message : ts.loadFailed, 'error')
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
      toast(ts.saved, 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function importOpml(file: File) {
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch(apiUrl('/api/opml/import'), { method: 'POST', headers: authHeaders(), body: form })
      if (!res.ok) throw new Error((await res.json()).detail ?? ts.subscriptions.importFailed)
      const data = (await res.json()) as Schemas['OpmlImportResult']
      toast(ts.subscriptions.imported(data.imported, data.skipped), 'success')
      window.setTimeout(() => void syncNow(), 3000)
      window.setTimeout(() => void syncNow(), 15000)
      void load()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  async function exportOpml() {
    try {
      const res = await fetch(apiUrl('/api/opml/export'), { headers: authHeaders() })
      saveBlob(await res.blob(), 'fluxus.opml')
    } catch {
      toast(ts.subscriptions.exportFailed, 'error')
    }
  }

  async function changePassword() {
    if (pwd.next !== pwd.confirm) {
      toast(ts.security.mismatch, 'error')
      return
    }
    try {
      const res = unwrap(
        await api().POST('/api/auth/password', { body: { current_password: pwd.current, new_password: pwd.next, revoke_devices: pwd.revokeDevices } }),
      )
      // Les anciennes sessions sont invalidées : on garde celle-ci avec le jeton renvoyé.
      setToken(res.access_token)
      toast(pwd.revokeDevices ? ts.security.changedDevices : ts.security.changedSessions, 'success')
      setPwd({ current: '', next: '', confirm: '', revokeDevices: false })
      void load()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  async function revoke(id: number) {
    try {
      unwrap(await api().DELETE('/api/auth/devices/{device_id}', { params: { path: { device_id: id } } }))
      setDevices((d) => d.filter((x) => x.id !== id))
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  async function logout() {
    // La déconnexion vide la base locale : ce qui n'est pas encore parti vers le serveur serait perdu.
    await pushOutbox().catch(() => undefined)
    const pending = await db.outbox.count()
    if (pending > 0) {
      const ok = await confirm({
        title: ts.session.logoutTitle,
        message: ts.session.logoutMessage(pending),
        confirmLabel: ts.session.logoutAnyway,
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
        <IconButton label={ts.back} onClick={() => navigate('/')}>
          <ChevronLeft className="size-5" />
        </IconButton>
        <h1 className="font-semibold">{ts.title}</h1>
        <div className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => void save()} loading={saving} disabled={!settings}>
          {t.common.save}
        </Button>
      </header>

      {!settings ? (
        <div className="flex justify-center p-10">
          <Spinner />
        </div>
      ) : (
        <div className="mx-auto max-w-2xl px-4 py-6 space-y-8">
          <Section title={ts.summaries.title} description={ts.summaries.description}>
            <Field label={ts.summaries.provider}>
              <Select value={provider} onChange={(e) => set('llm_provider', e.target.value)}>
                <option value="anthropic">Anthropic (Claude)</option>
                <option value="openai">{ts.summaries.providerOpenai}</option>
              </Select>
            </Field>
            {provider === 'anthropic' ? (
              <>
                <Field label={ts.summaries.anthropicKey} hint={settings.secrets_set.llm_api_key_anthropic ? ts.summaries.anthropicKeySet : ts.summaries.anthropicKeyHint}>
                  <Input type="password" value={values.llm_api_key_anthropic ?? ''} onChange={(e) => set('llm_api_key_anthropic', e.target.value)} placeholder="sk-ant-…" autoComplete="off" />
                </Field>
                <Field label={ts.summaries.model}>
                  <Select value={ANTHROPIC_MODELS.some((m) => m.id === values.llm_model) ? values.llm_model : 'custom'} onChange={(e) => e.target.value !== 'custom' && set('llm_model', e.target.value)}>
                    {ANTHROPIC_MODELS.map((m) => (
                      <option key={m.id} value={m.id}>
                        {ts.summaries[m.note](m.name)}
                      </option>
                    ))}
                    <option value="custom">{ts.summaries.modelOther}</option>
                  </Select>
                </Field>
                <Field label={ts.summaries.modelId}>
                  <Input value={values.llm_model ?? ''} onChange={(e) => set('llm_model', e.target.value)} />
                </Field>
                <Field label={ts.summaries.effort} hint={ts.summaries.effortHint}>
                  <Select value={values.llm_effort ?? 'low'} onChange={(e) => set('llm_effort', e.target.value)}>
                    <option value="low">{ts.summaries.effortLow}</option>
                    <option value="medium">{ts.summaries.effortMedium}</option>
                    <option value="high">{ts.summaries.effortHigh}</option>
                  </Select>
                </Field>
              </>
            ) : (
              <>
                <Field label={ts.summaries.openaiKey} hint={settings.secrets_set.llm_api_key_openai ? ts.summaries.openaiKeySet : undefined}>
                  <Input type="password" value={values.llm_api_key_openai ?? ''} onChange={(e) => set('llm_api_key_openai', e.target.value)} placeholder="sk-…" autoComplete="off" />
                </Field>
                <Field label={ts.summaries.model}>
                  <Input value={values.llm_model ?? ''} onChange={(e) => set('llm_model', e.target.value)} placeholder="gpt-4.1-mini" />
                </Field>
                <Field label={ts.summaries.baseUrl} hint={ts.summaries.baseUrlHint}>
                  <Input value={values.openai_base_url ?? ''} onChange={(e) => set('openai_base_url', e.target.value)} placeholder="https://api.mistral.ai/v1" />
                </Field>
              </>
            )}
            <Field label={ts.summaries.language}>
              <Select value={values.llm_language ?? 'fr'} onChange={(e) => set('llm_language', e.target.value)}>
                {SUMMARY_LANGUAGES.map((l) => (
                  <option key={l} value={l}>
                    {ts.summaries.languages[l]}
                  </option>
                ))}
              </Select>
            </Field>
            {stats && (
              <p className="text-xs text-muted">
                {ts.summaries.stats(stats.summaries, formatTokens(stats.summary_input_tokens), formatTokens(stats.summary_output_tokens))}
              </p>
            )}

            <div className="rounded-xl border border-app p-4 space-y-3">
              <h3 className="text-sm font-semibold text-app">{ts.summaries.autoTitle}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={ts.summaries.autoModel} hint={ts.summaries.autoModelHint}>
                  <Input value={values.llm_auto_model ?? ''} onChange={(e) => set('llm_auto_model', e.target.value)} placeholder={provider === 'anthropic' ? 'claude-sonnet-5' : 'gpt-4.1-mini'} />
                </Field>
                <Field label={ts.summaries.autoBudget} hint={ts.summaries.autoBudgetHint}>
                  <Input type="number" min={0} step={10000} value={values.llm_auto_daily_token_budget ?? '200000'} onChange={(e) => set('llm_auto_daily_token_budget', e.target.value)} />
                </Field>
              </div>
              {autoUsage && (
                <p className="text-xs text-muted">
                  {ts.summaries.autoToday(autoUsage.count, formatTokens(autoUsage.tokens_today), formatTokens(autoUsage.budget))}
                  {autoUsage.budget > 0 && autoUsage.tokens_today >= autoUsage.budget && <span className="ml-2 font-medium text-amber-600 dark:text-amber-400">{ts.summaries.budgetReached}</span>}
                </p>
              )}
            </div>
          </Section>

          <Section
            title={ts.defaultView.title}
            description={ts.defaultView.description}
          >
            <DefaultViewModePicker />
          </Section>

          <Section title={ts.reading.title} description={ts.reading.description}>
            <TypographyPicker />
          </Section>

          <Section title={ts.appearance.title}>
            <Field label={ts.appearance.language} hint={ts.appearance.languageHint}>
              <Select value={locale} onChange={(e) => localePref.set(e.target.value as Locale)} data-testid="interface-language">
                {LOCALES.map((l) => (
                  <option key={l} value={l} lang={l}>
                    {LOCALE_NAMES[l]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={ts.appearance.theme}>
              <Select
                value={theme}
                onChange={(e) => setTheme(e.target.value as Theme)}
              >
                <option value="system">{ts.appearance.themeSystem}</option>
                <option value="light">{ts.appearance.themeLight}</option>
                <option value="dark">{ts.appearance.themeDark}</option>
              </Select>
            </Field>
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                onClick={async () => {
                  try {
                    await resetDashboardLayout()
                    toast(ts.appearance.dashboardReset, 'success')
                  } catch (err) {
                    toast(err instanceof Error ? err.message : t.common.error, 'error')
                  }
                }}
              >
                {ts.appearance.resetDashboard}
              </Button>
              <span className="text-xs text-muted">{ts.appearance.resetDashboardHint}</span>
            </div>
          </Section>

          <Section title={ts.subscriptions.title} description={ts.subscriptions.description}>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => navigate('/abonnements')} data-testid="open-subscriptions">
                <ListChecks className="size-4" /> {ts.subscriptions.manage}
              </Button>
              <Button onClick={() => fileRef.current?.click()}>
                <Upload className="size-4" /> {ts.subscriptions.importOpml}
              </Button>
              <input ref={fileRef} type="file" accept=".opml,.xml,text/xml,text/x-opml" className="hidden" onChange={(e) => e.target.files?.[0] && void importOpml(e.target.files[0])} />
              <Button onClick={() => void exportOpml()}>
                <Download className="size-4" /> {ts.subscriptions.export}
              </Button>
              <Button
                variant="ghost"
                onClick={async () => {
                  const r = unwrap(await api().POST('/api/feeds/refresh-all'))
                  toast(ts.subscriptions.refreshing(Number(r.queued)))
                  window.setTimeout(() => void syncNow(), 5000)
                }}
              >
                {ts.subscriptions.refreshAll}
              </Button>
            </div>
            {stats && (
              <p className="text-xs text-muted">
                {ts.subscriptions.stats(stats.feeds, stats.entries, stats.unread, stats.starred)}
                {stats.jobs_failed > 0 && ts.subscriptions.failedJobs(stats.jobs_failed)}
                {stats.disk_free_bytes != null && ts.subscriptions.diskFree(formatBytes(stats.disk_free_bytes))}
              </p>
            )}
            {stats?.disk_low && (
              <p role="alert" className="text-xs font-medium text-red-600 dark:text-red-400">
                {ts.subscriptions.diskLow}
              </p>
            )}
          </Section>

          <Section title={ts.backup.title} description={ts.backup.description}>
            <BackupSection onImported={() => void load()} />
          </Section>

          <Section title={ts.offline.title} description={ts.offline.description}>
            <Field label={ts.offline.horizon} hint={ts.offline.horizonHint}>
              <Select value={values.sync_prefetch_days ?? '30'} onChange={(e) => set('sync_prefetch_days', e.target.value)}>
                {['7', '14', '30', '60', '90', '180'].map((d) => (
                  <option key={d} value={d}>
                    {ts.offline.days(d)}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void syncNow()}>{ts.offline.syncNow}</Button>
              <Button variant="ghost" onClick={() => void resetAndSync()}>
                {ts.offline.resyncAll}
              </Button>
            </div>
          </Section>

          <Section title={ts.devices.title} description={ts.devices.description}>
            {devices.length === 0 && <p className="text-sm text-muted">{ts.devices.none}</p>}
            <ul className="divide-y divide-app rounded-lg border border-app">
              {devices.map((d) => (
                <li key={d.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <span className="flex-1 truncate">{d.name}</span>
                  <span className="text-xs text-muted">{d.last_seen_at ? ts.devices.seen(relativeDate(d.last_seen_at)) : ts.devices.neverUsed}</span>
                  <IconButton label={ts.devices.revoke} size="sm" onClick={() => void revoke(d.id)}>
                    <Trash2 className="size-4" />
                  </IconButton>
                </li>
              ))}
            </ul>
          </Section>

          <Section title={ts.security.title}>
            <Field label={ts.security.currentPassword}>
              <Input type="password" value={pwd.current} onChange={(e) => setPwd({ ...pwd, current: e.target.value })} autoComplete="current-password" />
            </Field>
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label={ts.security.newPassword}>
                <Input type="password" value={pwd.next} onChange={(e) => setPwd({ ...pwd, next: e.target.value })} minLength={8} autoComplete="new-password" />
              </Field>
              <Field label={ts.security.confirm}>
                <Input type="password" value={pwd.confirm} onChange={(e) => setPwd({ ...pwd, confirm: e.target.value })} minLength={8} autoComplete="new-password" />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={pwd.revokeDevices} onChange={(e) => setPwd({ ...pwd, revokeDevices: e.target.checked })} className="size-4 accent-accent-600" />
              {ts.security.revokeDevices}
            </label>
            <Button onClick={() => void changePassword()} disabled={!pwd.current || pwd.next.length < 8}>
              {ts.security.change}
            </Button>
          </Section>

          <Section title={ts.session.title}>
            <p className="text-sm text-muted">
              {ts.session.server}<span className="font-mono text-xs">{getServerUrl() || window.location.origin}</span>
              {isDeviceToken(getToken()) && ts.session.deviceToken}
              {` · Fluxus ${__APP_VERSION__}`}
            </p>
            <Button variant="danger" onClick={() => void logout()}>
              {ts.session.logout}
            </Button>
          </Section>
        </div>
      )}
    </div>
  )
}

/** Présentation par défaut : deux familles (article à côté / pleine largeur), avec un schéma de chacune. */
function DefaultViewModePicker() {
  const current = useDefaultViewMode()
  const t = useT().settings.defaultView
  const layoutHints: Record<ListLayout, string> = { split: t.splitHint, full: t.fullHint }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {LAYOUT_GROUPS.map((g) => (
        <div key={g.layout} className="rounded-xl border border-app p-3">
          <div className="flex gap-3">
            <LayoutSketch layout={g.layout} />
            <span className="min-w-0">
              <span className="block font-medium text-app">{g.label}</span>
              <span className="block text-xs text-muted mt-0.5">{layoutHints[g.layout]}</span>
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
