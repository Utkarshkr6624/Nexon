import { useState } from 'react'
import { Check, ChevronDown, ChevronRight, Sparkles, X } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatCalendarDate, formatTimeRange } from '@/features/planner/datetime'
import { useCreateWorkSession } from '@/features/planner/hooks'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { PlannerSuggestion, WorkSession } from '@/types/planner'

export interface SuggestionCardProps {
  suggestion: PlannerSuggestion
  timeZone: string
  onAccepted?: (session: WorkSession) => void
  onDismissed?: (suggestion: PlannerSuggestion) => void
  className?: string
}

function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/**
 * One proposed slot, with the argument for it.
 *
 * **The reason is rendered verbatim and is not optional.** A suggestion is a
 * claim about someone else's calendar that the user did not write; a slot with
 * no stated reason is indistinguishable from a guess, and a user who books time
 * on one has been given an assertion rather than an argument. The evidence is
 * there so the claim can be checked rather than believed.
 *
 * **Accepting writes a work session and nothing else** — the engine proposes,
 * the user decides. The suggested instants are sent as they arrived, offsets
 * intact, rather than round-tripped through the browser's zone.
 */
export function SuggestionCard({
  suggestion,
  timeZone,
  onAccepted,
  onDismissed,
  className,
}: SuggestionCardProps) {
  const create = useCreateWorkSession()
  const [dismissed, setDismissed] = useState(false)
  const [showEvidence, setShowEvidence] = useState(false)

  if (dismissed) return null

  const evidenceEntries = Object.entries(suggestion.evidence)

  async function accept() {
    try {
      const session = await create.mutateAsync({
        task_id: suggestion.task_id,
        scheduled_start: suggestion.suggested_start,
        scheduled_end: suggestion.suggested_end,
      })
      toast.success('Time booked', `${suggestion.task_title} — ${formatTimeRange(session.scheduled_start, session.scheduled_end, timeZone)}`)
      onAccepted?.(session)
    } catch (cause) {
      toast.error('Could not book that time', toApiError(cause).message)
    }
  }

  function dismiss() {
    setDismissed(true)
    toast.info('Suggestion dismissed', suggestion.task_title)
    onDismissed?.(suggestion)
  }

  return (
    <article
      className={cn(
        'flex flex-col gap-3 rounded-lg border border-border bg-card p-3',
        'focus-within:border-primary/50',
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <p className="flex items-center gap-1.5 truncate text-sm font-medium text-foreground">
            <Sparkles className="size-4 shrink-0 text-primary" aria-hidden="true" />
            {suggestion.task_title}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatCalendarDate(suggestion.suggested_start.slice(0, 10))} ·{' '}
            {formatTimeRange(suggestion.suggested_start, suggestion.suggested_end, timeZone)}
          </p>
        </div>
        <Badge variant="outline" className="shrink-0">
          Proposed
        </Badge>
      </div>

      {/*
        The reason is quoted, not paraphrased: it is the engine's own sentence
        and the only thing that makes the slot arguable.
      */}
      <blockquote className="rounded-md border-l-2 border-primary/60 bg-muted/50 px-3 py-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Why</p>
        <p className="mt-1 text-sm leading-relaxed text-foreground">{suggestion.reason}</p>
      </blockquote>

      {evidenceEntries.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowEvidence((value) => !value)}
            aria-expanded={showEvidence}
            className="flex items-center gap-1 text-xs font-medium text-foreground"
          >
            {showEvidence ? (
              <ChevronDown className="size-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden="true" />
            )}
            What it used ({evidenceEntries.length})
          </button>
          {showEvidence && (
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
              {evidenceEntries.map(([key, value]) => (
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
      )}

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={accept} disabled={create.isPending}>
          <Check aria-hidden="true" />
          {create.isPending ? 'Booking…' : 'Accept'}
        </Button>
        <Button size="sm" variant="ghost" onClick={dismiss} disabled={create.isPending}>
          <X aria-hidden="true" />
          Dismiss
        </Button>
      </div>
    </article>
  )
}