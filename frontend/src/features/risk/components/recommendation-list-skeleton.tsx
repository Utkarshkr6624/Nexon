import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * Placeholder rows for the recommendation list.
 *
 * **A separate component from {@link RiskListSkeleton}, not a parameterised
 * one.** The two cards are genuinely different shapes — a recommendation has a
 * WHY paragraph and a shorter action block where a risk has a variable-length
 * evidence list — and a shared skeleton with boolean flags would have to grow a
 * flag for every difference, at which point it is two components wearing a
 * trench coat. The reasoning is otherwise identical: same announcement, same
 * `aria-busy`, same refusal to draw a pulse that reads as a count.
 *
 * The suggestion rows here are slightly denser than the risk placeholders
 * because a suggestion is a fixed, two-paragraph card, so the placeholder can
 * match it exactly and the list cannot jump when the answers arrive.
 */

export interface RecommendationListSkeletonProps {
  /** How many card placeholders to draw. Defaults to the common page size. */
  count?: number
  className?: string
}

export function RecommendationListSkeleton({
  count = 3,
  className,
}: RecommendationListSkeletonProps) {
  const rows = Math.max(1, count)

  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading suggestions</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="rounded-lg border border-border bg-card p-6"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-28" />
            </div>
            <Skeleton className="h-5 w-16 shrink-0 rounded-md" />
          </div>

          <div className="mt-4 space-y-2">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-3/4" />
          </div>

          <div className="mt-4 space-y-2">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-3 w-full" />
          </div>

          <div className="mt-4 flex gap-2">
            <Skeleton className="h-8 w-20 rounded-md" />
            <Skeleton className="h-8 w-24 rounded-md" />
          </div>
        </div>
      ))}
    </div>
  )
}
