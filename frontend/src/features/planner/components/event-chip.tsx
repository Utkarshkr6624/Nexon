import { CalendarClock } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatCalendarDate, formatTimeRange } from '@/features/planner/datetime'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { EVENT_TYPE_META } from '@/types/planner'
import type { CalendarEvent } from '@/types/planner'

export interface EventChipProps {
  event: CalendarEvent
  timeZone: string
  /**
   * Makes the chip a button. Omitted when the chip is decoration next to a
   * timeline block that is already the control.
   */
  onSelect?: (event: CalendarEvent) => void
  /** Prefix the time with the calendar date, for the week and month surfaces. */
  showDate?: boolean
  className?: string
}

/**
 * One calendar event as a row: its type, its title and the window it holds.
 *
 * The type is carried by a named icon **and** the label, not by colour alone —
 * the chip has to stay readable where the hue does not survive (a printed week,
 * a colour-vision difference, a muted row).
 */
export function EventChip({ event, timeZone, onSelect, showDate = false, className }: EventChipProps) {
  const meta = EVENT_TYPE_META[event.event_type] ?? EVENT_TYPE_META.other
  const Icon = meta.icon
  const when = showDate
    ? `${formatCalendarDate(event.starts_at.slice(0, 10))} · ${formatTimeRange(event.starts_at, event.ends_at, timeZone)}`
    : event.all_day
      ? `All day · ${formatCalendarDate(event.starts_at.slice(0, 10))}`
      : formatTimeRange(event.starts_at, event.ends_at, timeZone)

  const body = (
    <>
      <Badge variant={TONE_BADGE_VARIANT[meta.tone]} className="shrink-0 gap-1">
        <Icon aria-hidden="true" />
        {meta.label}
      </Badge>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{event.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{when}</span>
      </span>
      {event.location && (
        <span className="hidden shrink-0 truncate text-xs text-muted-foreground sm:block">
          {event.location}
        </span>
      )}
    </>
  )

  if (!onSelect) {
    return (
      <div
        className={cn(
          'flex min-w-0 items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5',
          className,
        )}
      >
        {body}
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(event)}
      className={cn(
        'flex min-w-0 w-full items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 text-left',
        'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      {body}
      <CalendarClock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
    </button>
  )
}