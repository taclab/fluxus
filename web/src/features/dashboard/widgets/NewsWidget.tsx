import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { Sparkles, Star } from 'lucide-react'
import { useRef } from 'react'

import type { View } from '../../../app/view'
import type { LocalEntry } from '../../../db/db'
import { hostname, relativeDate } from '../../../lib/format'
import { recordBulkRead } from '../../../sync/engine'
import { FeedIcon } from '../../feeds/Sidebar'
import { useDashboardData } from '../DashboardData'
import { widgetTitle, type NewsWidget as NewsWidgetModel, type WidgetWidth } from '../layout'
import { categoryFeedIds, latestAll, latestForFeeds } from '../queries'
import { WidgetFrame, type WidgetActions } from '../WidgetFrame'

interface Props {
  widget: NewsWidgetModel
  editing: boolean
  actions: WidgetActions
  onOpenEntry: (view: View, id: number) => void
  onOpenView: (view: View) => void
  handleProps?: React.ComponentProps<typeof WidgetFrame>['handleProps']
  dragging?: boolean
}

export function viewForSource(source: NewsWidgetModel['source']): View {
  if (source.kind === 'category') return { kind: 'category', id: source.id }
  if (source.kind === 'feed') return { kind: 'feed', id: source.id }
  return { kind: 'all' }
}

export function NewsWidget({ widget, editing, actions, onOpenEntry, onOpenView, handleProps, dragging }: Props) {
  const data = useDashboardData()
  const { title, orphan } = widgetTitle(widget, data.categoryMap, data.feedMap)
  const view = viewForSource(widget.source)

  const feedIds = widget.source.kind === 'category' ? categoryFeedIds(data.feeds, widget.source.id) : widget.source.kind === 'feed' ? [widget.source.id] : null
  const feedKey = feedIds?.join(',') ?? 'all'

  const entries = useLiveQuery(
    () => (feedIds ? latestForFeeds(feedIds, { unreadOnly: widget.unreadOnly, limit: widget.limit }) : latestAll({ unreadOnly: widget.unreadOnly, limit: widget.limit })),
    [feedKey, widget.unreadOnly, widget.limit],
  )
  // Garde les dernières données pendant les rejeux Dexie pour éviter le clignotement.
  const last = useRef<LocalEntry[]>([])
  if (entries) last.current = entries
  const items = entries ?? last.current

  const unreadCount = feedIds ? feedIds.reduce((a, id) => a + (data.unreadByFeed.get(id) ?? 0), 0) : [...data.unreadByFeed.values()].reduce((a, b) => a + b, 0)

  return (
    <WidgetFrame
      widgetId={widget.id}
      widgetType="news"
      title={title}
      count={unreadCount}
      editing={editing}
      width={widget.width as WidgetWidth}
      orphan={orphan}
      loading={entries === undefined && last.current.length === 0 && !data.ready}
      empty={items.length === 0}
      emptyLabel={widget.unreadOnly ? 'Tout est lu' : 'Aucun article'}
      onOpen={() => onOpenView(view)}
      onMarkAllRead={() => {
        const ids = items.filter((e) => !e.read).map((e) => e.id)
        if (ids.length) void recordBulkRead(ids, true)
      }}
      actions={actions}
      handleProps={handleProps}
      dragging={dragging}
    >
      {widget.display === 'cards' ? (
        <ul className="grid gap-3 p-3 sm:grid-cols-2">
          {items.map((e) => (
            <li key={e.id}>
              <button type="button" data-entry-id={e.id} onClick={() => onOpenEntry(view, e.id)} className="group flex w-full gap-3 rounded-xl border border-app p-2 text-left hover:bg-muted">
                {(e.cover || e.youtube_id) && (
                  <img src={e.cover ?? `https://i.ytimg.com/vi/${e.youtube_id}/mqdefault.jpg`} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-20 shrink-0 rounded-lg object-cover bg-muted" />
                )}
                <div className="min-w-0 flex-1">
                  <Meta entry={e} feed={data.feedMap.get(e.feed_id)} showFeed={widget.source.kind !== 'feed'} />
                  <h3 className={clsx('mt-0.5 line-clamp-2 text-sm leading-snug', e.read ? 'text-muted' : 'font-semibold text-app')}>{e.title}</h3>
                  {e.excerpt && <p className="mt-1 line-clamp-2 text-xs text-muted">{e.excerpt}</p>}
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="divide-y divide-app">
          {items.map((e) => (
            <li key={e.id}>
              <button type="button" data-entry-id={e.id} onClick={() => onOpenEntry(view, e.id)} className="flex w-full items-start gap-2.5 px-4 py-2 text-left hover:bg-muted">
                {!e.read ? <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent-500" /> : <span className="mt-2 size-1.5 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <h3 className={clsx('line-clamp-2 text-sm leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                  <Meta entry={e} feed={data.feedMap.get(e.feed_id)} showFeed={widget.source.kind !== 'feed'} />
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </WidgetFrame>
  )
}

function Meta({ entry, feed, showFeed }: { entry: LocalEntry; feed?: ReturnType<typeof useDashboardData>['feeds'][number]; showFeed: boolean }) {
  return (
    <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
      {showFeed && feed && <FeedIcon feed={feed} className="size-3" />}
      {showFeed && <span className="truncate">{feed?.title || hostname(entry.url)}</span>}
      <span className={clsx('shrink-0 tabular-nums', showFeed && 'ml-auto')}>{relativeDate(entry.published_at)}</span>
      {entry.starred && <Star className="size-3 shrink-0 fill-amber-400 text-amber-400" />}
      {entry.has_summary && <Sparkles className="size-3 shrink-0 text-accent-500" />}
    </div>
  )
}
