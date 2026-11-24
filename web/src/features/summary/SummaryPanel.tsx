import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { ChevronDown, RefreshCw, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { isOnline, type SummaryOut } from '../../api/client'
import { postSse } from '../../api/sse'
import { Button, IconButton } from '../../components/ui'
import { db } from '../../db/db'
import { formatTokens } from '../../lib/format'
import { renderSummary } from '../../lib/markdown'
import { useT } from '../../i18n'
import { SummaryMenu } from './SummaryMenu'
import { distinctSummaries, isSummaryStyle, pickSummary, requestKey, summaryKey, type SummaryRequest } from './summaryStyles'

interface Props {
  entryId: number
  isVideo: boolean
  /** Raccourci « r » : résumé du type par défaut s'il n'en existe aucun. */
  autoStart: boolean
  /** Choix fait dans le menu ✨ du lecteur ; `nonce` change à chaque choix. */
  request: { nonce: number; value: SummaryRequest } | null
  deep: boolean
  onDeepChange: (deep: boolean) => void
  onClose: () => void
  onSeek?: (seconds: number) => void
}

type Phase = 'idle' | 'streaming' | 'done' | 'error'

export function SummaryPanel({ entryId, isVideo, autoStart, request, deep, onDeepChange, onClose, onSeek }: Props) {
  const tt = useT()
  const t = tt.reader.summary
  // undefined = requête en cours ; tableau vide = aucun résumé stocké.
  const stored = useLiveQuery(() => db.summaries.where('entry_id').equals(entryId).toArray() as Promise<SummaryOut[]>, [entryId])
  const summaries = useMemo(() => distinctSummaries(stored ?? []), [stored])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [status, setStatus] = useState<string>('')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  // Demande en cours : son type s'affiche dans l'en-tête pendant la génération.
  const [pending, setPending] = useState<SummaryRequest | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const started = useRef(false)

  const current = pickSummary(stored ?? [], selectedKey)

  const run = useCallback(
    async (request: SummaryRequest, force: boolean) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setPhase('streaming')
      setError(null)
      setText('')
      setStatus(t.connecting)
      setSelectedKey(requestKey(request))
      setPending(request)
      if (!isOnline()) {
        setPhase('error')
        setError(t.offline)
        return
      }
      const body = { style: request.style ?? null, question: request.question ?? null, deep }
      try {
        for await (const evt of postSse(`/api/entries/${entryId}/summarize${force ? '?force=true' : ''}`, controller.signal, body)) {
          const data = JSON.parse(evt.data) as Record<string, unknown>
          if (evt.event === 'status') setStatus(String(data.message ?? ''))
          else if (evt.event === 'delta') setText((t) => t + String(data.text ?? ''))
          else if (evt.event === 'done') {
            const summary = data.summary as SummaryOut
            await db.summaries.put(summary)
            await db.entries.update(entryId, { has_summary: true })
            setSelectedKey(summaryKey(summary))
            setText(summary.text_md)
            setPhase('done')
          } else if (evt.event === 'error') {
            setError(String(data.message ?? tt.common.error))
            setPhase('error')
          }
        }
        setPhase((p) => (p === 'streaming' ? 'done' : p))
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : tt.common.error)
        setPhase('error')
      }
    },
    [entryId, deep, t, tt],
  )

  useEffect(() => {
    started.current = false
    setPhase('idle')
    setText('')
    setError(null)
    setSelectedKey(null)
    setMenuOpen(false)
    return () => abortRef.current?.abort()
  }, [entryId])

  useEffect(() => {
    if (stored === undefined) return // requête Dexie en cours
    if (started.current) return
    started.current = true
    if (stored.length === 0 && autoStart && !request) void run({}, false)
  }, [stored, autoStart, request, run])

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  /** Choix dans le menu : un résumé déjà prêt s'affiche (sauf en mode approfondi, qui en demande un nouveau). */
  const pick = (request: SummaryRequest) => {
    setMenuOpen(false)
    const key = requestKey(request)
    const existing = key ? summaries.find((s) => summaryKey(s) === key) : undefined
    if (existing && !deep) {
      abortRef.current?.abort()
      setPhase('idle')
      setError(null)
      setText('')
      setSelectedKey(key)
      return
    }
    void run(request, Boolean(existing))
  }

  // Choix fait dans le menu ✨ du lecteur, une fois les résumés stockés connus (un type prêt s'affiche).
  const handledNonce = useRef(0)
  useEffect(() => {
    if (!request || stored === undefined || request.nonce === handledNonce.current) return
    handledNonce.current = request.nonce
    pick(request.value)
  }, [request, stored]) // eslint-disable-line react-hooks/exhaustive-deps

  const regenerate = () => {
    const request: SummaryRequest = current?.style === 'question' ? { question: current.question ?? '' } : { style: isSummaryStyle(current?.style) ? current.style : undefined }
    void run(request, true)
  }

  const streamingOrFailed = phase === 'streaming' || phase === 'error'
  const showText = streamingOrFailed ? text : current?.text_md ?? text
  const html = showText ? renderSummary(showText, isVideo) : ''
  const title = (s: Pick<SummaryOut, 'style' | 'question'> | null) =>
    !s ? t.title : s.style === 'question' ? `${t.questionPrefix} ${s.question ?? ''}` : isSummaryStyle(s.style) ? t.styles[s.style].label : t.title
  const pendingAsSummary = pending?.question ? { style: 'question', question: pending.question } : pending?.style ? { style: pending.style, question: null } : null
  const heading = streamingOrFailed ? title(pendingAsSummary) : title(current)
  const others = summaries.filter((s) => s !== current)
  const ready = new Set(summaries.map(summaryKey))

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = (e.target as HTMLElement).closest<HTMLElement>('a.ts')
    if (target && onSeek) {
      e.preventDefault()
      onSeek(Number(target.dataset.ts))
    }
  }

  return (
    <aside className="rounded-2xl border border-accent-200 dark:border-accent-800/60 bg-accent-50/60 dark:bg-accent-900/10 p-5 space-y-3" data-summary-panel data-phase={phase}>
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 shrink-0 text-accent-600 dark:text-accent-300" />
        <div className="relative min-w-0 flex-1" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={t.menuLabel}
            className="flex max-w-full items-center gap-1 rounded-lg px-1.5 py-0.5 -mx-1.5 text-sm font-semibold text-app hover:bg-accent-100/70 dark:hover:bg-accent-800/30"
            data-testid="summary-menu"
          >
            <span className="truncate">{heading}</span>
            <ChevronDown className={clsx('size-4 shrink-0 text-muted transition-transform', menuOpen && 'rotate-180')} />
          </button>
          {menuOpen && <SummaryMenu ready={ready} deep={deep} onDeepChange={onDeepChange} onPick={pick} onClose={() => setMenuOpen(false)} />}
        </div>
        {current && phase !== 'streaming' && (
          <span className="text-[11px] text-muted hidden sm:inline" title={t.tokens(formatTokens(current.input_tokens), formatTokens(current.output_tokens))}>
            {current.model}
          </span>
        )}
        {(current || phase === 'done' || phase === 'error') && (
          <IconButton label={t.regenerate} size="sm" onClick={regenerate} disabled={phase === 'streaming'}>
            <RefreshCw className="size-4" />
          </IconButton>
        )}
        <IconButton label={t.close} size="sm" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>

      {phase === 'streaming' && !text && <p className="text-sm text-muted animate-soft-pulse">{status || t.writing}</p>}

      {phase === 'idle' && !current && !autoStart && (
        <Button size="sm" variant="primary" onClick={() => void run({}, false)}>
          <Sparkles className="size-4" /> {isVideo ? t.summarizeVideo : t.summarizeArticle}
        </Button>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {html && (
        <div
          className={clsx('prose-summary text-app', phase === 'streaming' && 'after:inline-block after:w-2 after:h-4 after:ml-0.5 after:bg-accent-500 after:animate-soft-pulse after:align-text-bottom')}
          dangerouslySetInnerHTML={{ __html: html }}
          onClick={onClick}
          data-summary-text
        />
      )}

      {others.length > 0 && phase !== 'streaming' && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-accent-200/70 pt-3 dark:border-accent-800/40" aria-label={t.otherSummaries}>
          {others.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setPhase('idle')
                setError(null)
                setSelectedKey(summaryKey(s))
              }}
              className="max-w-[16rem] truncate rounded-full border border-accent-200 px-2.5 py-0.5 text-xs text-muted hover:text-app hover:bg-accent-100/70 dark:border-accent-800/60 dark:hover:bg-accent-800/30"
              data-summary-chip={summaryKey(s)}
            >
              {title(s)}
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}
