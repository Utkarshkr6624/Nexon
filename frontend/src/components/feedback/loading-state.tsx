import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'

export interface LoadingStateProps {
  label?: string
  description?: string
  className?: string
  compact?: boolean
}

/** Inline busy indicator for a region that is already framed by its container. */
export function LoadingState({
  label = 'Loading',
  description,
  className,
  compact = false,
}: LoadingStateProps) {
  return (
    <div
      role="status"
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-2 py-6' : 'gap-3 py-16',
        className,
      )}
    >
      <Spinner size={compact ? 'default' : 'lg'} label={label} />
      <p className="text-sm font-medium text-foreground">{label}</p>
      {description && (
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

/**
 * Placeholder skeleton for a lazily loaded route. Mirrors the shape of a real
 * page — header, stat row, content card — so the transition into the loaded
 * route does not move the layout.
 */
export function RouteFallback() {
  return (
    <div className="app-container space-y-6 py-6" aria-busy="true">
      <div className="space-y-2">
        <div className="h-6 w-48 animate-pulse rounded-md bg-muted" />
        <div className="h-4 w-80 max-w-full animate-pulse rounded-md bg-muted" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Card key={index}>
            <CardHeader className="pb-3">
              <div className="h-3 w-24 animate-pulse rounded bg-muted" />
            </CardHeader>
            <CardContent>
              <div className="h-7 w-16 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardContent className="pt-6">
          <div className="h-40 animate-pulse rounded-md bg-muted" />
        </CardContent>
      </Card>
    </div>
  )
}
