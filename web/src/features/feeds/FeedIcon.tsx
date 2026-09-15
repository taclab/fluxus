import clsx from 'clsx'
import { Play, Rss } from 'lucide-react'
import { useState } from 'react'

import type { Feed } from '../../api/client'

export function FeedIcon({ feed, className }: { feed: Feed; className?: string }) {
  const [failed, setFailed] = useState(false)
  if (feed.is_youtube) return <Play className={clsx('size-4 text-red-500 shrink-0 fill-current', className)} />
  if (!feed.icon_url || failed) return <Rss className={clsx('size-4 text-muted shrink-0', className)} />
  return <img src={feed.icon_url} alt="" onError={() => setFailed(true)} className={clsx('size-4 rounded-sm shrink-0 object-contain', className)} loading="lazy" referrerPolicy="no-referrer" />
}

export function Count({ n, muted }: { n: number; muted?: boolean }) {
  return <span className={clsx('text-xs tabular-nums shrink-0', muted ? 'text-muted' : 'text-muted font-medium')}>{n > 999 ? '999+' : n}</span>
}
