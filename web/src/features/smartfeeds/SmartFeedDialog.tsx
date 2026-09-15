import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'

import { api, ApiError, isOnline, unwrap, type SmartFeed, type SmartFeedDefinitionIn, type SmartFeedIn } from '../../api/client'
import { Button, Dialog, Field, Input, Select, useToast } from '../../components/ui'
import { db } from '../../db/db'
import { relativeDate } from '../../lib/format'
import { syncNow } from '../../sync/engine'

interface Props {
  open: boolean
  /** Flux intelligent à modifier ; null pour une création. */
  smartFeed: SmartFeed | null
  /** Portée préremplie à la création (« créer depuis cet article »). */
  preset?: { scope_kind: 'feed' | 'category'; scope_id: number } | null
  onClose: () => void
  onSaved?: (sf: SmartFeed) => void
}

type Fields = { title: boolean; excerpt: boolean; author: boolean; url: boolean; content: boolean }

/** Brouillon strict (les types générés rendent les champs optionnels à cause des valeurs par défaut). */
interface Draft {
  name: string
  position: number | null
  scope_kind: 'all' | 'category' | 'feed'
  scope_id: number | null
  mode: 'keywords' | 'regex'
  keywords_any: string[]
  keywords_all: string[]
  keywords_none: string[]
  regex: string | null
  fields: Fields
  auto_summary: boolean
  summary_daily_cap: number
}

function blank(preset?: Props['preset']): Draft {
  return {
    name: '',
    position: null,
    scope_kind: preset?.scope_kind ?? 'all',
    scope_id: preset?.scope_id ?? null,
    mode: 'keywords',
    keywords_any: [],
    keywords_all: [],
    keywords_none: [],
    regex: null,
    fields: { title: true, excerpt: true, author: true, url: true, content: false },
    auto_summary: false,
    summary_daily_cap: 10,
  }
}

function fromSmartFeed(sf: SmartFeed): Draft {
  return {
    name: sf.name,
    position: sf.position,
    scope_kind: sf.scope_kind as Draft['scope_kind'],
    scope_id: sf.scope_id,
    mode: sf.mode as Draft['mode'],
    keywords_any: sf.keywords_any,
    keywords_all: sf.keywords_all,
    keywords_none: sf.keywords_none,
    regex: sf.regex,
    fields: { title: true, excerpt: true, author: true, url: true, content: false, ...sf.fields },
    auto_summary: sf.auto_summary,
    summary_daily_cap: sf.summary_daily_cap,
  }
}

function definitionOf(d: Draft): SmartFeedDefinitionIn {
  return {
    scope_kind: d.scope_kind,
    scope_id: d.scope_id,
    mode: d.mode,
    keywords_any: d.keywords_any,
    keywords_all: d.keywords_all,
    keywords_none: d.keywords_none,
    regex: d.regex,
    fields: d.fields,
  }
}

