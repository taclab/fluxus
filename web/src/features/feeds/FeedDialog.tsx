import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, unwrap, type Category, type Feed } from '../../api/client'
import { useToast } from '../../components/feedback'
import { Button, Dialog, Field, Input, Select } from '../../components/ui'
import { db } from '../../db/db'
import { syncNow } from '../../sync/engine'
import { agoDate, untilDate } from '../../lib/format'
import { forgetPendingViewModes, useDefaultViewMode } from '../entries/useViewMode'
import { VIEW_MODE_SPECS, VIEW_MODES, isViewMode, type ViewMode } from '../entries/viewMode'
import { EXTRACTION_OPTIONS, deleteFeeds, extractionHint, intervalHint, intervalLabel, intervalOptions, updateFeed, type ExtractionMode } from './feedSettings'
import { useT } from '../../i18n'

/** Sélecteur de présentation avec l'option « hériter » (libellé de ce qui serait hérité). */
export function ViewModeSelect({ value, inheritLabel, onChange, ...rest }: { value: ViewMode | ''; inheritLabel: string; onChange: (v: ViewMode | '') => void; 'aria-label'?: string; className?: string; disabled?: boolean }) {
  return (
    <Select value={value} onChange={(e) => onChange(isViewMode(e.target.value) ? e.target.value : '')} {...rest}>
      <option value="">{inheritLabel}</option>
      {VIEW_MODES.map((m) => (
        <option key={m} value={m}>
          {VIEW_MODE_SPECS[m].label}
        </option>
      ))}
    </Select>
  )
}

