import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * Placeholder rows for the Risk Center list.
 *
 * **The skeleton is the card's silhouette, not a generic block.** A list that
 * swaps three grey rectangles for three full cards moves everything on the page
 * down the instant the data lands — the header row, the pagination, the scroll
 * position — which is the exact jolt the analytics surface's `ChartShell` was
 * built to avoid by fixing its body height. Here the fix is to reserve the same
 * number of vertical blocks the real card occupies: a title, a description, a
 * meter, two evidence rows and a button row.
 *
 * **It is announced once, not per row.** `role="status"` with a single
 * visually-hidden sentence means a screen-reader user hears "Loading detected
 * risks" rather than the same message three times, and `aria-busy` on the
 * container tells assistive technology the region is still settling.
 *
 * **Nothing here reads as a value.** No bars, no zero counters, no severity
 * chips — a pulse in the shape of a count is a count to anyone glancing at it,
 * and this surface's whole point is that an absent finding is not a zero.
 */

export interface RiskListSkeletonProps {
  /** How many card placeholders to draw. Defaults to the common page size. */
  count?: number
  className?: string
}

export function RiskListSkeleton({ count = 3, className }: RiskListSkeletonProps) {
  const rows = Math.max(1, count)

  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading detected risks</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="rounded-lg border border-border bg-card p-6"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-4/5" />
            </div>
            <Skeleton className="h-5 w-20 shrink-0 rounded-md" />
          </div>

          <div className="mt-4 space-y-2">
            <Skeleton className="h-1.5 w-full" />
          </div>

          <div className="mt-4 space-y-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-12 w-full rounded-md" />
            <Skeleton className="h-12 w-full rounded-md" />
          </div>

          <div className="mt-4 flex gap-2">
            <Skeleton className="h-8 w-28 rounded-md" />
            <Skeleton className="h-8 w-32 rounded-md" />
          </div>
        </div>
      ))}
    </div>
  )
}
