/** Objets d'API réduits aux champs lus par les règles testées. */
import type { Category, EntryOut, Feed, SmartFeed } from '../api/client'

export function feed(id: number, over: Partial<Feed> = {}): Feed {
  return { id, title: `Flux ${id}`, category_id: null, position: 0, view_mode: null, is_youtube: false, ...over } as Feed
}

export function category(id: number, over: Partial<Category> = {}): Category {
  return { id, title: `Dossier ${id}`, position: 0, view_mode: null, ...over } as Category
}

export function smartFeed(id: number, over: Partial<SmartFeed> = {}): SmartFeed {
  return { id, name: `Intelligent ${id}`, position: 0, view_mode: null, ...over } as SmartFeed
}

export function entry(id: number, over: Partial<EntryOut> = {}): EntryOut {
  return {
    id,
    feed_id: 1,
    title: `Article ${id}`,
    url: null,
    author: null,
    published_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z',
    read: false,
    starred: false,
    excerpt: '',
    cover: null,
    youtube_id: null,
    has_content: false,
    has_summary: false,
    smart_feed_ids: [],
    summary_html: null,
    content_html: null,
    ...over,
  } as EntryOut
}
