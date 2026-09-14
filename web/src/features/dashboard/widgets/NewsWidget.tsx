import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { ChevronLeft, ChevronRight, Circle, CircleCheck, Sparkles, Star } from 'lucide-react'
import { useRef, useState, type MouseEvent } from 'react'

import type { Feed } from '../../../api/client'
import type { View } from '../../../app/view'
import type { LocalEntry } from '../../../db/db'
import { hostname, relativeDate } from '../../../lib/format'
import { recordBulkRead, recordChange } from '../../../sync/engine'
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
  const showFeed = widget.source.kind !== 'feed'
  const open = (e: LocalEntry) => onOpenEntry(view, e.id)

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
      {widget.display === 'carousel' && <Carousel items={items} feedMap={data.feedMap} showFeed={showFeed} onOpen={open} />}
      {widget.display === 'magazine' && <Magazine items={items} feedMap={data.feedMap} showFeed={showFeed} width={widget.width} onOpen={open} />}
      {widget.display === 'cards' && <Cards items={items} feedMap={data.feedMap} showFeed={showFeed} onOpen={open} />}
      {widget.display === 'list' && <SimpleList items={items} feedMap={data.feedMap} showFeed={showFeed} onOpen={open} />}
    </WidgetFrame>
  )
}

/* ---------- Visuel : image de couverture ou substitut ---------- */

const PLACEHOLDER_HUES = [212, 262, 330, 18, 152, 190, 42, 280]

function coverUrl(e: LocalEntry): string | null {
  if (e.cover) return e.cover
  if (e.youtube_id) return `https://i.ytimg.com/vi/${e.youtube_id}/mqdefault.jpg`
  return null
}

export function Cover({ entry, feed, className }: { entry: LocalEntry; feed?: Feed; className?: string }) {
  const [failed, setFailed] = useState(false)
  const url = coverUrl(entry)
  if (url && !failed) {
    return <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className={clsx('object-cover bg-muted', className)} />
  }
  // Substitut : dégradé stable par flux, initiale du flux ou de l'hôte.
  const hue = PLACEHOLDER_HUES[entry.feed_id % PLACEHOLDER_HUES.length]
  const label = (feed?.title || hostname(entry.url) || '•').trim()
  // Teinte translucide posée sur bg-muted : pastel en thème clair, sourde en thème sombre.
  return (
    <div
      aria-hidden
      className={clsx('flex items-center justify-center overflow-hidden bg-muted', className)}
      style={{ backgroundImage: `linear-gradient(135deg, hsl(${hue} 60% 55% / 0.28), hsl(${(hue + 40) % 360} 60% 45% / 0.42))` }}
    >
      {feed?.icon_url && !feed.is_youtube ? (
        <img src={feed.icon_url} alt="" referrerPolicy="no-referrer" className="size-1/3 max-h-10 max-w-10 rounded-md object-contain opacity-80 drop-shadow-sm" onError={(ev) => ((ev.target as HTMLImageElement).style.display = 'none')} />
      ) : (
        <span className="select-none text-3xl font-bold text-app/40">{label.charAt(0).toUpperCase()}</span>
      )}
    </div>
  )
}

/* ---------- Ligne de métadonnées + actions ---------- */

function Meta({ entry, feed, showFeed, className }: { entry: LocalEntry; feed?: Feed; showFeed: boolean; className?: string }) {
  return (
    <div className={clsx('flex items-center gap-1.5 text-[11px] text-muted min-w-0', className)}>
      {showFeed && feed && <FeedIcon feed={feed} className="size-3" />}
      {showFeed && <span className="truncate">{feed?.title || hostname(entry.url)}</span>}
    </div>
  )
}

