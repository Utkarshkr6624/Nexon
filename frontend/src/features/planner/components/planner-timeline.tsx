import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import type { LucideIcon } from 'lucide-react'
import { CalendarOff, Timer } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { cn } from '@/lib/utils'
import { formatTime, minutesSinceMidnight } from '@/features/planner/datetime'
import { EVENT_TYPE_META, WORK_SESSION_STATUS_META } from '@/types/planner'
import type { ApiError } from '@/lib/api-client'
import type { CalendarEvent, PlannerDay, WorkSession } from '@/types/planner'

/** Pixels per hour. Fixed so the axis reads as a clock rather than stretching. */
const HOUR_PX = 56
/** A block shorter than this is still drawn at this height — 5 minutes is real. */
const MIN_BLOCK_PX = 14
/**
 * Columns of side-by-side blocks before the rest move to the overflow list.
 * Past this the labels stop being readable, and unreadable overlap is worse than
 * an honest "+N more".
 */
const MAX_LANES = 4
const MIN_SPAN_HOURS = 8

/** Events read as solid commitments; sessions as the work inside them. */
const EVENT_BLOCK =
  'border-l-4 border-primary bg-secondary text-secondary-foreground hover:bg-accent'
const SESSION_BLOCK =
  'border-dashed border-primary/70 bg-primary/10 text-foreground hover:bg-primary/15'

export interface PlannerTimelineProps {
  day?: PlannerDay
  timeZone: string
  /** Draws the "now" line. Only true for the day actually being viewed. */
  isToday?: boolean
  /** Overrides the derived visible range. */
  dayStartHour?: number
  dayEndHour?: number
  onSelectEvent?: (event: CalendarEvent) => void
  onSelectSession?: (session: WorkSession) => void
  isLoading?: boolean
  error?: ApiError | null
  onRetry?: () => void
  className?: string
}

interface Block {
  key: string
  kind: 'event' | 'session'
  label: string
  detail: string
  icon: LucideIcon
  startMinute: number
  endMinute: number
  event?: CalendarEvent
  session?: WorkSession
}

interface Placed extends Block {
  lane: number
  lanes: number
}

/**
 * Greedy lane packing, then a cluster pass to size the blocks.
 *
 * Sorted by start, each block takes the leftmost column whose previous occupant
 * has already ended, so overlapping blocks sit side by side rather than on top
 * of each other. A *cluster* is a maximal run of blocks that overlap
 * transitively; every block in one shares the cluster's column count, which is
 * what makes a single block in a quiet stretch render full-width. Once a cluster
 * needs more than {@link MAX_LANES} columns the remainder is returned as
 * overflow rather than squeezed into a width nothing can be read at.
 */
function place(blocks: Block[]): { placed: Placed[]; overflow: Placed[] } {
  const sorted = [...blocks].sort((a, b) => a.startMinute - b.startMinute || a.endMinute - b.endMinute)
  const assigned: Placed[] = []
  const overflow: Placed[] = []
  const laneEnds: number[] = []

  for (const block of sorted) {
    let lane = laneEnds.findIndex((end) => end <= block.startMinute)
    if (lane === -1) {
      if (laneEnds.length >= MAX_LANES) {
        overflow.push({ ...block, lane: 0, lanes: 1 })
        continue
      }
      lane = laneEnds.length
      laneEnds.push(block.endMinute)
    } else {
      laneEnds[lane] = block.endMinute
    }
    assigned.push({ ...block, lane, lanes: 1 })
  }

  let cluster: Placed[] = []
  let clusterEnd = Number.NEGATIVE_INFINITY
  const size = (): void => {
    const lanes = new Set(cluster.map((item) => item.lane)).size
    for (const item of cluster) item.lanes = Math.max(lanes, 1)
  }
  for (const item of assigned) {
    if (cluster.length > 0 && item.startMinute >= clusterEnd) {
      size()
      cluster = []
    }
    cluster.push(item)
    clusterEnd = Math.max(clusterEnd, item.endMinute)
  }
  if (cluster.length > 0) size()

  return { placed: assigned, overflow }
}

/**
 * The day as a time axis: hour gridlines, positioned blocks, and a now-line.
 *
 * Events and sessions are distinguished by **colour, by icon and by the word in
 * the label**, because a schedule readable only by hue is unreadable to a
 * meaningful share of the people using it — and the two are different kinds of
 * thing: a meeting blocks the slot, a focus block is the work inside it.
 */
