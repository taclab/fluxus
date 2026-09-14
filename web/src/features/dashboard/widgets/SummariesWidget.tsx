import { useLiveQuery } from 'dexie-react-hooks'
import { Sparkles } from 'lucide-react'
import { useRef } from 'react'

import type { View } from '../../../app/view'
import { relativeDate } from '../../../lib/format'
import { useDashboardData } from '../DashboardData'
import { widgetTitle, type SummariesWidget as SummariesWidgetModel, type WidgetWidth } from '../layout'
import { latestSummaries, type SummaryWithEntry } from '../queries'
import { WidgetFrame, type WidgetActions } from '../WidgetFrame'
import { viewForSource } from './NewsWidget'

interface Props {
  widget: SummariesWidgetModel
  editing: boolean
  actions: WidgetActions
  onOpenEntry: (view: View, id: number) => void
  handleProps?: React.ComponentProps<typeof WidgetFrame>['handleProps']
  dragging?: boolean
}

/** Première phrase utile d'un résumé Markdown : on saute les titres et on retire le gras. */
function firstLines(md: string): string {
  const lines = md
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
  return lines
    .slice(0, 2)
    .join(' ')
    .replace(/\*\*/g, '')
    .replace(/^[-*]\s+/g, '')
}

export function SummariesWidget({ widget, editing, actions, onOpenEntry, handleProps, dragging }: Props) {
  const data = useDashboardData()
  const { title } = widgetTitle(widget, data.categoryMap, data.feedMap)
  const rows = useLiveQuery(() => latestSummaries(widget.limit), [widget.limit])
  const last = useRef<SummaryWithEntry[]>([])
  if (rows) last.current = rows
  const items = rows ?? last.current

  return (
    <WidgetFrame
      widgetId={widget.id}
      widgetType="summaries"
      title={title}
      editing={editing}
      width={widget.width as WidgetWidth}
      loading={rows === undefined && last.current.length === 0}
      empty={items.length === 0}
      emptyLabel="Aucun résumé pour l’instant. Appuyez sur r dans un article."
      actions={actions}
      handleProps={handleProps}
      dragging={dragging}
    >
      <ul className="divide-y divide-app">
        {items.map(({ summary, entry }) => {
          const feed = data.feedMap.get(entry.feed_id)
          const view: View = feed?.category_id != null ? viewForSource({ kind: 'category', id: feed.category_id }) : { kind: 'feed', id: entry.feed_id }
          return (
            <li key={summary.id}>
              <button type="button" data-entry-id={entry.id} onClick={() => onOpenEntry(view, entry.id)} className="flex w-full items-start gap-2.5 px-4 py-2.5 text-left hover:bg-muted">
                <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent-500" />
                <div className="min-w-0 flex-1">
                  <h3 className="line-clamp-1 text-sm font-medium text-app">{entry.title}</h3>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted">{firstLines(summary.text_md)}</p>
                  <p className="mt-1 text-[11px] text-muted">
                    {feed?.title} · {relativeDate(summary.created_at)}
                  </p>
                </div>
              </button>
            </li>
          )
        })}
      </ul>
    </WidgetFrame>
  )
}
