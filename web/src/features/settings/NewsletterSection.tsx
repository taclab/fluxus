/**
 * Newsletters par e-mail : domaine de réception, réglages de l'Email Worker (URL et secret partagé),
 * adresses. Le secret n'est montré qu'une fois, à sa génération ; le serveur ne le renvoie jamais.
 */

import { useLiveQuery } from 'dexie-react-hooks'
import { Copy, ExternalLink, Mail, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api, apiUrl, unwrap, type Schemas } from '../../api/client'
import { useConfirm, useToast } from '../../components/feedback'
import { Button, Dialog, Field, IconButton, Input, Select } from '../../components/ui'
import { db } from '../../db/db'
import { useT } from '../../i18n'
import { copyText } from '../../lib/clipboard'
import { agoDate } from '../../lib/format'
import { syncNow } from '../../sync/engine'

type Config = Schemas['NewsletterConfigOut']
type Address = Schemas['NewsletterAddressOut']

const GUIDE_URL = 'https://github.com/taclab/fluxus/blob/main/docs/NEWSLETTERS.md'

/** URL de l'instance telle que le Worker doit l'appeler (absolue, même depuis l'application). */
function instanceUrl(): string {
  return new URL(apiUrl('/'), window.location.href).origin
}

export function NewsletterSection() {
  const [config, setConfig] = useState<Config | null>(null)
  const [domain, setDomain] = useState('')
  const [secret, setSecret] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const confirm = useConfirm()
  const t = useT()
  const tn = t.newsletters

  async function load() {
    try {
      const data = unwrap(await api().GET('/api/newsletters'))
      setConfig(data)
      setDomain(data.domain)
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  useEffect(() => {
    void load()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function copy(text: string) {
    const ok = await copyText(text)
    toast(ok ? tn.copied : tn.copyFailed, ok ? 'success' : 'error')
  }

  async function saveDomain() {
    setBusy(true)
    try {
      unwrap(await api().PATCH('/api/settings', { body: { values: { newsletter_domain: domain } } }))
      toast(tn.domain.saved, 'success')
      await load()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function generateSecret() {
    if (config?.secret_set && !(await confirm({ title: tn.worker.regenerateTitle, message: tn.worker.regenerateMessage, confirmLabel: tn.worker.regenerate, danger: true }))) return
    try {
      setSecret(unwrap(await api().POST('/api/newsletters/secret')).secret)
      await load()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  if (!config) return null
  const url = instanceUrl()

  return (
    <div className="space-y-5" data-testid="newsletters">
      <a href={GUIDE_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-accent-600 dark:text-accent-300 underline">
        {tn.section.guide}
        <ExternalLink className="size-3.5" />
      </a>

      <Field label={tn.domain.label} hint={tn.domain.hint}>
        <div className="flex gap-2">
          <Input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder={tn.domain.placeholder} data-testid="newsletter-domain" onKeyDown={(e) => e.key === 'Enter' && void saveDomain()} />
          <Button onClick={() => void saveDomain()} loading={busy} disabled={domain.trim().toLowerCase() === config.domain}>
            {t.common.save}
          </Button>
        </div>
      </Field>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-app">{tn.worker.title}</h3>
        <div className="rounded-lg border border-app divide-y divide-app text-sm">
          <div className="flex items-center gap-3 px-3 py-2">
            <code className="w-32 shrink-0 text-xs text-muted">{tn.worker.urlLabel}</code>
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={tn.worker.urlHint}>
              {url}
            </span>
            <IconButton label={tn.addresses.copy} size="sm" onClick={() => void copy(url)}>
              <Copy className="size-4" />
            </IconButton>
          </div>
          <div className="flex items-center gap-3 px-3 py-2">
            <code className="w-32 shrink-0 text-xs text-muted">{tn.worker.secretLabel}</code>
            <span className={config.secret_set ? 'flex-1 text-app' : 'flex-1 text-amber-600 dark:text-amber-400'} data-testid="newsletter-secret-status">
              {config.secret_set ? tn.worker.secretSet : tn.worker.secretMissing}
            </span>
            <Button size="sm" variant={config.secret_set ? 'ghost' : 'primary'} onClick={() => void generateSecret()} data-testid="newsletter-secret">
              {config.secret_set ? tn.worker.regenerate : tn.worker.generate}
            </Button>
          </div>
        </div>
      </div>

      <AddressList config={config} onChanged={() => void load()} onCopy={(text) => void copy(text)} />

      <Dialog
        open={secret !== null}
        onClose={() => setSecret(null)}
        title={tn.worker.dialogTitle}
        footer={
          <Button variant="primary" onClick={() => setSecret(null)}>
            {tn.worker.done}
          </Button>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-muted">{tn.worker.dialogHint}</p>
          <div className="flex items-center gap-2">
            <Input readOnly value={secret ?? ''} className="font-mono text-xs" onFocus={(e) => e.currentTarget.select()} data-testid="newsletter-secret-value" />
            <IconButton label={tn.addresses.copy} onClick={() => secret && void copy(secret)}>
              <Copy className="size-4" />
            </IconButton>
          </div>
        </div>
      </Dialog>
    </div>
  )
}

function AddressList({ config, onChanged, onCopy }: { config: Config; onChanged: () => void; onCopy: (text: string) => void }) {
  const [label, setLabel] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [dedicated, setDedicated] = useState(false)
  const [busy, setBusy] = useState(false)
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), [])
  const feeds = useLiveQuery(() => db.feeds.where('id').anyOf(config.addresses.flatMap((a) => (a.feed_id ? [a.feed_id] : []))).toArray(), [config])
  const toast = useToast()
  const confirm = useConfirm()
  const t = useT()
  const tn = t.newsletters
  const ta = tn.addresses

  async function create() {
    setBusy(true)
    try {
      unwrap(await api().POST('/api/newsletters/addresses', { body: { label: label.trim(), dedicated, category_id: categoryId ? Number(categoryId) : null } }))
      toast(ta.created, 'success')
      setLabel('')
      setDedicated(false)
      onChanged()
      // Une adresse dédiée crée son flux tout de suite : il doit apparaître dans la barre latérale.
      if (dedicated) void syncNow()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(address: Address) {
    if (!(await confirm({ title: ta.revokeTitle(address.email ?? address.label), message: ta.revokeMessage, confirmLabel: ta.revoke, danger: true }))) return
    try {
      unwrap(await api().DELETE('/api/newsletters/addresses/{address_id}', { params: { path: { address_id: address.id } } }))
      toast(ta.revoked, 'success')
      onChanged()
    } catch (err) {
      toast(err instanceof Error ? err.message : t.common.error, 'error')
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-app">{ta.title}</h3>
      {!config.domain && config.addresses.length > 0 && <p className="text-xs text-amber-600 dark:text-amber-400">{ta.noDomain}</p>}
      {config.addresses.length === 0 ? (
        <p className="text-sm text-muted">{ta.empty}</p>
      ) : (
        <ul className="rounded-lg border border-app divide-y divide-app">
          {config.addresses.map((a) => {
            const feed = feeds?.find((f) => f.id === a.feed_id)
            const shown = a.email ?? a.token
            return (
              <li key={a.id} className="flex items-start gap-3 px-3 py-2.5" data-newsletter-address={a.token}>
                <Mail className="mt-0.5 size-4 shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-app">{a.label}</div>
                  <div className="truncate font-mono text-xs text-app" title={shown}>
                    {shown}
                  </div>
                  <div className="text-xs text-muted">
                    {a.feed_id ? ta.dedicatedTo(feed?.title ?? a.label) : ta.sorted} · {ta.received(a.received_count)}
                    {a.last_received_at && ` · ${ta.lastReceived(agoDate(a.last_received_at))}`}
                  </div>
                </div>
                {a.email && (
                  <IconButton label={ta.copy} size="sm" onClick={() => onCopy(a.email!)}>
                    <Copy className="size-4" />
                  </IconButton>
                )}
                <IconButton label={ta.revoke} size="sm" onClick={() => void revoke(a)} className="hover:text-red-600">
                  <Trash2 className="size-4" />
                </IconButton>
              </li>
            )
          })}
        </ul>
      )}

      <div className="space-y-3 rounded-lg bg-muted p-3">
        <div className="text-sm font-medium text-app">{ta.newTitle}</div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={ta.labelLabel}>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={ta.labelPlaceholder} maxLength={200} data-testid="newsletter-label" />
          </Field>
          <Field label={ta.folder}>
            <Select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
              <option value="">{t.feeds.add.noCategory}</option>
              {(categories ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={dedicated} onChange={(e) => setDedicated(e.target.checked)} className="mt-0.5 size-4 accent-accent-600" data-testid="newsletter-dedicated" />
          <span>
            {ta.dedicated}
            <span className="block text-xs text-muted">{ta.dedicatedHint}</span>
          </span>
        </label>
        <Button variant="primary" size="sm" onClick={() => void create()} loading={busy} disabled={!label.trim()} data-testid="newsletter-create">
          {ta.create}
        </Button>
      </div>
    </div>
  )
}
