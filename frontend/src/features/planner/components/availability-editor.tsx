import { useMemo, useState } from 'react'
import { Clock, Plus, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { normalizeWallClock, wallClockMinutes } from '@/features/planner/datetime'
import { useAvailability, useReplaceAvailability } from '@/features/planner/hooks'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { WEEKDAY_LABELS, WEEKDAY_SHORT_LABELS } from '@/types/planner'
import type { ApiError } from '@/lib/api-client'
import type { AvailabilityRule, AvailabilityRuleInput, Weekday } from '@/types/planner'

/** A window being edited. Wall-clock, because that is what a weekly rule is. */
interface DraftWindow {
  starts_at: string
  ends_at: string
  label: string
}

type DraftWeek = Record<Weekday, DraftWindow[]>

function emptyWeek(): DraftWeek {
  return {
    0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 6: [],
  }
}

function draftFromRules(rules: AvailabilityRule[]): DraftWeek {
  const week = emptyWeek()
  for (const rule of rules) {
    week[rule.weekday as Weekday].push({
      starts_at: normalizeWallClock(rule.starts_at),
      ends_at: normalizeWallClock(rule.ends_at),
      label: rule.label ?? '',
    })
  }
  for (const day of Object.keys(week) as unknown as Weekday[]) {
    week[day].sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  }
  return week
}

/**
 * Minutes actually covered, with overlapping windows merged.
 *
 * The backend deliberately stores the windows as sent — it does not clip or
 * reorder them — so two windows covering the same hour are both real. Summing
 * them would report an hour the user does not have.
 */
function coveredMinutes(windows: DraftWindow[]): number {
  if (windows.length === 0) return 0
  const ranges = windows
    .map((window) => ({ start: wallClockMinutes('00:00', window.starts_at), end: wallClockMinutes('00:00', window.ends_at) }))
    .sort((a, b) => a.start - b.start)
  let total = 0
  const [first] = ranges
  if (!first) return 0
  let current = first
  for (const range of ranges.slice(1)) {
    if (range.start <= current.end) {
      current = { start: current.start, end: Math.max(current.end, range.end) }
    } else {
      total += current.end - current.start
      current = range
    }
  }
  return total + (current.end - current.start)
}

function formatHours(minutes: number): string {
  if (minutes === 0) return '—'
  const hours = minutes / 60
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(2).replace(/0$/, '')}h`
}

export interface AvailabilityEditorProps {
  /** The zone the wall-clock times are read in. Sent with every request. */
  timeZone: string
  className?: string
}

/**
 * The weekly pattern, replaced wholesale.
 *
 * **`PUT`, not `PATCH`,** so "I no longer work Tuesdays" and "I did not send
 * Tuesday" are the same request — a diffing endpoint would have to guess between
 * them, and the guess is the bug.
 *
 * **An empty week is not "never available".** It means nothing was declared, and
 * that is said in the interface rather than left to be inferred from seven empty
 * rows, because the two readings produce opposite schedules.
 */
export function AvailabilityEditor({ timeZone, className }: AvailabilityEditorProps) {
  const availability = useAvailability(timeZone)

  if (availability.isPending) {
    return (
      <div className={cn('flex flex-col gap-3', className)} aria-busy="true">
        {Array.from({ length: 7 }, (_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (availability.isError) {
    return (
      <ErrorState
        error={availability.error as ApiError}
        onRetry={() => void availability.refetch()}
        title="Could not read your availability"
        compact
      />
    )
  }

  const rules = availability.data?.rules ?? []

  return (
    // Re-keyed on the rule set so a fresh mount always seeds from the server and
    // a cancelled edit can never seed the next one.
    <AvailabilityForm
      key={`${rules.length}:${rules.map((rule) => rule.id).join(',')}`}
      initial={draftFromRules(rules)}
      timeZone={timeZone}
      className={className}
    />
  )
}

function AvailabilityForm({
  initial,
  timeZone,
  className,
}: {
  initial: DraftWeek
  timeZone: string
  className?: string
}) {
  const [week, setWeek] = useState<DraftWeek>(initial)
  const [error, setError] = useState<ApiError | null>(null)
  const save = useReplaceAvailability()

  const totals = useMemo(
    () =>
      (Object.keys(week) as unknown as Weekday[]).map((day) => ({
        day,
        minutes: coveredMinutes(week[day]),
      })),
    [week],
  )
  const totalMinutes = totals.reduce((sum, entry) => sum + entry.minutes, 0)
  const declared = totalMinutes > 0

  function updateWindow(day: Weekday, index: number, patch: Partial<DraftWindow>) {
    setWeek((current) => ({
      ...current,
      [day]: current[day].map((window, position) =>
        position === index ? { ...window, ...patch } : window,
      ),
    }))
    setError(null)
  }

  function addWindow(day: Weekday) {
    setWeek((current) => ({
      ...current,
      [day]: [...current[day], { starts_at: '09:00', ends_at: '17:00', label: '' }],
    }))
    setError(null)
  }

  function removeWindow(day: Weekday, index: number) {
    setWeek((current) => ({ ...current, [day]: current[day].filter((_, position) => position !== index) }))
    setError(null)
  }

  async function onSubmit() {
    const rules: AvailabilityRuleInput[] = []
    for (const day of Object.keys(week) as unknown as Weekday[]) {
      for (const window of week[day]) {
        if (!window.starts_at || !window.ends_at) continue
        // The schema refuses ends_at <= starts_at, so a window that wraps past
        // midnight has to be two windows; say so rather than sending a 422.
        if (window.ends_at <= window.starts_at) {
          setError(
            toApiError(
              new Error(
                `${WEEKDAY_LABELS[day]}: "${window.starts_at} – ${window.ends_at}" ends before it starts. Split an overnight window into two.`,
              ),
            ),
          )
          return
        }
        rules.push({
          weekday: day,
          starts_at: window.starts_at,
          ends_at: window.ends_at,
          label: window.label.trim() || null,
        })
      }
    }

    try {
      await save.mutateAsync({ rules })
      toast.success(
        rules.length === 0 ? 'Availability cleared' : 'Availability saved',
        rules.length === 0
          ? 'Days are no longer measured against declared hours.'
          : `Stored in ${timeZone}.`,
      )
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not save your availability', apiError.message)
    }
  }

  return (
    <form
      className={cn('flex flex-col gap-4', className)}
      onSubmit={(event) => {
        event.preventDefault()
        void onSubmit()
      }}
      noValidate
    >
      <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
        These are the hours you say you are free, stored as wall-clock times and read in{' '}
        <span className="font-medium text-foreground">{timeZone}</span>. Anything scheduled outside
        them is reported as a conflict — the planner will not stop you, but it will name it.
        {declared ? null : (
          <>
            {' '}
            <strong className="font-medium text-foreground">
              Nothing is declared right now, which means no constraint — not that you are never
              available. Overload can only be calculated once there are hours to measure against.
            </strong>
          </>
        )}
      </p>

      {error && (
        <p role="alert" className="app-form-error">
          {error.message}
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {totals.map(({ day, minutes }) => (
          <li key={day} className="rounded-md border border-border p-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor={`availability-day-${day}`} className="font-medium">
                {WEEKDAY_LABELS[day]}
              </Label>
              <span className="flex items-center gap-1.5">
                <Clock className="size-3.5 text-muted-foreground" aria-hidden="true" />
                <span className="text-xs tabular-nums text-muted-foreground">
                  {formatHours(minutes)} declared
                </span>
                {minutes > 0 ? (
                  <Badge variant="success">Scheduled</Badge>
                ) : (
                  <Badge variant="outline">No hours</Badge>
                )}
              </span>
            </div>

            <div className="mt-2 flex flex-col gap-2">
              {week[day].length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No windows. This day carries no constraint.
                </p>
              )}

              {week[day].map((window, index) => (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <div className="app-form-field w-28">
                    {index === 0 && <Label htmlFor={`availability-${day}-${index}-start`}>From</Label>}
                    <Input
                      id={`availability-${day}-${index}-start`}
                      type="time"
                      value={window.starts_at}
                      onChange={(change) => updateWindow(day, index, { starts_at: change.target.value })}
                    />
                  </div>
                  <div className="app-form-field w-28">
                    {index === 0 && <Label htmlFor={`availability-${day}-${index}-end`}>To</Label>}
                    <Input
                      id={`availability-${day}-${index}-end`}
                      type="time"
                      value={window.ends_at}
                      onChange={(change) => updateWindow(day, index, { ends_at: change.target.value })}
                    />
                  </div>
                  <div className="app-form-field min-w-40 flex-1">
                    {index === 0 && (
                      <Label htmlFor={`availability-${day}-${index}-label`} optional>
                        Label
                      </Label>
                    )}
                    <Input
                      id={`availability-${day}-${index}-label`}
                      value={window.label}
                      maxLength={80}
                      placeholder="Core hours"
                      onChange={(change) => updateWindow(day, index, { label: change.target.value })}
                    />
                  </div>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label={`Remove window ${index + 1} on ${WEEKDAY_SHORT_LABELS[day]}`}
                    onClick={() => removeWindow(day, index)}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              ))}

              <div>
                <Button type="button" size="sm" variant="outline" onClick={() => addWindow(day)}>
                  <Plus aria-hidden="true" />
                  Add window
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save the whole week'}
        </Button>
        <span className="text-xs text-muted-foreground">
          {formatHours(totalMinutes)} declared across the week.
        </span>
      </div>
    </form>
  )
}