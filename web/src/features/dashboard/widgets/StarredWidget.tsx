import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Sparkles } from 'lucide-react'

import type { View } from '../../../app/view'
import { hostname, relativeDate } from '../../../lib/format'
import { FeedIcon } from '../../feeds/FeedIcon'
import { useDashboardData } from '../dashboardContext'
import { widgetTitle, type StarredWidget as StarredWidgetModel, type WidgetWidth } from '../layout'
import { latestStarred } from '../queries'
import { WidgetFrame, type WidgetActions } from '../WidgetFrame'

interface Props {
  widget: StarredWidgetModel
  editing: boolean
  actions: WidgetActions
  onOpenEntry: (view: View, id: number) => void
  onOpenView: (view: View) => void
  handleProps?: React.ComponentProps<typeof WidgetFrame>['handleProps']
  dragging?: boolean
}

export function StarredWidget({ widget, editing, actions, onOpenEntry, onOpenView, handleProps, dragging }: Props) {
  const data = useDashboardData()
  const { title } = widgetTitle(widget, data.categoryMap, data.feedMap)
  const entries = useLiveQuery(() => latestStarred(widget.limit), [widget.limit])
  const items = entries ?? []

  return (
    <WidgetFrame
      widgetId={widget.id}
      widgetType="starred"
      title={title}
      editing={editing}
      width={widget.width as WidgetWidth}
      loading={entries === undefined}
      empty={items.length === 0}
      emptyLabel="Aucun favori. Appuyez sur s dans un article pour l’ajouter."
      onOpen={() => onOpenView({ kind: 'starred' })}
      actions={actions}
      handleProps={handleProps}
      dragging={dragging}
    >
      <ul className="divide-y divide-app">
        {items.map((e) => {
          const feed = data.feedMap.get(e.feed_id)
          return (
            <li key={e.id}>
              <button type="button" data-entry-id={e.id} onClick={() => onOpenEntry({ kind: 'starred' }, e.id)} className="flex w-full items-start gap-2.5 px-4 py-2 text-left hover:bg-muted">
                <div className="min-w-0 flex-1">
                  <h3 className={clsx('line-clamp-2 text-sm leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
                    {feed && <FeedIcon feed={feed} className="size-3" />}
                    <span className="truncate">{feed?.title || hostname(e.url)}</span>
                    <span className="ml-auto shrink-0 tabular-nums">{relativeDate(e.published_at)}</span>
                    {e.has_summary && <Sparkles className="size-3 shrink-0 text-accent-500" />}
                  </div>
                </div>
              </button>
            </li>
          )
        })}
      </ul>
    </WidgetFrame>
  )
}