export function PlannerTimeline({
  day,
  timeZone,
  isToday = false,
  dayStartHour,
  dayEndHour,
  onSelectEvent,
  onSelectSession,
  isLoading = false,
  error = null,
  onRetry,
  className,
}: PlannerTimelineProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!isToday) return
    const handle = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(handle)
  }, [isToday])

  const blocks = useMemo<Block[]>(() => {
    const events = (day?.events ?? []).map((event): Block => {
      const meta = EVENT_TYPE_META[event.event_type] ?? EVENT_TYPE_META.other
      return {
        key: `event:${event.id}`,
        kind: 'event',
        label: event.title,
        detail: event.all_day
          ? `${meta.label} · all day`
          : `${meta.label} · ${formatTime(event.starts_at, timeZone)} – ${formatTime(event.ends_at, timeZone)}`,
        icon: meta.icon,
        startMinute: minutesSinceMidnight(event.starts_at, timeZone),
        endMinute: minutesSinceMidnight(event.ends_at, timeZone),
        event,
      }
    })

    const sessions = (day?.sessions ?? []).map((session): Block => {
      const meta = WORK_SESSION_STATUS_META[session.status]
      return {
        key: `session:${session.id}`,
        kind: 'session',
        // The word is in the label, not only in the colour.
        label: `Focus block · ${meta.label}`,
        detail: `${formatTime(session.scheduled_start, timeZone)} – ${formatTime(session.scheduled_end, timeZone)}`,
        icon: session.status === 'active' ? Timer : meta.icon,
        startMinute: minutesSinceMidnight(session.scheduled_start, timeZone),
        endMinute: minutesSinceMidnight(session.scheduled_end, timeZone),
        session,
      }
    })

    return [...events, ...sessions]
  }, [day, timeZone])

  const { placed, overflow } = useMemo(() => place(blocks), [blocks])

  const hours = useMemo(() => {
    const earliest = blocks.length ? Math.min(...blocks.map((b) => b.startMinute)) : 9 * 60
    const latest = blocks.length ? Math.max(...blocks.map((b) => b.endMinute)) : 18 * 60
    const start = dayStartHour ?? Math.max(0, Math.floor(earliest / 60) - 1)
    const end =
      dayEndHour ?? Math.min(24, Math.max(Math.ceil(latest / 60) + 1, start + MIN_SPAN_HOURS))
    return { start, end: Math.max(end, start + 1) }
  }, [blocks, dayStartHour, dayEndHour])

  if (isLoading) return <LoadingState label="Loading the day" compact />
  if (error) return <ErrorState error={error} onRetry={onRetry} compact />
  if (!day) return null

  const spanMinutes = (hours.end - hours.start) * 60
  const height = (hours.end - hours.start) * HOUR_PX
  const toPixels = (minute: number) => ((minute - hours.start * 60) / spanMinutes) * height
  const nowMinute = isToday ? minutesSinceMidnight(new Date(now).toISOString(), timeZone) : null

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="flex gap-3">
        {/* Hour gutter, outside the scroll area so it stays put. */}
        <div className="flex w-12 shrink-0 flex-col" style={{ height }}>
          {Array.from({ length: hours.end - hours.start }, (_, index) => (
            <span
              key={index}
              className="pr-2 text-right text-[11px] tabular-nums text-muted-foreground"
              style={{ height: HOUR_PX, lineHeight: `${HOUR_PX}px` }}
            >
              {String(hours.start + index).padStart(2, '0')}:00
            </span>
          ))}
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto" style={{ maxHeight: HOUR_PX * 12 }}>
          <div className="relative w-full" style={{ height }}>
            {Array.from({ length: hours.end - hours.start }, (_, index) => (
              <div
                key={index}
                aria-hidden="true"
                className="absolute inset-x-0 border-t border-border/70"
                style={{ top: index * HOUR_PX }}
              />
            ))}

            {nowMinute !== null &&
              nowMinute >= hours.start * 60 &&
              nowMinute <= hours.end * 60 && (
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-destructive"
                  style={{ top: toPixels(nowMinute) }}
                >
                  <span className="absolute -left-1 -top-1 size-2 rounded-full bg-destructive" />
                </div>
              )}

            {placed.map((item) => (
              <TimelineBlock
                key={item.key}
                item={item}
                top={toPixels(item.startMinute)}
                height={Math.max(toPixels(item.endMinute) - toPixels(item.startMinute), MIN_BLOCK_PX)}
                onSelect={() =>
                  item.event ? onSelectEvent?.(item.event) : onSelectSession?.(item.session as WorkSession)
                }
                interactive={Boolean(item.event ? onSelectEvent : onSelectSession)}
              />
            ))}
          </div>
        </div>
      </div>

      {blocks.length === 0 && (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <CalendarOff className="size-4" aria-hidden="true" />
          Nothing on this day yet. The hours are still shown so you can see where it is empty.
        </p>
      )}

      {overflow.length > 0 && (
        <div className="rounded-md border border-border bg-muted/40 p-2">
          <p className="text-xs font-medium text-foreground">
            {overflow.length} more at the same time
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Moved off the axis so their labels stay readable.
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {overflow.map((item) => (
              <li
                key={item.key}
                className={cn(
                  'flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]',
                  item.kind === 'session' ? SESSION_BLOCK : EVENT_BLOCK,
                )}
              >
                <item.icon className="size-3 shrink-0" aria-hidden="true" />
                <span className="truncate">{item.label}</span>
                <span className="opacity-70">{item.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function TimelineBlock({
  item,
  top,
  height,
  onSelect,
  interactive,
}: {
  item: Placed
  top: number
  height: number
  onSelect: () => void
  interactive: boolean
}) {
  const width = 100 / item.lanes
  const style: CSSProperties = {
    top,
    height,
    left: `${item.lane * width}%`,
    width: `calc(${width}% - 4px)`,
  }
  const Icon = item.icon
  const shared = item.kind === 'session' ? SESSION_BLOCK : EVENT_BLOCK

  const content = (
    <>
      <span className="flex min-w-0 items-center gap-1">
        <Icon className="size-3 shrink-0" aria-hidden="true" />
        <span className="truncate text-[11px] font-medium">{item.label}</span>
      </span>
      {height >= 26 && <span className="mt-0.5 block truncate text-[10px] opacity-80">{item.detail}</span>}
    </>
  )

  if (!interactive) {
    return (
      <div
        style={style}
        className={cn('absolute z-10 overflow-hidden rounded-md border px-1.5 py-1', shared)}
      >
        {content}
      </div>
    )
  }

  return (
    <button
      type="button"
      style={style}
      onClick={onSelect}
      aria-label={`${item.label}, ${item.detail}`}
      className={cn(
        'absolute z-10 overflow-hidden rounded-md border px-1.5 py-1 text-left',
        shared,
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      )}
    >
      {content}
    </button>
  )
}