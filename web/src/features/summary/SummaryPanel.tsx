import clsx from 'clsx'
import { useLiveQuery } from 'dexie-react-hooks'
import { RefreshCw, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { isOnline, type SummaryOut } from '../../api/client'
import { postSse } from '../../api/sse'
import { Button, IconButton } from '../../components/ui'
import { db } from '../../db/db'
import { formatTokens } from '../../lib/format'
import { renderSummary } from '../../lib/markdown'

interface Props {
  entryId: number
  isVideo: boolean
  autoStart: boolean
  onClose: () => void
  onSeek?: (seconds: number) => void
}

type Phase = 'idle' | 'streaming' | 'done' | 'error'

export function SummaryPanel({ entryId, isVideo, autoStart, onClose, onSeek }: Props) {
  // undefined = requête en cours ; null = aucun résumé stocké.
  const stored = useLiveQuery(
    () => db.summaries.where('entry_id').equals(entryId).sortBy('created_at').then((s) => (s.at(-1) as SummaryOut | undefined) ?? null),
    [entryId],
  )
  const [phase, setPhase] = useState<Phase>('idle')
  const [status, setStatus] = useState<string>('')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const started = useRef(false)

  const run = useCallback(
    async (force: boolean) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setPhase('streaming')
      setError(null)
      setText('')
      setStatus('Connexion…')
      if (!isOnline()) {
        setPhase('error')
        setError('Le résumé nécessite une connexion au serveur.')
        return
      }
      try {
        for await (const evt of postSse(`/api/entries/${entryId}/summarize${force ? '?force=true' : ''}`, controller.signal)) {
          const data = JSON.parse(evt.data) as Record<string, unknown>
          if (evt.event === 'status') setStatus(String(data.message ?? ''))
          else if (evt.event === 'delta') setText((t) => t + String(data.text ?? ''))
          else if (evt.event === 'done') {
            const summary = data.summary as SummaryOut
            await db.summaries.put(summary)
            await db.entries.update(entryId, { has_summary: true })
            setText(summary.text_md)
            setPhase('done')
          } else if (evt.event === 'error') {
            setError(String(data.message ?? 'Erreur'))
            setPhase('error')
          }
        }
        setPhase((p) => (p === 'streaming' ? 'done' : p))
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : 'Erreur')
        setPhase('error')
      }
    },
    [entryId],
  )

  useEffect(() => {
    started.current = false
    setPhase('idle')
    setText('')
    setError(null)
    return () => abortRef.current?.abort()
  }, [entryId])

  useEffect(() => {
    if (stored === undefined) return // requête Dexie en cours
    if (started.current) return
    started.current = true
    if (!stored && autoStart) void run(false)
  }, [stored, autoStart, run])

  const showText = phase === 'streaming' || phase === 'error' ? text : stored?.text_md ?? text
  const html = showText ? renderSummary(showText, isVideo) : ''

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = (e.target as HTMLElement).closest<HTMLElement>('a.ts')
    if (target && onSeek) {
      e.preventDefault()
      onSeek(Number(target.dataset.ts))
    }
  }

  return (
    <aside className="rounded-2xl border border-accent-200 dark:border-accent-800/60 bg-accent-50/60 dark:bg-accent-900/10 p-5 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-accent-600 dark:text-accent-300" />
        <h2 className="text-sm font-semibold text-app flex-1">Résumé</h2>
        {stored && phase !== 'streaming' && (
          <span className="text-[11px] text-muted hidden sm:inline" title={`${formatTokens(stored.input_tokens)} tokens en entrée, ${formatTokens(stored.output_tokens)} en sortie`}>
            {stored.model}
          </span>
        )}
        {(stored || phase === 'done' || phase === 'error') && (
          <IconButton label="Régénérer" size="sm" onClick={() => void run(true)} disabled={phase === 'streaming'}>
            <RefreshCw className="size-4" />
          </IconButton>
        )}
        <IconButton label="Fermer" size="sm" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>

      {phase === 'streaming' && !text && (
        <p className="text-sm text-muted animate-soft-pulse">{status || 'Rédaction du résumé…'}</p>
      )}

      {phase === 'idle' && !stored && !autoStart && (
        <Button size="sm" variant="primary" onClick={() => void run(false)}>
          <Sparkles className="size-4" /> Résumer {isVideo ? 'la vidéo' : "l'article"}
        </Button>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {html && <div className={clsx('prose-summary text-app', phase === 'streaming' && 'after:inline-block after:w-2 after:h-4 after:ml-0.5 after:bg-accent-500 after:animate-soft-pulse after:align-text-bottom')} dangerouslySetInnerHTML={{ __html: html }} onClick={onClick} />}
    </aside>
  )
}
