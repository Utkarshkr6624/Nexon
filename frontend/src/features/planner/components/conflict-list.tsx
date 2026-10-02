import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatCalendarDate } from '@/features/planner/datetime'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import { CONFLICT_KIND_META, CONFLICT_SEVERITY_META } from '@/types/planner'
import type { ApiError } from '@/lib/api-client'
import type { Conflict, ConflictSeverity, PlannerWindow } from '@/types/planner'

const SEVERITY_ALERT_VARIANT = {
  info: 'default',
  warning: 'warning',
  error: 'destructive',
} as const satisfies Record<ConflictSeverity, 'default' | 'warning' | 'destructive'>

export interface ConflictListProps {
  conflicts: Conflict[]
  /** The span the answer was computed for, so "nothing found" is bounded. */
  window?: PlannerWindow | null
  isLoading?: boolean
  error?: ApiError | null
  onRetry?: () => void
  /** Jump to the event or session the conflict is about. */
  onSelectEntity?: (conflict: Conflict) => void
  className?: string
}

/** Values as evidence are rendered as a short, non-fabricated form. */
function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') {
    // Intervals arrive as ISO instants; show only the time, not the UTC date.
    return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)
      ? value.slice(11, 16)
      : value
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/**
 * What is wrong with a schedule, with the evidence behind each claim.
 *
 * **Severity is an icon, a word and a colour** — never colour alone. An empty
 * list is stated as "nothing was wrong over this span", not as a blank panel,
 * because a reader who cannot tell the two apart cannot trust either.
 */
export function ConflictList({
  conflicts,
  window: span = null,
  isLoading = false,
  error = null,
  onRetry,
  onSelectEntity,
  className,
}: ConflictListProps) {
  if (isLoading) return <LoadingState label="Checking the schedule" compact />
  if (error) return <ErrorState error={error} onRetry={onRetry} compact />

  if (conflicts.length === 0) {
    return (
      <div className={cn('rounded-lg border border-border p-4', className)}>
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
          No conflicts
        </p>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          {span
            ? `Nothing overlaps, nothing falls outside your declared hours, and no session is booked after its task was due between ${formatCalendarDate(span.start_date)} and ${formatCalendarDate(span.end_date)}.`
            : 'Nothing overlaps, nothing falls outside your declared hours, and no session is booked after its task was due.'}
        </p>
      </div>
    )
  }

  return (
    <ul className={cn('flex flex-col gap-2', className)}>
      {conflicts.map((conflict, index) => {
        const severityMeta = CONFLICT_SEVERITY_META[conflict.severity]
        const SeverityIcon = severityMeta.icon
        return (
          <li key={`${conflict.kind}:${conflict.entity_id ?? index}`}>
            <Alert variant={SEVERITY_ALERT_VARIANT[conflict.severity]}>
              <AlertIcon>
                <SeverityIcon aria-hidden="true" />
              </AlertIcon>
              <div className="min-w-0 flex-1 space-y-2">
                <AlertTitle className="flex flex-wrap items-center gap-2">
                  <span>{severityMeta.label}</span>
                  <Badge variant={TONE_BADGE_VARIANT[CONFLICT_KIND_META[conflict.kind].tone]}>
                    {CONFLICT_KIND_META[conflict.kind].label}
                  </Badge>
                  <span className="text-xs font-normal text-muted-foreground">
                    {conflict.entity_type.replace('_', ' ')}
                  </span>
                </AlertTitle>
                <AlertDescription className="text-foreground/85">
                  {conflict.message}
                </AlertDescription>

                {Object.keys(conflict.evidence).length > 0 && <Evidence evidence={conflict.evidence} />}

                {onSelectEntity && conflict.entity_id && (
                  <Button size="sm" variant="outline" onClick={() => onSelectEntity(conflict)}>
                    Go to {conflict.entity_type === 'work_session' ? 'session' : 'event'}
                  </Button>
                )}
              </div>
            </Alert>
          </li>
        )
      })}
    </ul>
  )
}

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(evidence)

  return (
    <div className="rounded-md border border-border/60 bg-background/60 p-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex items-center gap-1 text-xs font-medium text-foreground"
      >
        {open ? (
          <ChevronDown className="size-3.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3.5" aria-hidden="true" />
        )}
        Evidence ({entries.length})
      </button>
      {open && (
        <dl className="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
          {entries.map(([key, value]) => (
            <div key={key} className="flex min-w-0 justify-between gap-2">
              <dt className="shrink-0 text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
              <dd className="min-w-0 truncate text-right tabular-nums text-foreground">
                {evidenceValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}