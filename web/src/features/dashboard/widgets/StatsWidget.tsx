import { useLiveQuery } from 'dexie-react-hooks'
import { AlertTriangle, BookOpenCheck, Inbox, Sparkles, Star } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import type { StatsOut } from '../../../api/client'
import type { View } from '../../../app/view'
import { db } from '../../../db/db'
import { formatTokens } from '../../../lib/format'
import { useDashboardData } from '../DashboardData'
import { widgetTitle, type StatsWidget as StatsWidgetModel, type WidgetWidth } from '../layout'
import { feedsInError, fetchStatsCached, readTodayCount } from '../queries'
import { WidgetFrame, type WidgetActions } from '../WidgetFrame'

interface Props {
  widget: StatsWidgetModel
  editing: boolean
  actions: WidgetActions
  onOpenView: (view: View) => void
  onEditFeed?: (feedId: number) => void
  handleProps?: React.ComponentProps<typeof WidgetFrame>['handleProps']
  dragging?: boolean
}

export function StatsWidget({ widget, editing, actions, onOpenView, handleProps, dragging }: Props) {
  const data = useDashboardData()
  const { title } = widgetTitle(widget, data.categoryMap, data.feedMap)
  const readToday = useLiveQuery(() => readTodayCount(), []) ?? 0
  const starred = useLiveQuery(() => db.entries.where('starredFlag').equals(1).count(), []) ?? 0
  const [server, setServer] = useState<StatsOut | null>(null)

  useEffect(() => {
    if (!widget.show.tokens) return
    let cancelled = false
    void fetchStatsCached().then((s) => !cancelled && setServer(s))
    return () => {
      cancelled = true
    }
  }, [widget.show.tokens])

  const totalUnread = useMemo(() => [...data.unreadByFeed.values()].reduce((a, b) => a + b, 0), [data.unreadByFeed])
  const byCategory = useMemo(() => {
    const map = new Map<number | null, number>()
    for (const f of data.feeds) map.set(f.category_id ?? null, (map.get(f.category_id ?? null) ?? 0) + (data.unreadByFeed.get(f.id) ?? 0))
    const rows = data.categories.map((c) => ({ id: c.id as number | null, title: c.title, count: map.get(c.id) ?? 0 }))
    const uncategorized = map.get(null) ?? 0
    if (uncategorized > 0) rows.push({ id: null, title: 'Sans catégorie', count: uncategorized })
    return rows.filter((r) => r.count > 0).sort((a, b) => b.count - a.count)
  }, [data.feeds, data.categories, data.unreadByFeed])
  const errors = useMemo(() => feedsInError(data.feeds), [data.feeds])
  const maxCount = byCategory[0]?.count ?? 1

  return (
    <WidgetFrame widgetId={widget.id} widgetType="stats" title={title} editing={editing} width={widget.width as WidgetWidth} actions={actions} handleProps={handleProps} dragging={dragging}>
      <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={<Inbox className="size-4" />} label="Non lus" value={totalUnread} onClick={() => onOpenView({ kind: 'unread' })} />
        {widget.show.readToday && <Tile icon={<BookOpenCheck className="size-4" />} label="Lus aujourd’hui" value={readToday} />}
        <Tile icon={<Star className="size-4" />} label="Favoris" value={starred} onClick={() => onOpenView({ kind: 'starred' })} />
        {widget.show.tokens && server && (
          <Tile icon={<Sparkles className="size-4" />} label="Résumés" value={server.summaries} hint={`${formatTokens(server.summary_input_tokens + server.summary_output_tokens)} tokens`} />
        )}
      </div>

      {(widget.show.unreadByCategory && byCategory.length > 0) || (widget.show.feedErrors && errors.length > 0) ? (
        <div className="grid gap-4 px-4 pb-4 lg:grid-cols-[1fr_auto]">
          {widget.show.unreadByCategory && byCategory.length > 0 && (
            <ul className="space-y-1.5">
              {byCategory.slice(0, 8).map((row) => (
                <li key={row.id ?? 'none'}>
                  <button
                    type="button"
                    onClick={() => (row.id != null ? onOpenView({ kind: 'category', id: row.id }) : onOpenView({ kind: 'all' }))}
                    className="group flex w-full items-center gap-3 text-left"
                  >
                    <span className="w-36 truncate text-xs text-app group-hover:underline">{row.title}</span>
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-accent-500" style={{ width: `${Math.max(4, (row.count / maxCount) * 100)}%` }} />
                    </span>
                    <span className="w-10 text-right text-xs tabular-nums text-muted">{row.count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {widget.show.feedErrors && errors.length > 0 && (
            <div className="rounded-xl border border-amber-300/60 bg-amber-50/60 px-3 py-2 text-xs text-amber-800 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-200 lg:w-64">
              <p className="flex items-center gap-1.5 font-medium">
                <AlertTriangle className="size-3.5" /> {errors.length} flux en erreur
              </p>
              <ul className="mt-1 space-y-0.5">
                {errors.slice(0, 5).map((f) => (
                  <li key={f.id}>
                    <button type="button" onClick={() => onOpenView({ kind: 'feed', id: f.id })} className="truncate text-left hover:underline max-w-full block" title={f.last_error ?? ''}>
                      {f.title}
                    </button>
                  </li>
                ))}
                {errors.length > 5 && <li className="text-amber-700/80 dark:text-amber-300/80">et {errors.length - 5} autres…</li>}
              </ul>
            </div>
          )}
        </div>
      ) : null}
    </WidgetFrame>
  )
}

function Tile({ icon, label, value, hint, onClick }: { icon: ReactNode; label: string; value: number; hint?: string; onClick?: () => void }) {
  const inner = (
    <>
      <span className="flex items-center gap-1.5 text-xs text-muted">
        {icon} {label}
      </span>
      <span className="mt-1 block text-2xl font-semibold tabular-nums text-app">{value}</span>
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </>
  )
  const cls = 'rounded-xl bg-muted/70 px-3 py-2.5 text-left'
  return onClick ? (
    <button type="button" onClick={onClick} className={`${cls} hover:bg-muted transition-colors`}>
      {inner}
    </button>
  ) : (
    <div className={cls}>{inner}</div>
  )
}