function ItemActions({ entry, className }: { entry: LocalEntry; className?: string }) {
  const stop = (fn: () => void) => (ev: MouseEvent) => {
    ev.preventDefault()
    ev.stopPropagation()
    fn()
  }
  return (
    <div className={clsx('flex items-center gap-0.5', className)}>
      <button
        type="button"
        aria-label={entry.starred ? 'Retirer des favoris' : 'Ajouter aux favoris'}
        onClick={stop(() => void recordChange(entry.id, { starred: !entry.starred }))}
        className={clsx('rounded-md p-1 hover:bg-muted', entry.starred ? 'text-amber-400' : 'text-muted hover:text-app')}
      >
        <Star className={clsx('size-3.5', entry.starred && 'fill-current')} />
      </button>
      <button
        type="button"
        aria-label={entry.read ? 'Marquer non lu' : 'Marquer lu'}
        onClick={stop(() => void recordChange(entry.id, { read: !entry.read }))}
        className="rounded-md p-1 text-muted hover:bg-muted hover:text-app"
      >
        {entry.read ? <CircleCheck className="size-3.5" /> : <Circle className="size-3.5" />}
      </button>
    </div>
  )
}

interface ListProps {
  items: LocalEntry[]
  feedMap: Map<number, Feed>
  showFeed: boolean
  onOpen: (e: LocalEntry) => void
}

/* ---------- Carrousel : grandes cartes horizontales ---------- */

