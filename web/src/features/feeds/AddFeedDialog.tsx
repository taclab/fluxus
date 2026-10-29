import { useState, type FormEvent } from 'react'

import { api, ApiError, unwrap, type Category } from '../../api/client'
import { Button, Dialog, Field, Input, Select, useToast } from '../../components/ui'
import { syncNow } from '../../sync/engine'

/** Adresse reçue par le partage système (`share_target` du manifeste, route /partage). Android met
 * souvent l'adresse dans `text`, parfois précédée du titre de la page : on prend la première URL trouvée. */
export function sharedUrl(params: URLSearchParams): string {
  for (const key of ['url', 'text', 'title']) {
    const match = params.get(key)?.match(/https?:\/\/[^\s<>"]+/)
    if (match) return match[0].replace(/[).,;:!?»]+$/, '')
  }
  return ''
}

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
      toast(`Abonné à « ${feed.title} »`, 'success')
      reset()
      onClose()
      window.setTimeout(() => void syncNow(), 1500)
      window.setTimeout(() => void syncNow(), 8000)
    } catch (err) {
      if (err instanceof ApiError && err.status === 300) {
        const detail = (err.detail as { detail?: { choices?: Choice[] } })?.detail
        setChoices(detail?.choices ?? [])
      } else {
        setError(err instanceof Error ? err.message : 'Erreur inconnue')
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
      title="Ajouter un flux"
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Adresse du site ou du flux" hint="Un site, un flux RSS/Atom ou une chaîne YouTube.">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://exemple.fr" autoFocus type="text" inputMode="url" autoCapitalize="off" />
        </Field>
        <Field label="Catégorie">
          <Select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">Sans catégorie</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>

        {choices && (
          <div className="space-y-2">
            <p className="text-sm text-muted">Plusieurs flux trouvés, choisissez :</p>
            {choices.map((c) => (
              <button key={c.url} type="button" onClick={() => void subscribe(c.url)} className="w-full text-left rounded-lg border border-app px-3 py-2 hover:bg-muted">
                <span className="block text-sm font-medium">{c.title || 'Flux'}</span>
                <span className="block text-xs text-muted truncate">{c.url}</span>
              </button>
            ))}
          </div>
        )}

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button type="submit" variant="primary" loading={busy}>
            S’abonner
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
