import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: false })

const TIMESTAMP_RE = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g

function toSeconds(h: string | undefined, m: string, s: string | undefined): number {
  if (s !== undefined) return Number(h) * 3600 + Number(m) * 60 + Number(s)
  return Number(h) * 60 + Number(m)
}

/** Rend un résumé Markdown en HTML sûr ; les horodatages deviennent des liens cliquables. */
export function renderSummary(md: string, withTimestamps: boolean): string {
  let html = marked.parse(md, { async: false }) as string
  if (withTimestamps) {
    html = html.replace(TIMESTAMP_RE, (_all, a: string, b: string, c: string | undefined) => {
      const seconds = c !== undefined ? toSeconds(a, b, c) : toSeconds(undefined, a, b)
      const label = c !== undefined ? `${a}:${b}:${c}` : `${a}:${b}`
      return `<a class="ts" href="#t=${seconds}" data-ts="${seconds}">${label}</a>`
    })
  }
  return DOMPurify.sanitize(html, { ADD_ATTR: ['data-ts', 'target'] })
}

export function sanitizeArticle(html: string): string {
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['iframe', 'video', 'audio', 'source'],
    ADD_ATTR: ['allowfullscreen', 'frameborder', 'loading', 'controls', 'poster', 'target'],
  })
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
  if (node.tagName === 'IMG') {
    node.setAttribute('loading', 'lazy')
    node.setAttribute('referrerpolicy', 'no-referrer')
  }
  if (node.tagName === 'IFRAME') {
    const src = node.getAttribute('src') ?? ''
    if (!/^https:\/\/(www\.)?(youtube(-nocookie)?\.com|player\.vimeo\.com|www\.dailymotion\.com)\//.test(src)) {
      node.remove()
    }
  }
})
