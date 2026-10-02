import type { ReactNode } from 'react'
import { Info } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { EmptyAnalytics } from '@/features/analytics/components/empty-analytics'
import { formatScore } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ScoredRead } from '@/types/analytics'

/**
 * A score, with the arithmetic that produced it on show.
 *
 * **A score a reader cannot argue with is a black box**, so this renders the
 * three things that make it arguable: every contributor's `points` of
 * `max_points`, the backend's one-sentence explanation for each, and the
 * `formula` string verbatim. The parts are shown as points rather than as a
 * percentage of the total, so they can be added up against the headline and
 * checked.
 *
 * When `available` is false the whole card becomes the `EmptyAnalytics` state:
 * the number is *not* rendered as `0`. `score: null` with a reason is the
 * backend saying it looked and found nothing to measure, which is a different
 * claim from "you scored zero".
 */
export interface ScoreCardProps {
  score: ScoredRead
  /** Supporting figures under the score, e.g. "14 active days of 30". */
  facts?: ReadonlyArray<{ label: string; value: ReactNode }>
  className?: string
  /** Hides the contributor list for a compact secondary placement. */
  compact?: boolean
}

export function ScoreCard({ score, facts, className, compact = false }: ScoreCardProps) {
  const unavailable = !score.available || score.score === null

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-3">
        <CardTitle>{score.label}</CardTitle>
        <CardDescription>
          {unavailable ? 'Not measurable for this window.' : score.disclaimer}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {unavailable ? (
          <EmptyAnalytics
            metric={metricFor(score)}
            reason={score.reason_if_unavailable}
            className="px-0 py-4"
          />
        ) : (
          <>
            <p className="text-4xl font-semibold tabular-nums tracking-tight text-foreground">
              {formatScore(score.score)}
              <span className="ml-1 text-lg font-normal text-muted-foreground">/ 100</span>
            </p>

            {facts && facts.length > 0 && (
              <dl className="flex flex-wrap gap-x-6 gap-y-1">
                {facts.map((fact) => (
                  <div key={fact.label} className="flex items-baseline gap-1.5">
                    <dt className="text-xs text-muted-foreground">{fact.label}</dt>
                    <dd className="text-sm font-medium tabular-nums text-foreground">
                      {fact.value}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            {!compact && score.components.length > 0 && (
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  Contributors
                </p>
                <ul className="space-y-3">
                  {score.components.map((component) => (
                    <li key={component.name} className="space-y-1.5">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="min-w-0 truncate text-sm font-medium text-foreground">
                          {component.name}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                          {component.points} of {component.max_points}
                        </span>
                      </div>
                      <Progress
                        value={component.points}
                        max={component.max_points || 1}
                        aria-label={`${component.name}: ${component.points} of ${component.max_points} points`}
                      />
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {component.explanation}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {score.formula && (
              <div className="space-y-1.5 rounded-md border border-border bg-muted/50 p-3">
                <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  <Info className="size-3" aria-hidden="true" />
                  How this was computed
                </p>
                {/* Verbatim: the backend owns the wording of its own formula. */}
                <p className="text-xs leading-relaxed text-foreground/80">{score.formula}</p>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Which empty-state copy belongs to which score.
 *
 * The three reads are structurally identical where it matters, so the metric is
 * named by the label the backend sends rather than by a prop every call site
 * would have to remember to set.
 */
function metricFor(score: ScoredRead): 'productivity' | 'consistency' | 'focus' {
  // A payload missing its label must not blank the page: this runs on the
  // unavailable path, which is exactly the path a partial response takes.
  const label = String(score.label ?? '').toLowerCase()
  if (label.includes('consistency')) return 'consistency'
  if (label.includes('focus')) return 'focus'
  return 'productivity'
}