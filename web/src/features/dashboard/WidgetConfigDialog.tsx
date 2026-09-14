import { useEffect, useState } from 'react'

import { Button, Dialog, Field, Input, Select } from '../../components/ui'
import { useDashboardData } from './DashboardData'
import { blankWidget, DISPLAY_LABEL, KNOWN_TYPES, TYPE_LABEL, WIDTHS, WIDTH_LABEL, type NewsDisplay, type Widget, type WidgetType, type WidgetWidth } from './layout'

interface Props {
  open: boolean
  /** Widget à modifier, ou null pour une création. */
  widget: Widget | null
  onClose: () => void
  onSubmit: (widget: Widget) => void
}

export function WidgetConfigDialog({ open, widget, onClose, onSubmit }: Props) {
  const data = useDashboardData()
  const [draft, setDraft] = useState<Widget>(() => widget ?? blankWidget('news'))

  useEffect(() => {
    if (open) setDraft(widget ?? blankWidget('news'))
  }, [open, widget])

  const changeType = (type: WidgetType) => {
    if (type === draft.type) return
    const fresh = blankWidget(type)
    setDraft({ ...fresh, id: draft.id, width: draft.width, ...(draft.title ? { title: draft.title } : {}) } as Widget)
  }

  const feedsSorted = [...data.feeds].sort((a, b) => a.title.localeCompare(b.title, 'fr'))
  const limitOptions = [3, 5, 8, 10, 12, 16, 20, 30]

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={widget ? 'Configurer le widget' : 'Ajouter un widget'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button variant="primary" onClick={() => onSubmit(draft)} data-testid="widget-submit">
            {widget ? 'Enregistrer' : 'Ajouter'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Type">
          <Select value={draft.type} onChange={(e) => changeType(e.target.value as WidgetType)} data-testid="widget-type">
            {KNOWN_TYPES.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </Select>
        </Field>

        {draft.type === 'news' && (
          <>
            <Field label="Source">
              <Select
                value={draft.source.kind === 'all' ? 'all' : `${draft.source.kind}:${draft.source.id}`}
                onChange={(e) => {
                  const v = e.target.value
                  const source = v === 'all' ? ({ kind: 'all' } as const) : v.startsWith('category:') ? ({ kind: 'category', id: Number(v.slice(9)) } as const) : ({ kind: 'feed', id: Number(v.slice(5)) } as const)
                  setDraft({ ...draft, source })
                }}
                data-testid="widget-source"
              >
                <option value="all">Tous les articles</option>
                {data.categories.length > 0 && (
                  <optgroup label="Catégories">
                    {data.categories.map((c) => (
                      <option key={c.id} value={`category:${c.id}`}>
                        {c.title}
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="Flux">
                  {feedsSorted.map((f) => (
                    <option key={f.id} value={`feed:${f.id}`}>
                      {f.title || f.url}
                    </option>
                  ))}
                </optgroup>
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Affichage">
                <Select value={draft.display} onChange={(e) => setDraft({ ...draft, display: e.target.value as NewsDisplay })}>
                  {(Object.keys(DISPLAY_LABEL) as NewsDisplay[]).map((d) => (
                    <option key={d} value={d}>
                      {DISPLAY_LABEL[d]}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Nombre d’articles">
                <Select value={draft.limit} onChange={(e) => setDraft({ ...draft, limit: Number(e.target.value) })}>
                  {limitOptions.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.unreadOnly} onChange={(e) => setDraft({ ...draft, unreadOnly: e.target.checked })} className="size-4 accent-accent-600" />
              Non lus seulement
            </label>
          </>
        )}

        {draft.type === 'youtube' && (
          <>
            <Field label="Source">
              <Select
                value={draft.source.kind === 'all_youtube' ? 'all' : String(draft.source.id)}
                onChange={(e) => setDraft({ ...draft, source: e.target.value === 'all' ? { kind: 'all_youtube' } : { kind: 'category', id: Number(e.target.value) } })}
              >
                <option value="all">Toutes les chaînes YouTube</option>
                {data.categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Nombre de vidéos">
                <Select value={draft.limit} onChange={(e) => setDraft({ ...draft, limit: Number(e.target.value) })}>
                  {[4, 6, 8, 12, 16, 24].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </Select>
              </Field>
              <label className="flex items-center gap-2 self-end pb-2.5 text-sm">
                <input type="checkbox" checked={draft.unreadOnly} onChange={(e) => setDraft({ ...draft, unreadOnly: e.target.checked })} className="size-4 accent-accent-600" />
                Non vues seulement
              </label>
            </div>
          </>
        )}

        {(draft.type === 'starred' || draft.type === 'summaries') && (
          <Field label="Nombre d’éléments">
            <Select value={draft.limit} onChange={(e) => setDraft({ ...draft, limit: Number(e.target.value) })}>
              {limitOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </Field>
        )}

        {draft.type === 'stats' && (
          <fieldset className="space-y-2 text-sm">
            <legend className="text-sm font-medium">Afficher</legend>
            {(
              [
                ['unreadByCategory', 'Non lus par catégorie'],
                ['readToday', 'Lus aujourd’hui'],
                ['feedErrors', 'Flux en erreur'],
                ['tokens', 'Résumés et tokens consommés'],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2">
                <input type="checkbox" checked={draft.show[key]} onChange={(e) => setDraft({ ...draft, show: { ...draft.show, [key]: e.target.checked } })} className="size-4 accent-accent-600" />
                {label}
              </label>
            ))}
          </fieldset>
        )}

        <div className="grid grid-cols-[1fr_auto] gap-3">
          <Field label="Titre personnalisé (optionnel)">
            <Input value={draft.title ?? ''} onChange={(e) => setDraft({ ...draft, title: e.target.value || undefined })} placeholder="Titre automatique" />
          </Field>
          <Field label="Largeur">
            <div className="flex h-10 items-center gap-1">
              {WIDTHS.map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setDraft({ ...draft, width: w as WidgetWidth })}
                  className={`h-9 min-w-10 rounded-lg px-2 text-sm font-medium ${draft.width === w ? 'bg-accent-600 text-white' : 'border border-app hover:bg-muted'}`}
                  aria-pressed={draft.width === w}
                >
                  {WIDTH_LABEL[w]}
                </button>
              ))}
            </div>
          </Field>
        </div>
      </div>
    </Dialog>
  )
}
