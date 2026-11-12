import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: false })

/** Un contenu tiers ne pose ni formulaire (hameçonnage dans l'application) ni feuille de style. */
const FORBIDDEN_TAGS = ['form', 'input', 'button', 'select', 'textarea', 'option', 'style']
/** Seules classes gardées : horodatages des résumés et langage d'un bloc de code. Toute autre classe
 * d'un contenu tiers pourrait emprunter celles de l'application (superposition plein écran…). */
const ALLOWED_CLASS = /^(ts|language-[\w-]+)$/

const TIMESTAMP_RE = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g

/** `[h:mm:ss]` (trois groupes) ou `[mm:ss]` (deux groupes, le troisième absent). */
function toSeconds(a: string, b: string, c: string | undefined): number {
  return c !== undefined ? Number(a) * 3600 + Number(b) * 60 + Number(c) : Number(a) * 60 + Number(b)
}

/** Rend un résumé Markdown en HTML sûr ; les horodatages deviennent des liens cliquables. */
export function renderSummary(md: string, withTimestamps: boolean): string {
  let html = marked.parse(md, { async: false }) as string
  if (withTimestamps) {
    html = html.replace(TIMESTAMP_RE, (_all, a: string, b: string, c: string | undefined) => {
      const seconds = toSeconds(a, b, c)
      const label = c !== undefined ? `${a}:${b}:${c}` : `${a}:${b}`
      return `<a class="ts" href="#t=${seconds}" data-ts="${seconds}">${label}</a>`
    })
  }
  return DOMPurify.sanitize(html, { ADD_ATTR: ['data-ts', 'target'], FORBID_TAGS: FORBIDDEN_TAGS, FORBID_ATTR: ['style'] })
}

export function sanitizeArticle(html: string): string {
  return DOMPurify.sanitize(html, {
    ADD_TAGS: ['iframe', 'video', 'audio', 'source'],
    ADD_ATTR: ['allowfullscreen', 'frameborder', 'loading', 'controls', 'poster', 'target'],
    FORBID_TAGS: FORBIDDEN_TAGS,
    FORBID_ATTR: ['style'],
  })
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  const kept = [...node.classList].filter((c) => ALLOWED_CLASS.test(c))
  if (kept.length) node.setAttribute('class', kept.join(' '))
  else node.removeAttribute('class')
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
