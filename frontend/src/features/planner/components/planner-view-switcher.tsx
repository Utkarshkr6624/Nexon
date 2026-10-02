import { CalendarDays, ChevronLeft, ChevronRight, Globe } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { addMonths, formatCalendarDate, monthOf, shiftDate, startOfWeek, todayInZone } from '@/features/planner/datetime'
import { cn } from '@/lib/utils'
import type { PlannerView } from '@/types/planner'

const VIEWS: Array<{ value: PlannerView; label: string }> = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
]

export interface PlannerViewSwitcherProps {
  view: PlannerView
  onViewChange: (view: PlannerView) => void
  /** Always a `YYYY-MM-DD` anchor; the month view reads its first seven. */
  anchor: string
  onAnchorChange: (anchor: string) => void
  /** The zone every day boundary is computed in. */
  timeZone: string
  className?: string
}

/**
 * Which slice of time is on screen, and which one you are looking at.
 *
 * **The zone is shown, not assumed.** A day here is local midnight to the next
 * local midnight in `timeZone`, so a calendar that silently used the browser's
 * zone would put a 23:30 session on the wrong day for anyone travelling — and
 * there would be no error to explain it.
 *
 * Navigation steps by the view's own unit: a day, a week, or a month, so "next"
 * means the next thing of the kind being looked at rather than always 24 hours.
 */
export function PlannerViewSwitcher({
  view,
  onViewChange,
  anchor,
  onAnchorChange,
  timeZone,
  className,
}: PlannerViewSwitcherProps) {
  const today = todayInZone(timeZone)
  const isToday = anchor === today

  function step(direction: 1 | -1) {
    if (view === 'day') return shiftDate(anchor, direction)
    if (view === 'week') return shiftDate(anchor, 7 * direction)
    // Month navigation lands on the 1st so the grid does not keep the old day's
    // offset and open mid-week.
    return `${addMonths(monthOf(anchor), direction)}-01`
  }

  const label =
    view === 'day'
      ? formatCalendarDate(anchor, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
      : view === 'week'
        ? `${formatCalendarDate(startOfWeek(anchor))} – ${formatCalendarDate(shiftDate(startOfWeek(anchor), 6), { day: 'numeric', month: 'short' })}`
        : formatCalendarDate(`${monthOf(anchor)}-01`, { month: 'long', year: 'numeric' })

  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-3', className)}>
      <div
        role="group"
        aria-label="Planner view"
        className="inline-flex items-center gap-1 rounded-md border border-border p-0.5"
      >
        {VIEWS.map((entry) => (
          <Button
            key={entry.value}
            size="sm"
            variant={view === entry.value ? 'secondary' : 'ghost'}
            aria-pressed={view === entry.value}
            onClick={() => onViewChange(entry.value)}
          >
            {entry.value === 'day' && <CalendarDays aria-hidden="true" />}
            {entry.label}
          </Button>
        ))}
      </div>

      <div className="flex items-center gap-1.5">
        <Button
          size="icon"
          variant="outline"
          aria-label={view === 'day' ? 'Previous day' : view === 'week' ? 'Previous week' : 'Previous month'}
          onClick={() => onAnchorChange(step(-1))}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Button size="sm" variant="outline" disabled={isToday} onClick={() => onAnchorChange(today)}>
          Today
        </Button>
        <Button
          size="icon"
          variant="outline"
          aria-label={view === 'day' ? 'Next day' : view === 'week' ? 'Next week' : 'Next month'}
          onClick={() => onAnchorChange(step(1))}
        >
          <ChevronRight aria-hidden="true" />
        </Button>

        <p className="ml-2 text-sm font-medium text-foreground">{label}</p>
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Globe className="size-3.5" aria-hidden="true" />
          {timeZone}
        </p>
      </div>
    </div>
  )
}