import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { useRef } from 'react'

import type { View } from '../../../app/view'
import type { LocalEntry } from '../../../db/db'
import { relativeDate } from '../../../lib/format'
import { recordBulkRead } from '../../../sync/engine'
import { useDashboardData } from '../DashboardData'
import { widgetTitle, type WidgetWidth, type YoutubeWidget as YoutubeWidgetModel } from '../layout'
import { latestForFeeds, youtubeFeedIds } from '../queries'
import { WidgetFrame, type WidgetActions } from '../WidgetFrame'

interface Props {
  widget: YoutubeWidgetModel
  editing: boolean
  actions: WidgetActions
  onOpenEntry: (view: View, id: number) => void
  onOpenView: (view: View) => void
  handleProps?: React.ComponentProps<typeof WidgetFrame>['handleProps']
  dragging?: boolean
}

export function YoutubeWidget({ widget, editing, actions, onOpenEntry, onOpenView, handleProps, dragging }: Props) {
  const data = useDashboardData()
  const { title, orphan } = widgetTitle(widget, data.categoryMap, data.feedMap)
  const categoryId = widget.source.kind === 'category' ? widget.source.id : null
  const feedIds = youtubeFeedIds(data.feeds, categoryId)
  const feedKey = feedIds.join(',')

  const entries = useLiveQuery(() => latestForFeeds(feedIds, { unreadOnly: widget.unreadOnly, limit: widget.limit }), [feedKey, widget.unreadOnly, widget.limit])
  const last = useRef<LocalEntry[]>([])
  if (entries) last.current = entries
  const items = (entries ?? last.current).filter((e) => e.youtube_id)

  // Vue de contexte : la catégorie si ciblée, sinon le flux de la vidéo cliquée.
  const viewFor = (e: LocalEntry): View => (categoryId != null ? { kind: 'category', id: categoryId } : { kind: 'feed', id: e.feed_id })
  const openView: View | null = categoryId != null ? { kind: 'category', id: categoryId } : feedIds.length === 1 ? { kind: 'feed', id: feedIds[0] } : null

  return (
    <WidgetFrame
      widgetId={widget.id}
      widgetType="youtube"
      title={title}
      count={feedIds.reduce((a, id) => a + (data.unreadByFeed.get(id) ?? 0), 0)}
      editing={editing}
      width={widget.width as WidgetWidth}
      orphan={orphan}
      loading={entries === undefined && last.current.length === 0 && !data.ready}
      empty={data.ready && items.length === 0}
      emptyLabel={feedIds.length === 0 ? 'Aucun flux YouTube' : 'Aucune vidéo non vue'}
      onOpen={openView ? () => onOpenView(openView) : undefined}
      onMarkAllRead={() => {
        const ids = items.filter((e) => !e.read).map((e) => e.id)
        if (ids.length) void recordBulkRead(ids, true)
      }}
      actions={actions}
      handleProps={handleProps}
      dragging={dragging}
    >
      <ul className={clsx('grid gap-3 p-3', widget.width === 6 ? 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4' : widget.width === 3 ? 'grid-cols-2 lg:grid-cols-3' : 'grid-cols-2')}>
        {items.map((e) => (
          <li key={e.id}>
            <button type="button" data-entry-id={e.id} onClick={() => onOpenEntry(viewFor(e), e.id)} className="group block w-full text-left">
              <div className="relative aspect-video overflow-hidden rounded-lg bg-muted">
                <img src={`https://i.ytimg.com/vi/${e.youtube_id}/mqdefault.jpg`} alt="" loading="lazy" className="size-full object-cover transition-transform group-hover:scale-[1.03]" />
                {!e.read && <span className="absolute left-1.5 top-1.5 size-2 rounded-full bg-accent-500 ring-2 ring-white/80" />}
              </div>
              <h3 className={clsx('mt-1.5 line-clamp-2 text-xs leading-snug', e.read ? 'text-muted' : 'font-medium text-app')}>{e.title}</h3>
              <p className="mt-0.5 truncate text-[11px] text-muted">
                {data.feedMap.get(e.feed_id)?.title} · {relativeDate(e.published_at)}
              </p>
            </button>
          </li>
        ))}
      </ul>
    </WidgetFrame>
  )
}
