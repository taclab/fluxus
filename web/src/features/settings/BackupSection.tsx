/**
 * Sauvegarde et migration : export de toute la base en un fichier, import par remplacement total.
 *
 * L'en-tête du fichier (première ligne du JSON Lines compressé) est lu dans le navigateur avant
 * l'envoi, pour montrer ce qui va remplacer le contenu de l'instance. Après l'import, l'époque de
 * synchronisation du serveur change : chaque appareil vide son cache à sa prochaine synchronisation.
 */

import { AlertTriangle, DatabaseBackup, Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { isCapacitor } from '../../api/auth'
import { api, apiUrl, authHeaders, unwrap, type Schemas } from '../../api/client'
import { Button, Dialog, Field, Input, useToast } from '../../components/ui'
import { downloadUrl } from '../../lib/download'
import { fullDate, plural } from '../../lib/format'
import { syncNow } from '../../sync/engine'

const CONFIRM_WORD = 'REMPLACER'

interface ArchiveHeader {
  format: string
  version: number
  exported_at?: string
  counts?: Record<string, number>
}

/** Table, libellé au singulier, au pluriel. */
const COUNT_LABELS: [string, string, string][] = [
  ['feeds', 'flux', 'flux'],
  ['categories', 'dossier', 'dossiers'],
  ['entries', 'article', 'articles'],
  ['summaries', 'résumé', 'résumés'],
  ['smart_feeds', 'flux intelligent', 'flux intelligents'],
  ['transcripts', 'transcription', 'transcriptions'],
]

/** Première ligne décompressée, sans lire tout le fichier. */
async function readHeader(file: File): Promise<ArchiveHeader> {
  const reader = file.stream().pipeThrough(new DecompressionStream('gzip')).pipeThrough(new TextDecoderStream()).getReader()
  let text = ''
  try {
    while (!text.includes('\n') && text.length < 1_000_000) {
      const { value, done } = await reader.read()
      if (done) break
      text += value
    }
  } catch {
    throw new Error('Fichier illisible : ce n’est pas une sauvegarde Fluxus (.jsonl.gz)')
  } finally {
    void reader.cancel().catch(() => undefined)
  }
  let header: ArchiveHeader
  try {
    header = JSON.parse(text.split('\n')[0]) as ArchiveHeader
  } catch {
    throw new Error('Fichier illisible : ce n’est pas une sauvegarde Fluxus (.jsonl.gz)')
  }
  if (header.format !== 'fluxus-export') throw new Error('Ce fichier n’est pas une sauvegarde Fluxus')
  return header
}

export function BackupSection({ onImported }: { onImported?: () => void }) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [exporting, setExporting] = useState(false)
  const [pending, setPending] = useState<{ file: File; header: ArchiveHeader } | null>(null)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [importing, setImporting] = useState(false)

  async function exportAll() {
    setExporting(true)
    try {
      // Lien à usage unique : le fichier peut peser des centaines de Mo, le navigateur le télécharge
      // directement plutôt que de le garder en mémoire dans l'onglet.
      const { url } = unwrap(await api().POST('/api/backup/export-link'))
      downloadUrl(apiUrl(url))
      toast('Téléchargement de la sauvegarde lancé', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Export impossible', 'error')
    } finally {
      setExporting(false)
    }
  }

  async function pick(file: File) {
    if (fileRef.current) fileRef.current.value = ''
    try {
      setPending({ file, header: await readHeader(file) })
      setPassword('')
      setConfirm('')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Fichier illisible', 'error')
    }
  }

  async function runImport() {
    if (!pending) return
    setImporting(true)
    try {
      const form = new FormData()
      form.append('file', pending.file)
      form.append('password', password)
      form.append('confirm', confirm)
      const res = await fetch(apiUrl('/api/backup/import'), { method: 'POST', headers: authHeaders(), body: form })
      const data = (await res.json().catch(() => ({}))) as Partial<Schemas['ImportResult']> & { detail?: string }
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Import impossible')
      setPending(null)
      toast(`Sauvegarde importée : ${data.counts?.feeds ?? 0} flux, ${data.counts?.entries ?? 0} articles`, 'success')
      // L'époque du serveur a changé : la synchronisation vide le cache de cet appareil et recharge tout.
      await syncNow()
      onImported?.()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Import impossible', 'error')
    } finally {
      setImporting(false)
    }
  }

  const counts = pending?.header.counts ?? {}
  const ready = password.length > 0 && confirm.trim().toUpperCase() === CONFIRM_WORD

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void exportAll()} loading={exporting} disabled={isCapacitor()} data-testid="backup-export">
          <DatabaseBackup className="size-4" /> Exporter toute la base
        </Button>
        <Button onClick={() => fileRef.current?.click()} data-testid="backup-import">
          <Upload className="size-4" /> Importer une sauvegarde…
        </Button>
        <input ref={fileRef} type="file" accept=".gz,application/gzip" className="hidden" data-testid="backup-file" onChange={(e) => e.target.files?.[0] && void pick(e.target.files[0])} />
      </div>
      <p className="text-xs text-muted">
        Un seul fichier (.jsonl.gz) : dossiers, flux et leurs réglages, articles avec leur état lu et favori, résumés, transcriptions, flux intelligents, tableau de bord et réglages. Le mot de passe, les appareils connectés et les clés API ne sont jamais inclus : saisissez les clés à nouveau sur la nouvelle instance.
        {isCapacitor() && ' L’export se fait depuis la version web.'}
      </p>

      <Dialog
        open={pending !== null}
        onClose={() => !importing && setPending(null)}
        title="Remplacer le contenu de cette instance"
        width="max-w-lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPending(null)} disabled={importing}>
              Annuler
            </Button>
            <Button variant="danger" onClick={() => void runImport()} loading={importing} disabled={!ready} data-testid="backup-confirm">
              Tout remplacer
            </Button>
          </>
        }
      >
        {pending && (
          <div className="space-y-4 text-sm">
            <div className="rounded-lg bg-muted px-3 py-2">
              <p className="font-medium text-app">{pending.file.name}</p>
              {pending.header.exported_at && <p className="text-xs text-muted">Exportée {fullDate(pending.header.exported_at)}</p>}
              <p className="mt-1 text-xs text-muted" data-testid="backup-counts">
                {COUNT_LABELS.filter(([k]) => counts[k] !== undefined)
                  .map(([k, one, many]) => `${counts[k]} ${plural(counts[k], one, many)}`)
                  .join(' · ')}
              </p>
            </div>
            <div className="flex gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-100">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <p>
                Tout le contenu actuel de cette instance (flux, articles, résumés, flux intelligents, réglages) sera <strong>supprimé et remplacé</strong>. Le mot de passe, les appareils connectés et les clés API sont conservés. Chaque appareil rechargera ses données ; les changements pas encore synchronisés seront perdus.
              </p>
            </div>
            <Field label="Mot de passe actuel">
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" data-testid="backup-password" />
            </Field>
            <Field label={`Tapez ${CONFIRM_WORD} pour confirmer`}>
              <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" data-testid="backup-confirm-word" />
            </Field>
          </div>
        )}
      </Dialog>
    </>
  )
}
