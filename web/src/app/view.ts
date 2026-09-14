/** La "vue" courante est encodée dans l'URL pour que le bouton retour fonctionne partout. */

import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'

export type ViewKind = 'dashboard' | 'all' | 'unread' | 'starred' | 'feed' | 'category' | 'search'

export interface View {
  kind: ViewKind
  id?: number
  query?: string
}

export function viewPath(view: View): string {
  switch (view.kind) {
    case 'dashboard':
      return '/'
    case 'all':
      return '/all'
    case 'unread':
      return '/unread'
    case 'starred':
      return '/starred'
    case 'feed':
      return `/feed/${view.id}`
    case 'category':
      return `/category/${view.id}`
    case 'search':
      return `/search?q=${encodeURIComponent(view.query ?? '')}`
  }
}

export function useView() {
  const location = useLocation()
  const params = useParams()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()

  const view = useMemo<View>(() => {
    const p = location.pathname
    if (p.startsWith('/feed/')) return { kind: 'feed', id: Number(params.id) }
    if (p.startsWith('/category/')) return { kind: 'category', id: Number(params.id) }
    if (p === '/unread') return { kind: 'unread' }
    if (p === '/starred') return { kind: 'starred' }
    if (p === '/search') return { kind: 'search', query: search.get('q') ?? '' }
    if (p === '/all') return { kind: 'all' }
    return { kind: 'dashboard' }
  }, [location.pathname, params.id, search])

  const entryId = search.get('entry') ? Number(search.get('entry')) : null
  const unreadOnly = search.get('unread') === '1'

  const setView = useCallback(
    (v: View) => {
      const next = new URLSearchParams()
      if (unreadOnly && v.kind !== 'unread' && v.kind !== 'starred') next.set('unread', '1')
      if (v.kind === 'search') next.set('q', v.query ?? '')
      const qs = next.toString()
      navigate(viewPath(v).split('?')[0] + (qs ? `?${qs}` : ''))
    },
    [navigate, unreadOnly],
  )

  const setEntry = useCallback(
    (id: number | null, replace = false) => {
      const next = new URLSearchParams(search)
      if (id === null) next.delete('entry')
      else next.set('entry', String(id))
      setSearch(next, { replace })
    },
    [search, setSearch],
  )

  /** Ouvre un article dans le contexte d'une vue (depuis le tableau de bord). */
  const openEntryIn = useCallback(
    (v: View, id: number) => {
      const base = viewPath(v).split('?')[0]
      const qs = new URLSearchParams()
      if (v.kind === 'search') qs.set('q', v.query ?? '')
      qs.set('entry', String(id))
      navigate(`${base}?${qs.toString()}`)
    },
    [navigate],
  )

  const setUnreadOnly = useCallback(
    (value: boolean) => {
      const next = new URLSearchParams(search)
      if (value) next.set('unread', '1')
      else next.delete('unread')
      next.delete('entry')
      setSearch(next)
    },
    [search, setSearch],
  )

  return { view, entryId, unreadOnly, setView, setEntry, setUnreadOnly, openEntryIn }
}