export function SmartFeedDialog({ open, smartFeed, preset, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState<Draft>(() => (smartFeed ? fromSmartFeed(smartFeed) : blank(preset)))
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [preview, setPreview] = useState<{ state: 'idle' | 'loading' | 'ok' | 'error' | 'offline'; count?: number; samples?: { id: number; title: string; feed_id: number; published_at: string }[]; message?: string }>({ state: 'idle' })
  const toast = useToast()
  const categories = useLiveQuery(() => db.categories.orderBy('position').toArray(), []) ?? []
  const feeds = useLiveQuery(() => db.feeds.toArray(), []) ?? []
  const feedMap = useMemo(() => new Map(feeds.map((f) => [f.id, f])), [feeds])
  const feedsSorted = useMemo(() => [...feeds].sort((a, b) => a.title.localeCompare(b.title, 'fr')), [feeds])

  useEffect(() => {
    if (open) {
      setDraft(smartFeed ? fromSmartFeed(smartFeed) : blank(preset))
      setConfirmDelete(false)
      setPreview({ state: 'idle' })
    }
  }, [open, smartFeed, preset])

  // Aperçu en direct, debounce 500 ms, sur le serveur.
  const definitionKey = JSON.stringify(definitionOf(draft))
  useEffect(() => {
    if (!open) return
    const d = definitionOf(draft)
    const hasFilter = draft.mode === 'regex' ? Boolean(draft.regex?.trim()) : draft.keywords_any.length + draft.keywords_all.length > 0
    if (!hasFilter) {
      setPreview({ state: 'idle' })
      return
    }
    if (!isOnline()) {
      setPreview({ state: 'offline' })
      return
    }
    let cancelled = false
    setPreview((p) => ({ ...p, state: 'loading' }))
    const timer = window.setTimeout(async () => {
      try {
        const res = unwrap(await api().POST('/api/smart-feeds/preview', { body: d }))
        if (!cancelled) setPreview({ state: 'ok', count: res.count, samples: res.samples })
      } catch (err) {
        if (!cancelled) setPreview({ state: 'error', message: err instanceof Error ? err.message : 'Erreur' })
      }
    }, 500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [definitionKey, open]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!draft.name.trim()) {
      toast('Donnez un nom au flux intelligent', 'error')
      return
    }
    setBusy(true)
    try {
      const body: SmartFeedIn = { ...draft, name: draft.name.trim(), scope_id: draft.scope_kind === 'all' ? null : draft.scope_id }
      const saved = smartFeed
        ? unwrap(await api().PUT('/api/smart-feeds/{smart_feed_id}', { params: { path: { smart_feed_id: smartFeed.id } }, body }))
        : unwrap(await api().POST('/api/smart-feeds', { body }))
      await db.smartFeeds.put(saved)
      toast(smartFeed ? 'Flux intelligent mis à jour' : `Flux intelligent « ${saved.name} » créé`, 'success')
      onSaved?.(saved)
      onClose()
      void syncNow()
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Enregistrement impossible', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!smartFeed) return
    setBusy(true)
    try {
      unwrap(await api().DELETE('/api/smart-feeds/{smart_feed_id}', { params: { path: { smart_feed_id: smartFeed.id } } }))
      await db.smartFeeds.delete(smartFeed.id)
      toast('Flux intelligent supprimé', 'success')
      onClose()
      void syncNow()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Erreur', 'error')
    } finally {
      setBusy(false)
    }
  }

  const scopeValue = draft.scope_kind === 'all' ? 'all' : `${draft.scope_kind}:${draft.scope_id ?? ''}`
  const scopeOrphan = (draft.scope_kind === 'category' && draft.scope_id != null && !categories.some((c) => c.id === draft.scope_id)) || (draft.scope_kind === 'feed' && draft.scope_id != null && !feedMap.has(draft.scope_id))

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={smartFeed ? 'Modifier le flux intelligent' : 'Nouveau flux intelligent'}
      width="max-w-2xl"
      footer={
        confirmDelete ? (
          <>
            <span className="mr-auto self-center text-sm text-muted">Supprimer ce flux intelligent ? Les articles ne sont pas touchés.</span>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Non
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={busy}>
              Supprimer
            </Button>
          </>
        ) : (
          <>
            {smartFeed && (
              <Button variant="ghost" className="mr-auto text-red-600 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
                Supprimer
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              Annuler
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={busy} data-testid="smartfeed-submit">
              {smartFeed ? 'Enregistrer' : 'Créer'}
            </Button>
          </>
        )
      }
    >
      <div className="grid gap-5 md:grid-cols-[1fr_16rem]">
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Nom">
              <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Intelligence artificielle" autoFocus data-testid="smartfeed-name" />
            </Field>
            <Field label="Portée">
              <Select
                value={scopeValue}
                data-testid="smartfeed-scope"
                onChange={(e) => {
                  const v = e.target.value
                  if (v === 'all') setDraft({ ...draft, scope_kind: 'all', scope_id: null })
                  else {
                    const [kind, id] = v.split(':')
                    setDraft({ ...draft, scope_kind: kind as 'category' | 'feed', scope_id: Number(id) })
                  }
                }}
              >
                <option value="all">Tous les flux</option>
                {scopeOrphan && <option value={scopeValue}>Portée supprimée</option>}
                {categories.length > 0 && (
                  <optgroup label="Catégories">
                    {categories.map((c) => (
                      <option key={c.id} value={`category:${c.id}`}>
                        {c.title}
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="Flux">
                  {feedsSorted.map((f) => (
                    <option key={f.id} value={`feed:${f.id}`}>
                      {f.title || f.url}
                    </option>
                  ))}
                </optgroup>
              </Select>
            </Field>
          </div>

          <div className="flex rounded-lg border border-app bg-muted p-0.5 text-sm" role="radiogroup" aria-label="Mode">
            {(['keywords', 'regex'] as const).map((m) => (
              <button key={m} type="button" role="radio" aria-checked={draft.mode === m} onClick={() => setDraft({ ...draft, mode: m })} className={clsx('flex-1 rounded-md px-3 py-1.5 font-medium', draft.mode === m ? 'bg-panel text-app shadow-sm' : 'text-muted hover:text-app')}>
                {m === 'keywords' ? 'Mots-clés' : 'Expression régulière (expert)'}
              </button>
            ))}
          </div>

          {draft.mode === 'keywords' ? (
            <div className="space-y-3">
              <KeywordsInput label="Au moins un de" hint="Entrée ou virgule pour ajouter. Sans casse ni accents." values={draft.keywords_any} onChange={(v) => setDraft({ ...draft, keywords_any: v })} testId="kw-any" />
              <KeywordsInput label="Tous ces mots" values={draft.keywords_all} onChange={(v) => setDraft({ ...draft, keywords_all: v })} testId="kw-all" />
              <KeywordsInput label="Aucun de" values={draft.keywords_none} onChange={(v) => setDraft({ ...draft, keywords_none: v })} testId="kw-none" />
            </div>
          ) : (
            <Field label="Expression régulière" hint="Moteur PostgreSQL, insensible à la casse, appliquée au texte sans accents (écrivez « eleve » pour « élève »). Pas de références arrière ; quantificateurs ≤ 255. Ex. : \\m(gpu|cuda)\\M">
              <Input value={draft.regex ?? ''} onChange={(e) => setDraft({ ...draft, regex: e.target.value })} placeholder="\\m(gpu|cuda)\\M" className="font-mono" data-testid="smartfeed-regex" />
            </Field>
          )}

          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium text-app">Champs examinés</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
              {(
                [
                  ['title', 'Titre'],
                  ['excerpt', 'Extrait du flux'],
                  ['author', 'Auteur'],
                  ['url', 'Adresse'],
                  ['content', 'Contenu complet'],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={draft.fields[key]} onChange={(e) => setDraft({ ...draft, fields: { ...draft.fields, [key]: e.target.checked } })} className="size-4 accent-accent-600" />
                  {label}
                </label>
              ))}
            </div>
            {draft.fields.content && <p className="text-xs text-muted">Le contenu complet n’existe que pour les articles déjà extraits (mode « Toujours » du flux, ou ouverture dans le lecteur).</p>}
          </fieldset>

          <div className="rounded-xl border border-app p-3 space-y-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={draft.auto_summary} onChange={(e) => setDraft({ ...draft, auto_summary: e.target.checked })} className="size-4 accent-accent-600" data-testid="smartfeed-auto" />
              <Sparkles className="size-4 text-accent-500" /> Résumer automatiquement les nouveaux articles
            </label>
            {draft.auto_summary && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <span>Au plus</span>
                <Input type="number" min={1} max={200} value={draft.summary_daily_cap} onChange={(e) => setDraft({ ...draft, summary_daily_cap: Math.max(1, Math.min(200, Number(e.target.value) || 1)) })} className="w-20" />
                <span>résumés par jour pour ce flux, dans la limite du budget global (Paramètres → Résumés). Modèle économique dédié.</span>
              </div>
            )}
          </div>
        </div>

        <aside className="rounded-xl bg-muted/60 p-3 text-sm" data-testid="smartfeed-preview">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Aperçu</p>
          {preview.state === 'idle' && <p className="text-muted">Ajoutez un mot-clé pour voir les articles correspondants.</p>}
          {preview.state === 'offline' && <p className="text-muted">Aperçu indisponible hors-ligne.</p>}
          {preview.state === 'loading' && <p className="animate-soft-pulse text-muted">Recherche…</p>}
          {preview.state === 'error' && <p className="text-red-600 dark:text-red-400">{preview.message}</p>}
          {preview.state === 'ok' && (
            <>
              <p className="text-app">
                <span className="text-2xl font-semibold tabular-nums">{preview.count}</span> <span className="text-muted">article{preview.count === 1 ? '' : 's'} sur la période conservée</span>
              </p>
              <ul className="mt-2 space-y-1.5">
                {preview.samples?.map((s) => (
                  <li key={s.id} className="leading-snug">
                    <span className="line-clamp-2 text-app">{s.title}</span>
                    <span className="text-[11px] text-muted">
                      {feedMap.get(s.feed_id)?.title ?? 'Flux'} · {relativeDate(s.published_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </Dialog>
  )
}

/** Saisie de mots-clés en puces : Entrée ou virgule ajoute, Retour arrière sur champ vide retire le dernier. */
function KeywordsInput({ label, hint, values, onChange, testId }: { label: string; hint?: string; values: string[]; onChange: (v: string[]) => void; testId: string }) {
  const [text, setText] = useState('')
  const commit = () => {
    const parts = text
      .split(',')
      .map((p) => p.trim())
      .filter(Boolean)
    if (parts.length) onChange([...values, ...parts.filter((p) => !values.includes(p))])
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commit()
    } else if (e.key === 'Backspace' && !text && values.length) {
      onChange(values.slice(0, -1))
    }
  }
  return (
    <div className="space-y-1.5">
      <span className="block text-sm font-medium text-app">{label}</span>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-app bg-panel px-2 py-1.5 focus-within:ring-2 focus-within:ring-accent-400">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-md bg-accent-100 px-2 py-0.5 text-xs font-medium text-accent-800 dark:bg-accent-900/40 dark:text-accent-100">
            {v}
            <button type="button" aria-label={`Retirer ${v}`} onClick={() => onChange(values.filter((x) => x !== v))} className="rounded hover:bg-accent-200/60 dark:hover:bg-accent-800/60">
              <X className="size-3" />
            </button>
          </span>
        ))}
        <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onBlur={commit} placeholder={values.length ? '' : 'mot ou expression'} className="min-w-[8rem] flex-1 bg-transparent text-sm text-app placeholder:text-muted focus:outline-none" data-testid={testId} />
      </div>
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </div>
  )
}
