import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { AlignLeft, ChevronLeft, Circle, CircleCheck, ExternalLink, FileText, Loader2, Sparkles, Star, Type, WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, isOnline, unwrap, type Feed } from '../../api/client'
import { ThemeToggle } from '../../components/ThemeToggle'
import { TypographyPicker } from '../../components/TypographyPicker'
import { useToast } from '../../components/feedback'
import { EmptyState, IconButton, Kbd, Spinner } from '../../components/ui'
import { db, toLocalEntry } from '../../db/db'
import { fullDate, hostname } from '../../lib/format'
import { sanitizeArticle } from '../../lib/markdown'
import { recordChange } from '../../sync/engine'
import { SummaryPanel } from '../summary/SummaryPanel'
import { openExternal } from '../../lib/links'
import { FeedIcon } from '../feeds/FeedIcon'
import { getLocale, useT } from '../../i18n'

interface Props {
  entryId: number | null
  onBack?: () => void
  summaryRequested: number // compteur incrémenté par le raccourci "r"
  fullContentRequested: number // compteur incrémenté par le raccourci "f"
  onCreateSmartFeed?: (feedId: number) => void
}

export function Reader({ entryId, onBack, summaryRequested, fullContentRequested, onCreateSmartFeed }: Props) {
  const tt = useT()
  const t = tt.reader
  const raw = useLiveQuery(async () => (entryId ? ((await db.entries.get(entryId)) ?? null) : null), [entryId])
  // Entre deux articles, useLiveQuery renvoie encore le résultat précédent jusqu'à la première émission de
  // la nouvelle requête : on le traite comme « en chargement » plutôt que d'afficher un instant l'ancien
  // article sous le nouvel identifiant (même comportement en colonnes et dans la fenêtre de lecture).
  const entry = raw === null ? null : raw && raw.id === entryId ? raw : undefined
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
  // Pendant le rendu, pas dans un effet : sinon le nouvel article serait affiché une fois avec l'état
  // du précédent, et `SummaryPanel` monté avec un `autoStart` que personne n'a demandé pour lui.
  const [shownId, setShownId] = useState<number | null>(null)
  if (entry && shownId !== entry.id) {
    setShownId(entry.id)
    setShowFull(true)
    setSummaryOpen(Boolean(entry.has_summary))
    setSummaryAuto(false)
  }
  useEffect(() => {
    containerRef.current?.scrollTo({ top: 0 })
  }, [shownId])

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
        toast(t.offlineFull, 'error')
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
        if (updated.extraction_status === 'failed') toast(updated.extraction_error ?? t.extractionFailed, 'error')
        else setShowFull(true)
      } catch (err) {
        toast(err instanceof Error ? err.message : tt.common.error, 'error')
      } finally {
        setExtracting(false)
      }
    },
    [entry, toast, t, tt],
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
            <IconButton label={t.backToList} onClick={onBack}>
              <ChevronLeft className="size-5" />
            </IconButton>
          </header>
        )}
        {entryId !== null && !missing ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : missing ? (
          <EmptyState icon={<AlignLeft className="size-10" />} title={t.notFoundTitle} hint={t.notFoundHint} />
        ) : (
        <EmptyState
          icon={<AlignLeft className="size-10" />}
          title={t.selectTitle}
          hint={t.selectHint}
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
          <IconButton label={t.backToList} onClick={onBack}>
            <ChevronLeft className="size-5" />
          </IconButton>
        )}
        <div className="flex-1" />
        <span className="md:hidden">
          <ThemeToggle size="md" />
        </span>
        <div className="relative" ref={typoRef}>
          <IconButton label={t.typography} active={typoOpen} onClick={() => setTypoOpen((o) => !o)}>
            <Type className="size-5" />
          </IconButton>
          {typoOpen && (
            <div className="absolute right-0 top-full z-30 mt-1 w-80 rounded-2xl border border-app bg-panel p-4 shadow-xl" data-typo-popover>
              <TypographyPicker compact />
            </div>
          )}
        </div>
        <IconButton label={entry.read ? t.markUnread : t.markRead} onClick={() => void recordChange(entry.id, { read: !entry.read })}>
          {entry.read ? <CircleCheck className="size-5" /> : <Circle className="size-5" />}
        </IconButton>
        <IconButton label={entry.starred ? t.unstar : t.star} active={entry.starred} onClick={() => void recordChange(entry.id, { starred: !entry.starred })}>
          <Star className={clsx('size-5', entry.starred && 'fill-amber-400 text-amber-400')} />
        </IconButton>
        {!isVideo && (
          <IconButton
            label={hasFull ? (showFull ? t.showFeedContent : t.showFull) : t.fetchFull}
            active={hasFull && showFull}
            onClick={() => (hasFull ? setShowFull((s) => !s) : void fetchFull())}
            disabled={extracting}
          >
            {extracting ? <Loader2 className="size-5 animate-spin" /> : <FileText className="size-5" />}
          </IconButton>
        )}
        <IconButton
          label={t.summarize}
          active={summaryOpen}
          onClick={() => {
            setSummaryOpen(true)
            setSummaryAuto(true)
          }}
        >
          <Sparkles className="size-5" />
        </IconButton>
        {onCreateSmartFeed && (
          <IconButton label={t.createSmartFeed} onClick={() => onCreateSmartFeed(entry.feed_id)} className="hidden sm:inline-flex">
            <WandSparkles className="size-5" />
          </IconButton>
        )}
        {entry.url && (
          <IconButton label={t.openOriginal} onClick={() => openExternal(entry.url)}>
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
                src={`https://www.youtube-nocookie.com/embed/${youtubeId}?enablejsapi=1&rel=0&hl=${getLocale()}`}
                title={entry.title}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
                className="size-full"
              />
            </div>
          )}

          {summaryOpen && <SummaryPanel key={entry.id} entryId={entry.id} isVideo={isVideo} autoStart={summaryAuto} onClose={() => setSummaryOpen(false)} onSeek={isVideo ? seek : undefined} />}

          {entry.extraction_status === 'failed' && !isVideo && (
            <p className="text-xs text-muted rounded-lg bg-muted px-3 py-2">
              {t.fullUnavailable(entry.extraction_error ?? '')}{' '}
              <button type="button" className="underline" onClick={() => void fetchFull('browser')}>
                {t.retryBrowser}
              </button>
            </p>
          )}

          {html ? <div className="prose-reader text-app" dangerouslySetInnerHTML={{ __html: html }} /> : !isVideo && <p className="text-muted text-sm">{t.noContent}</p>}

          {entry.url && (
            <p className="pt-6 border-t border-app">
              <a href={entry.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm text-accent-600 dark:text-accent-300 hover:underline">
                {t.readOn(hostname(entry.url))} <ExternalLink className="size-3.5" />
              </a>
            </p>
          )}
        </div>
      </div>
    </article>
  )
}