function Carousel({ items, feedMap, showFeed, onOpen }: ListProps) {
  const scroller = useRef<HTMLUListElement>(null)
  const scrollBy = (dir: -1 | 1) => scroller.current?.scrollBy({ left: dir * Math.max(240, scroller.current.clientWidth * 0.8), behavior: 'smooth' })
  return (
    <div className="group/carousel relative">
      <ul ref={scroller} className="scrollbar-none flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 py-4">
        {items.map((e) => {
          const feed = feedMap.get(e.feed_id)
          return (
            <li key={e.id} className="w-56 shrink-0 snap-start sm:w-60">
              <article className="group flex h-full flex-col">
                <button type="button" data-entry-id={e.id} onClick={() => onOpen(e)} className="block w-full text-left">
                  <Cover entry={e} feed={feed} className="aspect-[16/10] w-full rounded-xl transition-transform group-hover:scale-[1.02]" />
                  <h3 className={clsx('mt-2.5 line-clamp-3 text-[15px] font-semibold leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                </button>
                <div className="mt-auto flex items-center justify-between pt-2">
                  <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted">
                    {showFeed && feed && <FeedIcon feed={feed} className="size-3" />}
                    {showFeed && <span className="truncate max-w-[7rem]">{feed?.title || hostname(e.url)}</span>}
                    <span className="shrink-0 tabular-nums">{relativeDate(e.published_at)}</span>
                  </div>
                  <ItemActions entry={e} />
                </div>
              </article>
            </li>
          )
        })}
      </ul>
      <button
        type="button"
        aria-label="Précédent"
        onClick={() => scrollBy(-1)}
        className="absolute left-1 top-1/2 hidden size-9 -translate-y-1/2 items-center justify-center rounded-full border border-app bg-panel/90 text-app shadow-md backdrop-blur opacity-0 transition-opacity group-hover/carousel:opacity-100 md:flex"
      >
        <ChevronLeft className="size-5" />
      </button>
      <button
        type="button"
        aria-label="Suivant"
        onClick={() => scrollBy(1)}
        className="absolute right-1 top-1/2 hidden size-9 -translate-y-1/2 items-center justify-center rounded-full border border-app bg-panel/90 text-app shadow-md backdrop-blur opacity-0 transition-opacity group-hover/carousel:opacity-100 md:flex"
      >
        <ChevronRight className="size-5" />
      </button>
    </div>
  )
}

/* ---------- Magazine : vignette à gauche, titre, source, extrait ---------- */

function Magazine({ items, feedMap, showFeed, width, onOpen }: ListProps & { width: WidgetWidth }) {
  const narrow = width === 2
  return (
    <ul className="divide-y divide-app">
      {items.map((e) => {
        const feed = feedMap.get(e.feed_id)
        return (
          <li key={e.id}>
            <article className="group flex gap-4 px-4 py-3.5 hover:bg-muted/60 transition-colors">
              <button type="button" data-entry-id={e.id} onClick={() => onOpen(e)} className="shrink-0" aria-label={e.title}>
                <Cover entry={e} feed={feed} className={clsx('rounded-lg', narrow ? 'h-16 w-24' : 'h-24 w-36 sm:h-28 sm:w-44')} />
              </button>
              <div className="flex min-w-0 flex-1 flex-col">
                <button type="button" onClick={() => onOpen(e)} className="text-left">
                  <h3 className={clsx('line-clamp-2 text-[15px] font-semibold leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                </button>
                {showFeed && <Meta entry={e} feed={feed} showFeed className="mt-1" />}
                {!narrow && e.excerpt && <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-muted">{e.excerpt}</p>}
                <div className="mt-auto flex items-center justify-between pt-1.5">
                  <span className="text-[11px] tabular-nums text-muted">{relativeDate(e.published_at)}</span>
                  <div className="flex items-center gap-1">
                    {e.has_summary && <Sparkles className="size-3.5 text-accent-500" />}
                    <ItemActions entry={e} />
                  </div>
                </div>
              </div>
            </article>
          </li>
        )
      })}
    </ul>
  )
}

/* ---------- Cartes : grille compacte ---------- */

function Cards({ items, feedMap, showFeed, onOpen }: ListProps) {
  return (
    <ul className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((e) => {
        const feed = feedMap.get(e.feed_id)
        return (
          <li key={e.id}>
            <article className="group flex h-full flex-col overflow-hidden rounded-xl border border-app">
              <button type="button" data-entry-id={e.id} onClick={() => onOpen(e)} className="block text-left">
                <Cover entry={e} feed={feed} className="aspect-[16/9] w-full" />
                <div className="px-3 pt-2.5">
                  <h3 className={clsx('line-clamp-2 text-sm font-semibold leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                  {e.excerpt && <p className="mt-1 line-clamp-2 text-xs text-muted">{e.excerpt}</p>}
                </div>
              </button>
              <div className="mt-auto flex items-center justify-between px-3 pb-2 pt-1.5">
                <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted">
                  {showFeed && feed && <FeedIcon feed={feed} className="size-3" />}
                  {showFeed && <span className="truncate max-w-[8rem]">{feed?.title || hostname(e.url)}</span>}
                  <span className="shrink-0 tabular-nums">{relativeDate(e.published_at)}</span>
                </div>
                <ItemActions entry={e} />
              </div>
            </article>
          </li>
        )
      })}
    </ul>
  )
}

/* ---------- Liste : compacte, sans image ---------- */

function SimpleList({ items, feedMap, showFeed, onOpen }: ListProps) {
  return (
    <ul className="divide-y divide-app">
      {items.map((e) => {
        const feed = feedMap.get(e.feed_id)
        return (
          <li key={e.id}>
            <div className="group flex items-start gap-2.5 px-4 py-2 hover:bg-muted/60">
              <button type="button" data-entry-id={e.id} onClick={() => onOpen(e)} className="flex min-w-0 flex-1 items-start gap-2.5 text-left">
                {!e.read ? <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent-500" /> : <span className="mt-2 size-1.5 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <h3 className={clsx('line-clamp-2 text-sm leading-snug', e.read ? 'text-muted' : 'text-app')}>{e.title}</h3>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
                    {showFeed && feed && <FeedIcon feed={feed} className="size-3" />}
                    {showFeed && <span className="truncate">{feed?.title || hostname(e.url)}</span>}
                    <span className={clsx('shrink-0 tabular-nums', showFeed && 'ml-auto')}>{relativeDate(e.published_at)}</span>
                    {e.starred && <Star className="size-3 shrink-0 fill-amber-400 text-amber-400" />}
                    {e.has_summary && <Sparkles className="size-3 shrink-0 text-accent-500" />}
                  </div>
                </div>
              </button>
              <ItemActions entry={e} className="opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
