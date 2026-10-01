import * as React from 'react'

import { cn } from '@/lib/utils'

/** Placeholder block shown while content loads. */
function Skeleton({ className, ...props }: React.ComponentPropsWithoutRef<'div'>) {
  return (
    <div
      aria-hidden="true"
      data-slot="skeleton"
      className={cn('animate-pulse rounded-md bg-muted', className)}
      {...props}
    />
  )
}
Skeleton.displayName = 'Skeleton'

export { Skeleton }
