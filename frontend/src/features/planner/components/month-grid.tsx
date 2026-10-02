import { ChevronLeft, ChevronRight, Flag, TriangleAlert } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCalendarDate, shiftDate, startOfWeek } from '@/features/planner/datetime'
import { cn } from '@/lib/utils'
import { WEEKDAY_SHORT_LABELS, plannerToday } from '@/types/planner'
import type { ApiError } from '@/lib/api-client'
import type { DateOnlyString, PlannerDay } from '@/types/planner'

/** The most a single cell draws before it collapses into a "+N more". */
const MAX_CHIPS = 3

/** Six rows always, so stepping between months does not change the page height. */
function monthGridDates(month: string): DateOnlyString[] {
  const first = startOfWeek(`${month}-01`)
  return Array.from({ length: 42 }, (_, index) => shiftDate(first, index))
}

function monthLabel(month: string): string {
  return formatCalendarDate(`${month}-01`, { month: 'long', year: 'numeric' })
}

interface Chip {
  key: string
  label: string
  tone: 'deadline' | 'event' | 'session'
  settled: boolean
}

export interface MonthGridProps {
  /** `YYYY-MM`. */
  month: string
  timeZone: string
  days: PlannerDay[] | undefined
  isLoading?: boolean
  error?: ApiError | null
  onRetry?: () => void
  /** Due dates keyed by local day, from `useTasks`. */
  deadlines?: Map<string, { id: string; title: string }[]>
  selectedDate: DateOnlyString
  onSelectDate: (date: DateOnlyString) => void
  onShiftMonth: (amount: number) => void
}

/**
 * A month at a glance.
 *
 * **Cap, do not shrink.** A busy Tuesday can hold a dozen things, and a cell
 * that renders all twelve is unreadable at every size. Each cell draws three,
 * says how many it left out, and the day view holds the rest — hierarchy first:
 * the date, then whether the day is in trouble, then what is on it.
 */
export function MonthGrid({
  month,
  timeZone,
  days,
  isLoading = false,
  error = null,
  onRetry,
  deadlines,
  selectedDate,
  onSelectDate,
  onShiftMonth,
}: MonthGridProps) {
  const grid = monthGridDates(month)
  const lookup = new Map((days ?? []).map((day) => [day.date, day]))
  const today = plannerToday(timeZone)

  return (
    <section aria-label={`Calendar for ${monthLabel(month)}`} className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">{monthLabel(month)}</h2>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="outline"
            className="size-8"
            aria-label="Previous month"
            onClick={() => onShiftMonth(-1)}
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button size="sm" variant="outline" onClick={() => onSelectDate(today)}>
            Today
          </Button>
          <Button
            size="icon"
            variant="outline"
            className="size-8"
            aria-label="Next month"
            onClick={() => onShiftMonth(1)}
          >
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={onRetry} title="The month could not be loaded" />
      ) : (
        <>
          <div className="hidden grid-cols-7 gap-1 sm:grid" aria-hidden="true">
            {WEEKDAY_SHORT_LABELS.map((label) => (
              <p key={label} className="px-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {label}
              </p>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-1">
            {grid.map((dateKey) => {
              const day = lookup.get(dateKey)
              const inMonth = dateKey.startsWith(month)
              const due = deadlines?.get(dateKey) ?? []

              const chips: Chip[] = [
                // Deadlines first: they are the only thing on the cell that can
                // change what happens today.
                ...due.map((task) => ({
                  key: `d-${task.id}`,
                  label: task.title,
                  tone: 'deadline' as const,
                  settled: false,
                })),
                ...(day?.events ?? [])
                  .filter((event) => !event.all_day)
                  .map((event) => ({
                    key: `e-${event.id}`,
                    label: event.title,
                    tone: 'event' as const,
                    settled: event.completed_at !== null,
                  })),
                ...(day?.sessions ?? []).map((session) => ({
                  key: `s-${session.id}`,
                  label: session.status === 'active' ? 'Focus block · running' : 'Focus block',
                  tone: 'session' as const,
                  settled: session.status === 'completed',
                })),
              ]

              const shown = chips.slice(0, MAX_CHIPS)
              const hidden = chips.length - shown.length
              const selected = dateKey === selectedDate
              const isToday = dateKey === today

              return (
                <button
                  key={dateKey}
                  type="button"
                  onClick={() => onSelectDate(dateKey)}
                  aria-current={selected ? 'date' : undefined}
                  aria-label={`${formatCalendarDate(dateKey)}${day?.overloaded ? ', overloaded' : ''}`}
                  className={cn(
                    'flex min-h-16 flex-col gap-1 rounded-md border p-1 text-left transition-colors sm:min-h-24 sm:p-1.5',
                    selected ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/40',
                    !inMonth && 'opacity-45',
                    day?.overloaded && 'border-destructive/40 bg-destructive/[0.04]',
                  )}
                >
                  <span className="flex items-center justify-between gap-1">
                    <span
                      className={cn(
                        'text-xs tabular-nums',
                        isToday ? 'font-semibold text-primary' : 'text-muted-foreground',
                      )}
                    >
                      {Number(dateKey.slice(8, 10))}
                    </span>
                    {day?.overloaded && (
                      <TriangleAlert
                        aria-label="Overloaded"
                        className="size-3 shrink-0 text-destructive"
                      />
                    )}
                  </span>

                  {isLoading ? (
                    <span className="hidden space-y-1 sm:block" aria-hidden="true">
                      <Skeleton className="h-2.5 w-full" />
                      <Skeleton className="h-2.5 w-3/4" />
                    </span>
                  ) : (
                    <>
                      <span className="hidden min-h-0 flex-1 space-y-0.5 sm:block">
                        {shown.map((chip) => (
                          <span
                            key={chip.key}
                            className={cn(
                              'flex items-center gap-1 truncate rounded px-1 py-0.5 text-[10px] leading-tight',
                              chip.tone === 'deadline' && 'bg-destructive/10 text-destructive',
                              chip.tone === 'event' && 'bg-secondary text-secondary-foreground',
                              chip.tone === 'session' && 'bg-primary/10 text-primary',
                              chip.settled && 'opacity-60 line-through',
                            )}
                          >
                            {chip.tone === 'deadline' && <Flag aria-hidden="true" className="size-2.5 shrink-0" />}
                            {chip.label}
                          </span>
                        ))}
                        {hidden > 0 && (
                          <span className="block px-1 text-[10px] leading-tight text-muted-foreground">
                            +{hidden} more
                          </span>
                        )}
                      </span>

                      {/* A phone never sees a squeezed grid, so the count carries
                          the day instead of three unreadable chips. */}
                      <span className="text-[10px] text-muted-foreground sm:hidden">
                        {chips.length > 0 ? `${chips.length} item${chips.length === 1 ? '' : 's'}` : '—'}
                      </span>
                    </>
                  )}
                </button>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}