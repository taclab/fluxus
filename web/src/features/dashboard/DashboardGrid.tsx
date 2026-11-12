import {
  closestCorners,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, type SortingStrategy } from '@dnd-kit/sortable'
import clsx from 'clsx'
import { GripVertical } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import type { View } from '../../app/view'
import { useDashboardData } from './dashboardContext'
import { isKnownWidget, WIDTH_CLASS, widgetTitle, type AnyWidget, type Widget, type WidgetWidth } from './layout'
import { WidgetFrame, type WidgetActions } from './WidgetFrame'
import { NewsWidget } from './widgets/NewsWidget'
import { StarredWidget } from './widgets/StarredWidget'
import { StatsWidget } from './widgets/StatsWidget'
import { SummariesWidget } from './widgets/SummariesWidget'
import { YoutubeWidget } from './widgets/YoutubeWidget'

/**
 * Stratégie neutre : dnd-kit ne déplace rien lui-même. On réordonne réellement le tableau pendant
 * le survol, la grille CSS reflue donc exactement malgré les largeurs mixtes (2/3/6 colonnes).
 */
const noTransform: SortingStrategy = () => null

interface Props {
  widgets: AnyWidget[]
  editing: boolean
  onReorder: (widgets: AnyWidget[]) => void
  onResize: (id: string, width: WidgetWidth) => void
  onConfigure: (widget: Widget) => void
  onRemove: (id: string) => void
  onOpenEntry: (view: View, id: number) => void
  onOpenView: (view: View) => void
}

const announcements = {
  onDragStart: ({ active }: { active: { id: string | number } }) => `Widget ${active.id} saisi.`,
  onDragOver: ({ over }: { over: { id: string | number } | null }) => (over ? `Déplacé vers la position de ${over.id}.` : 'Hors de la grille.'),
  onDragEnd: ({ over }: { over: { id: string | number } | null }) => (over ? 'Widget déposé.' : 'Déplacement annulé.'),
  onDragCancel: () => 'Déplacement annulé.',
}

export function DashboardGrid({ widgets, editing, onReorder, onResize, onConfigure, onRemove, onOpenEntry, onOpenView }: Props) {
  const [draft, setDraft] = useState<AnyWidget[] | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const snapshot = useRef<AnyWidget[] | null>(null)
  const lastOverId = useRef<string | null>(null)
  const items = draft ?? widgets

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  useEffect(() => {
    if (!editing) setDraft(null)
  }, [editing])

  const onDragStart = useCallback(
    (e: DragStartEvent) => {
      snapshot.current = widgets
      lastOverId.current = null
      setActiveId(String(e.active.id))
      setDraft(widgets)
    },
    [widgets],
  )

  const onDragOver = useCallback((e: DragOverEvent) => {
    const overId = e.over ? String(e.over.id) : null
    if (!overId || overId === String(e.active.id) || overId === lastOverId.current) return
    lastOverId.current = overId
    setDraft((cur) => {
      const list = cur ?? []
      const from = list.findIndex((w) => w.id === String(e.active.id))
      const to = list.findIndex((w) => w.id === overId)
      if (from === -1 || to === -1) return cur
      return arrayMove(list, from, to)
    })
  }, [])

  const onDragEnd = useCallback(
    (_e: DragEndEvent) => {
      setActiveId(null)
      if (draft && draft.some((w, i) => w.id !== widgets[i]?.id)) onReorder(draft)
      setDraft(null)
      snapshot.current = null
    },
    [draft, widgets, onReorder],
  )

  const onDragCancel = useCallback(() => {
    setActiveId(null)
    setDraft(null)
    snapshot.current = null
  }, [])

  const move = (id: string, delta: -1 | 1) => {
    const from = widgets.findIndex((w) => w.id === id)
    const to = from + delta
    if (from === -1 || to < 0 || to >= widgets.length) return
    onReorder(arrayMove(widgets, from, to))
  }

  const active = activeId ? items.find((w) => w.id === activeId) : undefined

  return (
    <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart} onDragOver={onDragOver} onDragEnd={onDragEnd} onDragCancel={onDragCancel} accessibility={{ announcements }}>
      <SortableContext items={items.map((w) => w.id)} strategy={noTransform}>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-6">
          {items.map((w, index) => (
            <SortableWidget
              key={w.id}
              widget={w}
              editing={editing}
              dragging={w.id === activeId}
              actions={{
                onMoveUp: index > 0 ? () => move(w.id, -1) : undefined,
                onMoveDown: index < items.length - 1 ? () => move(w.id, 1) : undefined,
                onResize: (width) => onResize(w.id, width),
                onConfigure: isKnownWidget(w) ? () => onConfigure(w) : undefined,
                onRemove: () => onRemove(w.id),
              }}
              onOpenEntry={onOpenEntry}
              onOpenView={onOpenView}
            />
          ))}
        </div>
      </SortableContext>
      <DragOverlay dropAnimation={null}>{active ? <OverlayCard widget={active} /> : null}</DragOverlay>
    </DndContext>
  )
}