export function FeedDialog({ feed, categories, onClose }: { feed: Feed | null; categories: Category[]; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [mode, setMode] = useState<ExtractionMode>('auto')
  const [disabled, setDisabled] = useState(false)
  const [viewMode, setViewModeValue] = useState<ViewMode | ''>('')
  const [interval, setIntervalMin] = useState(0)
  const [autoSummary, setAutoSummary] = useState(false)
  const [cap, setCap] = useState('10')
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const toast = useToast()
  const defaultMode = useDefaultViewMode()
  const tr = useT()
  const t = tr.feeds.edit

  useEffect(() => {
    if (!feed) return
    setTitle(feed.title)
    setUrl(feed.url)
    setCategoryId(feed.category_id ? String(feed.category_id) : '')
    setMode(feed.extraction_mode as ExtractionMode)
    setDisabled(feed.disabled)
    setViewModeValue(feed.view_mode ?? '')
    setIntervalMin(feed.fetch_interval_fixed_min ?? 0)
    setAutoSummary(feed.auto_summary)
    setCap(String(feed.summary_daily_cap))
    setConfirmDelete(false)
  }, [feed])

  // Ce dont le flux hériterait : le dossier choisi dans le formulaire, sinon le défaut.
  const folder = categories.find((c) => String(c.id) === categoryId)
  const inherited = folder?.view_mode ? t.inheritedFolder(VIEW_MODE_SPECS[folder.view_mode].label, folder.title) : t.inheritedDefault(VIEW_MODE_SPECS[defaultMode].label)

  if (!feed) return null

  async function save() {
    if (!feed) return
    const capValue = Number(cap)
    if (autoSummary && !(Number.isInteger(capValue) && capValue >= 1 && capValue <= 200)) {
      toast(t.capRange, 'error')
      return
    }
    setBusy(true)
    try {
      await updateFeed(feed.id, {
        title,
        url: url !== feed.url ? url : null,
        category_id: categoryId ? Number(categoryId) : null,
        clear_category: !categoryId,
        extraction_mode: mode,
        disabled,
        view_mode: viewMode || 'inherit',
        fetch_interval_fixed_min: interval,
        auto_summary: autoSummary,
        summary_daily_cap: autoSummary ? capValue : null,
      })
      toast(t.updated, 'success')
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : tr.common.error, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!feed) return
    setBusy(true)
    try {
      await deleteFeeds([feed.id])
      toast(t.unsubscribed, 'success')
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : tr.common.error, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function refresh() {
    if (!feed) return
    try {
      unwrap(await api().POST('/api/feeds/{feed_id}/refresh', { params: { path: { feed_id: feed.id } } }))
      toast(t.refreshStarted)
      window.setTimeout(() => void syncNow(), 4000)
    } catch (err) {
      toast(err instanceof Error ? err.message : tr.common.error, 'error')
    }
  }

  return (
    <Dialog
      open={Boolean(feed)}
      onClose={onClose}
      title={t.title}
      footer={
        <>
          {confirmDelete ? (
            <>
              <span className="text-sm text-muted self-center mr-auto">{t.confirmDelete}</span>
              <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                {tr.common.no}
              </Button>
              <Button variant="danger" onClick={() => void remove()} loading={busy}>
                {t.yesDelete}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" className="mr-auto text-red-600 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
                {t.unsubscribe}
              </Button>
              <Button variant="ghost" onClick={onClose}>
                {tr.common.cancel}
              </Button>
              <Button variant="primary" onClick={() => void save()} loading={busy}>
                {tr.common.save}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <Field label={t.titleLabel}>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label={t.urlLabel}>
          <Input value={url} onChange={(e) => setUrl(e.target.value)} type="url" />
        </Field>
        <Field label={tr.feeds.add.category}>
          <Select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">{tr.feeds.add.noCategory}</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.viewModeLabel} hint={t.viewModeHint}>
          <ViewModeSelect value={viewMode} inheritLabel={t.inherit(inherited)} onChange={setViewModeValue} />
        </Field>
        <Field label={t.extractionLabel} hint={extractionHint()}>
          <Select value={mode} onChange={(e) => setMode(e.target.value as ExtractionMode)}>
            {EXTRACTION_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.intervalLabel} hint={intervalHint()}>
          <Select value={interval} onChange={(e) => setIntervalMin(Number(e.target.value))}>
            {intervalOptions(feed).map((o) => (
              <option key={o.minutes} value={o.minutes}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={autoSummary} onChange={(e) => setAutoSummary(e.target.checked)} className="size-4 accent-accent-600" data-testid="feed-auto-summary" />
            {t.autoSummary}
          </label>
          {autoSummary && (
            <div className="flex items-center gap-2 pl-6 text-sm text-muted">
              <span>{t.atMost}</span>
              <Input type="number" min={1} max={200} value={cap} onChange={(e) => setCap(e.target.value)} className="h-8 w-20" aria-label={t.perDayLabel} />
              <span>{t.perDay}</span>
            </div>
          )}
          <p className="pl-6 text-xs text-muted">{t.budgetHint}</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={disabled} onChange={(e) => setDisabled(e.target.checked)} className="size-4 accent-accent-600" />
          {t.pause}
        </label>

        <div className="rounded-lg bg-muted px-3 py-2 text-xs text-muted space-y-1">
          <div className="flex justify-between">
            <span>{t.lastFetch}</span>
            <span>{feed.last_fetched_at ? agoDate(feed.last_fetched_at) : tr.common.never}</span>
          </div>
          <div className="flex justify-between">
            <span>{t.nextFetch}</span>
            <span>{feed.disabled ? t.paused : untilDate(feed.next_fetch_at)}</span>
          </div>
          <div className="flex justify-between">
            <span>{t.currentInterval}</span>
            <span>{intervalLabel(feed)}</span>
          </div>
          {feed.last_error && <div className="text-red-600 dark:text-red-400 break-words">{t.error(feed.last_error)}</div>}
          <div className="flex justify-between gap-2">
            <button type="button" onClick={() => void refresh()} className="text-accent-600 dark:text-accent-300 underline">
              {t.refreshNow}
            </button>
            <Link to="/abonnements" className="text-accent-600 dark:text-accent-300 underline" onClick={onClose}>
              {t.manageAll}
            </Link>
          </div>
        </div>
      </div>
    </Dialog>
  )
}

export function CategoryDialog({ category, open, onClose }: { category: Category | null; open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [viewMode, setViewModeValue] = useState<ViewMode | ''>('')
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const toast = useToast()
  const defaultMode = useDefaultViewMode()
  const tr = useT()
  const t = tr.feeds.category

  useEffect(() => {
    setTitle(category?.title ?? '')
    setViewModeValue(category?.view_mode ?? '')
    setConfirmDelete(false)
  }, [category, open])

  async function save() {
    setBusy(true)
    try {
      if (category) {
        const updated = unwrap(await api().PUT('/api/categories/{category_id}', { params: { path: { category_id: category.id } }, body: { title, view_mode: viewMode || 'inherit' } }))
        await forgetPendingViewModes([{ kind: 'category', id: category.id }])
        await db.categories.put(updated)
      } else {
        const created = unwrap(await api().POST('/api/categories', { body: { title, view_mode: viewMode || null } }))
        await db.categories.put(created)
      }
      onClose()
    } catch (err) {
      toast(err instanceof Error ? err.message : tr.common.error, 'error')
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
      toast(err instanceof Error ? err.message : tr.common.error, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={category ? t.editTitle : t.newTitle}
      footer={
        confirmDelete ? (
          <>
            <span className="text-sm text-muted self-center mr-auto">{t.deleteWarning}</span>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              {tr.common.no}
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={busy}>
              {tr.common.delete}
            </Button>
          </>
        ) : (
          <>
            {category && (
              <Button variant="ghost" className="mr-auto text-red-600 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
                {tr.common.delete}
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              {tr.common.cancel}
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={busy} disabled={!title.trim()}>
              {tr.common.save}
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <Field label={t.name}>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus onKeyDown={(e) => e.key === 'Enter' && void save()} />
        </Field>
        <Field label={tr.feeds.edit.viewModeLabel} hint={t.viewModeHint}>
          <ViewModeSelect value={viewMode} inheritLabel={t.defaultMode(VIEW_MODE_SPECS[defaultMode].label)} onChange={setViewModeValue} />
        </Field>
      </div>
    </Dialog>
  )
}
