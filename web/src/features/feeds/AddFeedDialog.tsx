import { useState, type FormEvent } from 'react'

import { api, ApiError, unwrap, type Category } from '../../api/client'
import { useToast } from '../../components/feedback'
import { Button, Dialog, Field, Input, Select } from '../../components/ui'
import { syncNow } from '../../sync/engine'
import { useT } from '../../i18n'

interface Choice {
  url: string
  title: string
}

export function AddFeedDialog({
  open,
  onClose,
  categories,
  defaultCategoryId,
  initialUrl = '',
}: {
  open: boolean
  onClose: () => void
  categories: Category[]
  defaultCategoryId?: number | null
  /** Adresse préremplie (partage depuis une autre application) ; lue au montage. */
  initialUrl?: string
}) {
  const [url, setUrl] = useState(initialUrl)
  const [categoryId, setCategoryId] = useState<string>(defaultCategoryId ? String(defaultCategoryId) : '')
  const [choices, setChoices] = useState<Choice[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()
  const tr = useT()
  const t = tr.feeds.add

  function reset() {
    setUrl('')
    setChoices(null)
    setError(null)
  }

  async function subscribe(feedUrl: string) {
    setBusy(true)
    setError(null)
    try {
      const feed = unwrap(
        await api().POST('/api/feeds', {
          body: { url: feedUrl, category_id: categoryId ? Number(categoryId) : null, extraction_mode: 'auto' },
        }),
      )
      toast(t.subscribed(feed.title), 'success')
      reset()
      onClose()
      window.setTimeout(() => void syncNow(), 1500)
      window.setTimeout(() => void syncNow(), 8000)
    } catch (err) {
      if (err instanceof ApiError && err.status === 300) {
        const detail = (err.detail as { detail?: { choices?: Choice[] } })?.detail
        setChoices(detail?.choices ?? [])
      } else {
        setError(err instanceof Error ? err.message : t.unknownError)
      }
    } finally {
      setBusy(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!url.trim()) return
    await subscribe(url.trim())
  }

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title={t.title}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label={t.urlLabel} hint={t.urlHint}>
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t.urlPlaceholder} autoFocus type="text" inputMode="url" autoCapitalize="off" />
        </Field>
        <Field label={t.category}>
          <Select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">{t.noCategory}</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>

        {choices && (
          <div className="space-y-2">
            <p className="text-sm text-muted">{t.severalFound}</p>
            {choices.map((c) => (
              <button key={c.url} type="button" onClick={() => void subscribe(c.url)} className="w-full text-left rounded-lg border border-app px-3 py-2 hover:bg-muted">
                <span className="block text-sm font-medium">{c.title || t.feed}</span>
                <span className="block text-xs text-muted truncate">{c.url}</span>
              </button>
            ))}
          </div>
        )}

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            {tr.common.cancel}
          </Button>
          <Button type="submit" variant="primary" loading={busy}>
            {t.subscribe}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
