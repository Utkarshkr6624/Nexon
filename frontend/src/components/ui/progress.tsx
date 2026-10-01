/**
 * Progress bar primitive.
 *
 * Hand-rolled in place of `@radix-ui/react-progress`, which is not installed
 * and cannot be added. Radix's contribution here is the `role="progressbar"`
 * plumbing and nothing else, so a plain element reproduces it exactly.
 */
import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const progressVariants = cva('h-full rounded-full transition-[width] duration-300 ease-out', {
  variants: {
    variant: {
      default: 'bg-primary',
      success: 'bg-success',
      warning: 'bg-warning',
      destructive: 'bg-destructive',
    },
  },
  defaultVariants: {
    variant: 'default',
  },
})

export type ProgressVariant = NonNullable<VariantProps<typeof progressVariants>['variant']>

export interface ProgressProps
  extends React.ComponentPropsWithoutRef<'div'>,
    VariantProps<typeof progressVariants> {
  /** Current amount. Clamped to `[0, max]`; non-numeric values read as `0`. */
  value?: number
  max?: number
  /** Escape hatch for colouring the fill independently of `variant`. */
  indicatorClassName?: string
  /** Required for a named bar — the fill alone carries no accessible value. */
  'aria-label'?: string
}

const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
  (
    { className, variant, value = 0, max = 100, indicatorClassName, 'aria-label': ariaLabel, ...props },
    ref,
  ) => {
    // A zero or negative `max` would make the fill percentage meaningless and
    // put `aria-valuemax` below `aria-valuemin`; fall back to a sane range.
    const upperBound = Number.isFinite(max) && max > 0 ? max : 100
    const amount = Number.isFinite(value) ? Math.min(Math.max(value, 0), upperBound) : 0
    const percent = upperBound === 0 ? 0 : (amount / upperBound) * 100

    return (
      <div
        ref={ref}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={upperBound}
        aria-valuenow={amount}
        aria-label={ariaLabel}
        data-slot="progress"
        className={cn('relative h-2 w-full overflow-hidden rounded-full bg-secondary', className)}
        {...props}
      >
        <div
          data-slot="progress-indicator"
          className={cn(progressVariants({ variant }), indicatorClassName)}
          style={{ width: `${percent}%` }}
        />
      </div>
    )
  },
)
Progress.displayName = 'Progress'

export { Progress }