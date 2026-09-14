import { useEffect, useState } from 'react'

import { api, unwrap, type Category, type Feed } from '../../api/client'
import { Button, Dialog, Field, Input, Select, useToast } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import { relativeDate } from '../../lib/format'

export function FeedDialog({ feed, categories, onClose }: { feed: Feed | null; categories: Category[]; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [mode, setMode] = useState<'none' | 'http' | 'browser' | 'auto'>('auto')
  const [disabled, setDisabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (!feed) return
    setTitle(feed.title)
    setUrl(feed.url)
    setCategoryId(feed.category_id ? String(feed.category_id) : '')
    setMode(feed.extraction_mode as typeof mode)
    setDisabled(feed.disabled)
    setConfirmDelete(false)
  }, [feed])

  if (!feed) return null

  async function save() {
    if (!feed) return
    setBusy(true)
    try {
      const updated = unwrap(
        await api().PUT('/api/feeds/{feed_id}', {
          params: { path: { feed_id: feed.id } },
          body: {
            title,
            url: url !== feed.url ? url : null,
            category_id: categoryId ? Number(categoryId) : null,
            clear_category: !categoryId,
            extraction_mode: mode,
            disabled,
          },
        }),
      )
      await db.feeds.put(updated)
      toast('Flux mis à jour', 'success')
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!feed) return
    setBusy(true)
    try {
      unwrap(await api().DELETE('/api/feeds/{feed_id}', { params: { path: { feed_id: feed.id } } }))
      await db.transaction('rw', db.feeds, db.entries, db.summaries, async () => {
        const ids = await db.entries.where('feed_id').equals(feed.id).primaryKeys()
        await db.summaries.where('entry_id').anyOf(ids).delete()
        await db.entries.bulkDelete(ids)
        await db.feeds.delete(feed.id)
      })
      toast('Désabonné', 'success')
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function refresh() {
    if (!feed) return
    try {
      unwrap(await api().POST('/api/feeds/{feed_id}/refresh', { params: { path: { feed_id: feed.id } } }))
      toast('Rafraîchissement lancé')
      window.setTimeout(() => void syncNow(), 4000)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    }
  }

  return (
    <Dialog
      open={Boolean(feed)}
      onClose={onClose}
      title="Modifier le flux"
      footer={
        <>
          {confirmDelete ? (
            <>
              <span className="text-sm text-muted self-center mr-auto">Supprimer ce flux et ses articles ?</span>
              <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                Non
              </Button>
              <Button variant="danger" onClick={() => void remove()} loading={busy}>
                Oui, supprimer
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" className="mr-auto text-red-600 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
                Se désabonner
              </Button>
              <Button variant="ghost" onClick={onClose}>
                Annuler
              </Button>
              <Button variant="primary" onClick={() => void save()} loading={busy}>
                Enregistrer
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Titre">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Adresse du flux">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} type="url" />
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
        <Field label="Récupération de l’article complet" hint="« À la demande » extrait quand vous ouvrez l’article (HTTP puis navigateur si besoin). Les modes « Toujours » extraient chaque nouvel article en arrière-plan, pour la lecture hors-ligne.">
          <Select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="auto">À la demande (automatique)</option>
            <option value="http">Toujours, en HTTP</option>
            <option value="browser">Toujours, via le navigateur (sites JavaScript)</option>
            <option value="none">Jamais (contenu du flux seulement)</option>
          </Select>
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={disabled} onChange={(e) => setDisabled(e.target.checked)} className="size-4 accent-accent-600" />
          Mettre en pause ce flux
        </label>

        <div className="rounded-lg bg-muted px-3 py-2 text-xs text-muted space-y-1">
          <div className="flex justify-between">
            <span>Dernière récupération</span>
            <span>{feed.last_fetched_at ? relativeDate(feed.last_fetched_at) : 'jamais'}</span>
          </div>
          <div className="flex justify-between">
            <span>Intervalle</span>
            <span>{feed.fetch_interval_min} min</span>
          </div>
          {feed.last_error && <div className="text-red-600 dark:text-red-400 break-words">Erreur : {feed.last_error}</div>}
          <button type="button" onClick={() => void refresh()} className="text-accent-600 dark:text-accent-300 underline">
            Rafraîchir maintenant
          </button>
        </div>
      </div>
    </Dialog>
  )
}

export function CategoryDialog({ category, open, onClose }: { category: Category | null; open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const toast = useToast()

  useEffect(() => {
    setTitle(category?.title ?? '')
    setConfirmDelete(false)
  }, [category, open])

  async function save() {
    setBusy(true)
    try {
      if (category) {
        const updated = unwrap(await api().PUT('/api/categories/{category_id}', { params: { path: { category_id: category.id } }, body: { title } }))
        await db.categories.put(updated)
      } else {
        const created = unwrap(await api().POST('/api/categories', { body: { title } }))
        await db.categories.put(created)
      }
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!category) return
    setBusy(true)
    try {
      unwrap(await api().DELETE('/api/categories/{category_id}', { params: { path: { category_id: category.id } } }))
      await db.transaction('rw', db.categories, db.feeds, async () => {
        await db.categories.delete(category.id)
        await db.feeds.where('category_id').equals(category.id).modify({ category_id: null })
      })
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={category ? 'Renommer la catégorie' : 'Nouvelle catégorie'}
      footer={
        confirmDelete ? (
          <>
            <span className="text-sm text-muted self-center mr-auto">Les flux passeront « sans catégorie ».</span>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Non
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={busy}>
              Supprimer
            </Button>
          </>
        ) : (
          <>
            {category && (
              <Button variant="ghost" className="mr-auto text-red-600 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
                Supprimer
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              Annuler
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={busy} disabled={!title.trim()}>
              Enregistrer
            </Button>
          </>
        )
      }
    >
      <Field label="Nom">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus onKeyDown={(e) => e.key === 'Enter' && void save()} />
      </Field>
    </Dialog>
  )
}
