import { useEffect, useState } from 'react'
import { CircleCheck, PauseCircle, Pencil, Play, Square, Timer, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatMinutes, formatTimeRange } from '@/features/planner/datetime'
import { useWorkSessionTimer } from '@/features/planner/hooks'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { WORK_SESSION_STATUS_META } from '@/types/planner'
import type { WorkSession } from '@/types/planner'

/** How often a running timer redraws. Minutes are the displayed unit. */
const TICK_MS = 30_000

export interface WorkSessionCardProps {
  session: WorkSession
  timeZone: string
  /** Resolved by the caller; the session carries only the id. */
  taskTitle?: string
  onEdit?: (session: WorkSession) => void
  onDelete?: (session: WorkSession) => void
  className?: string
}

/**
 * A reserved block of work, with the clock that measures it.
 *
 * **The elapsed figure is a preview, never an assertion.** The minutes the
 * backend records come from the database clock on `stop`; what is shown while a
 * session runs is `actual_minutes` (already accumulated) plus the time since
 * `actual_start` read from this browser, so the number on screen can disagree
 * with what the stop will write — by design, since only one of the two is
 * authoritative.
 */
export function WorkSessionCard({
  session,
  timeZone,
  taskTitle,
  onEdit,
  onDelete,
  className,
}: WorkSessionCardProps) {
  const meta = WORK_SESSION_STATUS_META[session.status]
  const StatusIcon = meta.icon
  const timer = useWorkSessionTimer()
  const [tick, setTick] = useState(() => Date.now())

  const running = session.status === 'active'
  useEffect(() => {
    if (!running) return
    const handle = window.setInterval(() => setTick(Date.now()), TICK_MS)
    return () => window.clearInterval(handle)
  }, [running])

  const liveMinutes =
    running && session.actual_start ? Math.max(0, (tick - new Date(session.actual_start).getTime()) / 60_000) : 0
  const elapsed = Math.round(session.actual_minutes + liveMinutes)

  async function transition(next: 'start' | 'stop') {
    try {
      const saved = await timer.mutateAsync({ id: session.id, transition: next })
      toast.success(
        next === 'start' ? 'Timer started' : 'Timer stopped',
        `${formatMinutes(saved.actual_minutes)} recorded on this session.`,
      )
    } catch (cause) {
      const error = toApiError(cause)
      toast.error(next === 'start' ? 'Could not start the timer' : 'Could not stop the timer', error.message)
    }
  }

  const canStart = session.status === 'planned'
  const canStop = session.status === 'active'

  return (
    <article
      className={cn(
        'flex flex-col gap-3 rounded-lg border bg-card p-3',
        running ? 'border-primary/50' : 'border-border',
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <p className="truncate text-sm font-medium text-foreground">
            {taskTitle ?? (session.task_id ? 'Scheduled work' : 'Scheduled work — no task')}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatTimeRange(session.scheduled_start, session.scheduled_end, timeZone)}
            {session.estimated_minutes !== null && ` · estimated ${formatMinutes(session.estimated_minutes)}`}
          </p>
        </div>
        <Badge variant={TONE_BADGE_VARIANT[meta.tone]} className="shrink-0 gap-1">
          <StatusIcon aria-hidden="true" />
          {meta.label}
        </Badge>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p
          className={cn(
            'flex items-center gap-1.5 text-sm tabular-nums',
            running ? 'font-semibold text-primary' : 'text-muted-foreground',
          )}
        >
          <Timer className="size-4" aria-hidden="true" />
          <span className="sr-only">Elapsed</span>
          {formatMinutes(elapsed)}
          {running && <span className="text-xs font-normal text-muted-foreground">running</span>}
        </p>

        <div className="flex items-center gap-2">
          {canStart && (
            <Button size="sm" onClick={() => transition('start')} disabled={timer.isPending}>
              <Play aria-hidden="true" />
              Start
            </Button>
          )}
          {canStop && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => transition('stop')}
              disabled={timer.isPending}
            >
              <Square aria-hidden="true" />
              Stop
            </Button>
          )}
          {session.status === 'completed' && (
            <span className="flex items-center gap-1 text-xs text-success">
              <CircleCheck className="size-3.5" aria-hidden="true" />
              Recorded
            </span>
          )}
          {session.status === 'cancelled' && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <PauseCircle className="size-3.5" aria-hidden="true" />
              Slot released
            </span>
          )}
          {onEdit && (
            <Button size="icon" variant="ghost" aria-label="Edit session" onClick={() => onEdit(session)}>
              <Pencil aria-hidden="true" />
            </Button>
          )}
          {onDelete && (
            <Button
              size="icon"
              variant="ghost"
              aria-label="Delete session"
              onClick={() => onDelete(session)}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </article>
  )
}