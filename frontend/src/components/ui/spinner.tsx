import { cva, type VariantProps } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import * as React from 'react'

import { cn } from '@/lib/utils'

const spinnerVariants = cva('animate-spin text-muted-foreground', {
  variants: {
    size: {
      sm: 'h-3.5 w-3.5',
      default: 'h-4 w-4',
      lg: 'h-6 w-6',
    },
  },
  defaultVariants: {
    size: 'default',
  },
})

export interface SpinnerProps
  extends Omit<React.ComponentPropsWithoutRef<'svg'>, 'children'>,
    VariantProps<typeof spinnerVariants> {
  /** Accessible label; the spinner itself is decorative. */
  label?: string
}

const Spinner = React.forwardRef<SVGSVGElement, SpinnerProps>(
  ({ className, size, label = 'Loading', ...props }, ref) => (
    <span role="status" className="inline-flex items-center">
      <Loader2
        ref={ref}
        aria-hidden="true"
        className={cn(spinnerVariants({ size }), className)}
        {...props}
      />
      <span className="sr-only">{label}</span>
    </span>
  ),
)
Spinner.displayName = 'Spinner'

export { Spinner, spinnerVariants }
