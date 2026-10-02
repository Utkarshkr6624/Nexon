import { Suspense, lazy } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * Charts, loaded on demand.
 *
 * **Recharts is ~250 ms of module evaluation**, and the dashboard needs it only
 * for the panels *below* the headline row — never for the greeting, the window
 * control or the health card. Importing it eagerly put that cost on the first
 * paint of every dashboard load, which is long enough to matter on a slow
 * machine: the route's content appeared late enough that a test waiting on the
 * page heading could miss it. Deferring keeps the first paint to the components
 * that carry it.
 *
 * The fallbacks below mirror the real cards' shape, so a chart arriving late
 * fills a hole rather than moving the page.
 */
export const TrendChart = lazy(() =>
  import('@/features/analytics/components/trend-chart').then((module) => ({
    default: module.TrendChart,
  })),
)

export const AnalyticsBarChart = lazy(() =>
  import('@/features/analytics/components/bar-chart').then((module) => ({
    default: module.AnalyticsBarChart,
  })),
)

export const TimeDistributionChart = lazy(() =>
  import('@/features/analytics/components/time-distribution-chart').then((module) => ({
    default: module.TimeDistributionChart,
  })),
)

/** The same frame the real chart occupies, so nothing reflows when it lands. */
export function ChartFallback({
  title,
  subtitle,
  className,
}: {
  title: string
  subtitle?: string
  className?: string
}) {
  return (
    <Card className={cn('min-w-0', className)} aria-busy="true">
      <CardHeader className="pb-4">
        <CardTitle>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>
      <CardContent>
        <Skeleton className="h-64 w-full" />
      </CardContent>
    </Card>
  )
}

/** One boundary per chart: an error in one panel must not empty the page. */
export function LazyChart({
  title,
  subtitle,
  className,
  children,
}: {
  title: string
  subtitle?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <Suspense fallback={<ChartFallback title={title} subtitle={subtitle} className={className} />}>
      {children}
    </Suspense>
  )
}