function SortableWidget({ widget, editing, dragging, actions, onOpenEntry, onOpenView }: { widget: AnyWidget; editing: boolean; dragging: boolean; actions: WidgetActions; onOpenEntry: Props['onOpenEntry']; onOpenView: Props['onOpenView'] }) {
  const { setNodeRef, setActivatorNodeRef, attributes, listeners } = useSortable({ id: widget.id, disabled: !editing, transition: null })
  const handleProps = { ref: setActivatorNodeRef, ...attributes, ...listeners } as React.ComponentProps<typeof WidgetFrame>['handleProps']
  return (
    <div ref={setNodeRef} className={clsx('col-span-1 min-w-0', WIDTH_CLASS[widget.width])}>
      <WidgetSwitch widget={widget} editing={editing} dragging={dragging} actions={actions} handleProps={handleProps} onOpenEntry={onOpenEntry} onOpenView={onOpenView} />
    </div>
  )
}

function WidgetSwitch({ widget, editing, dragging, actions, handleProps, onOpenEntry, onOpenView }: { widget: AnyWidget; editing: boolean; dragging: boolean; actions: WidgetActions; handleProps: React.ComponentProps<typeof WidgetFrame>['handleProps']; onOpenEntry: Props['onOpenEntry']; onOpenView: Props['onOpenView'] }): ReactNode {
  if (!isKnownWidget(widget)) {
    return (
      <WidgetFrame widgetId={widget.id} widgetType={widget.type} title={widget.title ?? 'Widget non pris en charge'} editing={editing} width={widget.width} actions={{ onMoveUp: actions.onMoveUp, onMoveDown: actions.onMoveDown, onRemove: actions.onRemove }} handleProps={handleProps} dragging={dragging}>
        <p className="p-6 text-center text-sm text-muted">Ce widget a été créé par une version plus récente de Fluxus.</p>
      </WidgetFrame>
    )
  }
  const common = { editing, dragging, actions, handleProps, onOpenEntry, onOpenView }
  switch (widget.type) {
    case 'news':
      return <NewsWidget widget={widget} {...common} />
    case 'youtube':
      return <YoutubeWidget widget={widget} {...common} />
    case 'starred':
      return <StarredWidget widget={widget} {...common} />
    case 'summaries':
      return <SummariesWidget widget={widget} editing={editing} dragging={dragging} actions={actions} handleProps={handleProps} onOpenEntry={onOpenEntry} />
    case 'stats':
      return <StatsWidget widget={widget} editing={editing} dragging={dragging} actions={actions} handleProps={handleProps} onOpenView={onOpenView} />
  }
}

/** Fantôme compact affiché sous le pointeur pendant le déplacement. */
function OverlayCard({ widget }: { widget: AnyWidget }) {
  const data = useDashboardData()
  const { title } = widgetTitle(widget, data.categoryMap, data.feedMap)
  return (
    <div className="flex items-center gap-2 rounded-2xl border border-accent-300 bg-panel px-4 py-3 shadow-xl text-app w-72">
      <GripVertical className="size-4 text-muted" />
      <span className="truncate text-sm font-semibold">{title}</span>
    </div>
  )
}
