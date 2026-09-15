import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { AlignLeft, ChevronLeft, Circle, CircleCheck, ExternalLink, FileText, Loader2, Sparkles, Star, Type, WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, isOnline, unwrap, type Feed } from '../../api/client'
import { ThemeToggle } from '../../components/ThemeToggle'
import { TypographyPicker } from '../../components/TypographyPicker'
import { EmptyState, IconButton, Kbd, Spinner, useToast } from '../../components/ui'
import { db, toLocalEntry } from '../../db/db'
import { fullDate, hostname } from '../../lib/format'
import { sanitizeArticle } from '../../lib/markdown'
import { recordChange } from '../../sync/engine'
import { SummaryPanel } from '../summary/SummaryPanel'
import { FeedIcon } from '../feeds/Sidebar'

interface Props {
  entryId: number | null
  onBack?: () => void
  summaryRequested: number // compteur incrémenté par le raccourci "r"
  fullContentRequested: number // compteur incrémenté par le raccourci "f"
  onCreateSmartFeed?: (feedId: number) => void
}

/** N'ouvre que des liens http(s) : un flux ne doit pas pouvoir déclencher un autre schéma. */
export function openExternal(url: string | null | undefined) {
  if (url && /^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener')
}

export function Reader({ entryId, onBack, summaryRequested, fullContentRequested, onCreateSmartFeed }: Props) {
  const entry = useLiveQuery(async () => (entryId ? ((await db.entries.get(entryId)) ?? null) : null), [entryId])
  const feed = useLiveQuery(() => (entry ? db.feeds.get(entry.feed_id) : undefined), [entry?.feed_id]) as Feed | undefined
  const [showFull, setShowFull] = useState(true)
  const [extracting, setExtracting] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [summaryAuto, setSummaryAuto] = useState(false)
  const [typoOpen, setTypoOpen] = useState(false)
  const typoRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<HTMLIFrameElement>(null)
  const toast = useToast()

  // Réinitialisation à chaque changement d'article (et seulement là : l'arrivée d'un résumé ou d'un
  // changement d'état ne doit ni remonter la page ni perdre la bascule flux / article complet).
  const initializedFor = useRef<number | null>(null)
  useEffect(() => {
    if (!entry || initializedFor.current === entry.id) return
    initializedFor.current = entry.id
    setShowFull(true)
    setSummaryOpen(Boolean(entry.has_summary))
    setSummaryAuto(false)
    containerRef.current?.scrollTo({ top: 0 })
  }, [entry])

  useEffect(() => {
    if (!typoOpen) return
    const close = (e: MouseEvent) => {
      if (!typoRef.current?.contains(e.target as Node)) setTypoOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [typoOpen])

  // Marque comme lu une seule fois, à l'ouverture : « marquer non lu » doit ensuite rester possible.
  const markedRead = useRef<number | null>(null)
  useEffect(() => {
    if (!entry || markedRead.current === entry.id) return
    markedRead.current = entry.id
    if (!entry.read) void recordChange(entry.id, { read: true })
  }, [entry])

  const fetchFull = useCallback(
    async (mode: 'auto' | 'http' | 'browser' = 'auto') => {
      if (!entry) return
      if (!isOnline()) {
        toast('Hors ligne : impossible de récupérer l’article complet', 'error')
        return
      }
      setExtracting(true)
      try {
        const updated = unwrap(await api().POST('/api/entries/{entry_id}/fetch-content', { params: { path: { entry_id: entry.id } }, body: { mode } }))
        // Fusion au moment de la réponse : l'état lu/favori local (éventuellement modifié pendant
        // l'attente, ou en attente dans l'outbox) prime sur l'instantané renvoyé par le serveur ;
        // seul le contenu extrait est pris de la réponse.
        await db.transaction('rw', db.entries, async () => {
          const current = await db.entries.get(entry.id)
          const server = toLocalEntry(updated)
          await db.entries.put(current ? { ...server, read: current.read, readFlag: current.readFlag, starred: current.starred, starredFlag: current.starredFlag } : server)
        })
        if (updated.extraction_status === 'failed') toast(updated.extraction_error ?? 'Extraction impossible', 'error')
        else setShowFull(true)
      } catch (err) {
        toast(err instanceof Error ? err.message : 'Erreur', 'error')
      } finally {
        setExtracting(false)
      }
    },
    [entry, toast],
  )

  useEffect(() => {
    if (summaryRequested > 0 && entry) {
      setSummaryOpen(true)
      setSummaryAuto(true)
    }
  }, [summaryRequested]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (fullContentRequested > 0 && entry) {
      if (entry.extraction_status === 'done') setShowFull((s) => !s)
      else void fetchFull()
    }
  }, [fullContentRequested]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!entryId || !entry) {
    const missing = entryId !== null && entry === null
    return (
      <div className={clsx('flex h-full flex-col bg-app', !onBack && 'hidden md:flex')}>
        {onBack && (
          <header className="flex h-14 shrink-0 items-center gap-1 border-b border-app bg-panel px-2">
            <IconButton label="Retour à la liste" onClick={onBack}>
              <ChevronLeft className="size-5" />
            </IconButton>
          </header>
        )}
        {entryId !== null && !missing ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : missing ? (
          <EmptyState icon={<AlignLeft className="size-10" />} title="Article introuvable sur cet appareil" hint="Il n’a pas encore été synchronisé ou a été purgé de la base locale." />
        ) : (
        <EmptyState
          icon={<AlignLeft className="size-10" />}
          title="Sélectionnez un article"
          hint="Naviguez avec j / k, marquez comme lu avec m, mettez en favori avec s, résumez avec r."
          action={
            <div className="flex flex-wrap justify-center gap-1.5 text-xs text-muted mt-2">
              <Kbd>j</Kbd>
              <Kbd>k</Kbd>
              <Kbd>m</Kbd>
              <Kbd>s</Kbd>
              <Kbd>v</Kbd>
              <Kbd>f</Kbd>
              <Kbd>r</Kbd>
            </div>
          }
        />
        )}
      </div>
    )
  }

  const hasFull = entry.extraction_status === 'done' && Boolean(entry.content_html)
  const html = sanitizeArticle((showFull && hasFull ? entry.content_html : entry.summary_html) ?? '')
  const youtubeId = entry.youtube_id
  const isVideo = Boolean(youtubeId)

  const seek = (seconds: number) => {
    const win = playerRef.current?.contentWindow
    if (!win) return
    win.postMessage(JSON.stringify({ event: 'command', func: 'seekTo', args: [seconds, true] }), '*')
    win.postMessage(JSON.stringify({ event: 'command', func: 'playVideo', args: [] }), '*')
    playerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <article className="flex h-full flex-col bg-app">
      <header className="flex items-center gap-1 h-14 px-2 border-b border-app bg-panel shrink-0">
        {onBack && (
          <IconButton label="Retour à la liste" onClick={onBack}>
            <ChevronLeft className="size-5" />
          </IconButton>
        )}
        <div className="flex-1" />
        <span className="md:hidden">
          <ThemeToggle size="md" />
        </span>
        <div className="relative" ref={typoRef}>
          <IconButton label="Typographie de lecture" active={typoOpen} onClick={() => setTypoOpen((o) => !o)}>
            <Type className="size-5" />
          </IconButton>
          {typoOpen && (
            <div className="absolute right-0 top-full z-30 mt-1 w-80 rounded-2xl border border-app bg-panel p-4 shadow-xl" data-typo-popover>
              <TypographyPicker compact />
            </div>
          )}
        </div>
        <IconButton label={entry.read ? 'Marquer non lu (m)' : 'Marquer lu (m)'} onClick={() => void recordChange(entry.id, { read: !entry.read })}>
          {entry.read ? <CircleCheck className="size-5" /> : <Circle className="size-5" />}
        </IconButton>
        <IconButton label={entry.starred ? 'Retirer des favoris (s)' : 'Ajouter aux favoris (s)'} active={entry.starred} onClick={() => void recordChange(entry.id, { starred: !entry.starred })}>
          <Star className={clsx('size-5', entry.starred && 'fill-amber-400 text-amber-400')} />
        </IconButton>
        {!isVideo && (
          <IconButton
            label={hasFull ? (showFull ? 'Voir le contenu du flux (f)' : 'Voir l’article complet (f)') : 'Récupérer l’article complet (f)'}
            active={hasFull && showFull}
            onClick={() => (hasFull ? setShowFull((s) => !s) : void fetchFull())}
            disabled={extracting}
          >
            {extracting ? <Loader2 className="size-5 animate-spin" /> : <FileText className="size-5" />}
          </IconButton>
        )}
        <IconButton
          label="Résumer (r)"
          active={summaryOpen}
          onClick={() => {
            setSummaryOpen(true)
            setSummaryAuto(true)
          }}
        >
          <Sparkles className="size-5" />
        </IconButton>
        {onCreateSmartFeed && (
          <IconButton label="Créer un flux intelligent à partir de ce flux" onClick={() => onCreateSmartFeed(entry.feed_id)} className="hidden sm:inline-flex">
            <WandSparkles className="size-5" />
          </IconButton>
        )}
        {entry.url && (
          <IconButton label="Ouvrir l’original (v)" onClick={() => openExternal(entry.url)}>
            <ExternalLink className="size-5" />
          </IconButton>
        )}
      </header>

      <div ref={containerRef} className="flex-1 overflow-y-auto">
        <div className="reader-column mx-auto px-5 sm:px-8 py-8 space-y-6">
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-muted">
              {feed && <FeedIcon feed={feed} />}
              <span className="truncate">{feed?.title ?? hostname(entry.url)}</span>
              {entry.author && <span className="truncate">· {entry.author}</span>}
            </div>
            <h1 className="reader-heading text-2xl sm:text-3xl font-bold leading-tight tracking-tight text-app">
              {entry.url ? (
                <a href={entry.url} target="_blank" rel="noopener noreferrer" className="hover:underline decoration-accent-400 underline-offset-4">
                  {entry.title}
                </a>
              ) : (
                entry.title
              )}
            </h1>
            <p className="text-sm text-muted">{fullDate(entry.published_at)}</p>
          </div>

          {youtubeId && (
            <div className="aspect-video w-full overflow-hidden rounded-xl bg-black">
              <iframe
                ref={playerRef}
                src={`https://www.youtube-nocookie.com/embed/${youtubeId}?enablejsapi=1&rel=0&hl=fr`}
                title={entry.title}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
                className="size-full"
              />
            </div>
          )}

          {summaryOpen && <SummaryPanel entryId={entry.id} isVideo={isVideo} autoStart={summaryAuto} onClose={() => setSummaryOpen(false)} onSeek={isVideo ? seek : undefined} />}

          {entry.extraction_status === 'failed' && !isVideo && (
            <p className="text-xs text-muted rounded-lg bg-muted px-3 py-2">
              Article complet indisponible : {entry.extraction_error}.{' '}
              <button type="button" className="underline" onClick={() => void fetchFull('browser')}>
                Réessayer via le navigateur
              </button>
            </p>
          )}

          {html ? <div className="prose-reader text-app" dangerouslySetInnerHTML={{ __html: html }} /> : !isVideo && <p className="text-muted text-sm">Ce flux ne fournit pas de contenu. Utilisez « Article complet » pour le récupérer.</p>}

          {entry.url && (
            <p className="pt-6 border-t border-app">
              <a href={entry.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm text-accent-600 dark:text-accent-300 hover:underline">
                Lire sur {hostname(entry.url)} <ExternalLink className="size-3.5" />
              </a>
            </p>
          )}
        </div>
      </div>
    </article>
  )
}
