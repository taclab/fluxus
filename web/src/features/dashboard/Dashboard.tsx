import clsx from 'clsx'
import { Check, Menu, Plus, RefreshCw, Search, SlidersHorizontal } from 'lucide-react'
import { useCallback, useState } from 'react'

import type { View } from '../../app/view'
import { useView } from '../../app/view'
import { useToast } from '../../components/feedback'
import { Button, IconButton } from '../../components/ui'
import { syncNow } from '../../sync/engine'
import { useT } from '../../i18n'
import { longDay } from '../../lib/format'
import { useSyncState } from '../../sync/useSync'
import { DashboardDataProvider } from './DashboardData'
import { useDashboardData } from './dashboardContext'
import { DashboardGrid } from './DashboardGrid'
import { type AnyWidget, type Widget, type WidgetWidth } from './layout'
import { useDashboardLayout } from './useDashboardLayout'
import { WidgetConfigDialog } from './WidgetConfigDialog'

interface Props {
  onOpenSidebar: () => void
  onOpenEntry: (view: View, id: number) => void
  onSearch: () => void
}

export function Dashboard(props: Props) {
  return (
    <DashboardDataProvider>
      <DashboardInner {...props} />
    </DashboardDataProvider>
  )
}

function DashboardInner({ onOpenSidebar, onOpenEntry, onSearch }: Props) {
  const data = useDashboardData()
  const toast = useToast()
  const t = useT()
  const td = t.dashboard
  const { setView } = useView()
  const sync = useSyncState()
  const { layout, isDefault, readOnly, loading, save } = useDashboardLayout(data.categories, data.feeds, (m) => toast(m))
  const [editing, setEditing] = useState(false)
  const [config, setConfig] = useState<{ open: boolean; widget: Widget | null }>({ open: false, widget: null })

  const widgets = layout.widgets
  const commit = useCallback(
    (next: AnyWidget[]) => {
      if (readOnly) {
        toast(td.readOnlyToast, 'error')
        return
      }
      save(next)
    },
    [save, readOnly, toast, td],
  )

  const onResize = (id: string, width: WidgetWidth) => commit(widgets.map((w) => (w.id === id ? { ...w, width } : w)))
  const onRemove = (id: string) => commit(widgets.filter((w) => w.id !== id))
  const onSubmitConfig = (w: Widget) => {
    const exists = widgets.some((x) => x.id === w.id)
    commit(exists ? widgets.map((x) => (x.id === w.id ? w : x)) : [...widgets, w])
    setConfig({ open: false, widget: null })
  }

  const today = longDay()

  return (
    <section className="flex h-full flex-col bg-app">
      <header className="flex h-14 shrink-0 items-center gap-1 border-b border-app bg-panel px-2">
        <IconButton label={td.menu} onClick={onOpenSidebar} className="lg:hidden">
          <Menu className="size-5" />
        </IconButton>
        <div className="min-w-0 flex-1 px-2">
          <h1 className="truncate font-semibold text-app leading-tight">{td.title}</h1>
          <p className="truncate text-[11px] text-muted capitalize leading-tight">{today}</p>
        </div>
        <IconButton label={td.search} size="sm" onClick={onSearch}>
          <Search className="size-4" />
        </IconButton>
        <IconButton label={td.sync} size="sm" onClick={() => void syncNow()} disabled={sync.status === 'syncing'}>
          <RefreshCw className={clsx('size-4', sync.status === 'syncing' && 'animate-spin')} />
        </IconButton>
        {editing && (
          <Button size="sm" variant="secondary" onClick={() => setConfig({ open: true, widget: null })} data-testid="add-widget">
            <Plus className="size-4" /> <span className="hidden sm:inline">{td.addWidget}</span>
          </Button>
        )}
        <Button size="sm" variant={editing ? 'primary' : 'ghost'} onClick={() => setEditing((e) => !e)} data-testid="toggle-edit" disabled={readOnly}>
          {editing ? <Check className="size-4" /> : <SlidersHorizontal className="size-4" />}
          <span className="hidden sm:inline">{editing ? td.done : td.customize}</span>
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1600px] p-4 space-y-4">
          {readOnly && (
            <p className="rounded-xl border border-amber-300/60 bg-amber-50/60 px-4 py-2 text-sm text-amber-800 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-200">
              {td.readOnlyNotice}
            </p>
          )}
          {editing && isDefault && (
            <p className="rounded-xl border border-app bg-panel px-4 py-2 text-sm text-muted">
              {td.defaultNotice}
            </p>
          )}
          {!loading && (
            <DashboardGrid
              widgets={widgets}
              editing={editing}
              onReorder={commit}
              onResize={onResize}
              onConfigure={(w) => setConfig({ open: true, widget: w })}
              onRemove={onRemove}
              onOpenEntry={onOpenEntry}
              onOpenView={setView}
            />
          )}
          {!loading && widgets.length === 0 && (
            <div className="flex flex-col items-center gap-3 py-16 text-center text-muted">
              <p>{td.noWidgets}</p>
              <Button variant="primary" onClick={() => setConfig({ open: true, widget: null })}>
                <Plus className="size-4" /> {td.addWidget}
              </Button>
            </div>
          )}
        </div>
      </div>

      <WidgetConfigDialog open={config.open} widget={config.widget} onClose={() => setConfig({ open: false, widget: null })} onSubmit={onSubmitConfig} />
    </section>
  )
}
