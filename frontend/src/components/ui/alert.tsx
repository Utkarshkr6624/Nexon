/**
 * Inline status message.
 *
 * Hand-rolled in place of `@radix-ui/react-alert`, which is not installed and
 * cannot be added. That package is a one-line `role="alert"` wrapper, so there
 * is nothing to reproduce beyond the live region itself.
 */
import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const alertVariants = cva(
  'relative flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-sm [&>svg]:size-4 [&>svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'border-border bg-card text-card-foreground',
        destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
        warning: 'border-warning/30 bg-warning/10 text-warning',
        success: 'border-success/30 bg-success/10 text-success',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export type AlertVariant = NonNullable<VariantProps<typeof alertVariants>['variant']>

export interface AlertProps
  extends React.ComponentPropsWithoutRef<'div'>,
    VariantProps<typeof alertVariants> {}

const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant, ...props }, ref) => (
    <div
      ref={ref}
      role="alert"
      data-slot="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  ),
)
Alert.displayName = 'Alert'

/** Leading icon slot; a bare `<AlertTriangle />` child works just as well. */
const AlertIcon = React.forwardRef<HTMLSpanElement, React.ComponentPropsWithoutRef<'span'>>(
  ({ className, ...props }, ref) => (
    <span
      ref={ref}
      aria-hidden="true"
      data-slot="alert-icon"
      className={cn('flex size-4 shrink-0 items-center justify-center [&_svg]:size-4', className)}
      {...props}
    />
  ),
)
AlertIcon.displayName = 'AlertIcon'

const AlertTitle = React.forwardRef<HTMLHeadingElement, React.ComponentPropsWithoutRef<'h5'>>(
  ({ className, ...props }, ref) => (
    <h5
      ref={ref}
      data-slot="alert-title"
      className={cn('font-medium leading-none tracking-tight', className)}
      {...props}
    />
  ),
)
AlertTitle.displayName = 'AlertTitle'

const AlertDescription = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="alert-description"
      className={cn('text-sm leading-relaxed [&_p]:leading-relaxed', className)}
      {...props}
    />
  ),
)
AlertDescription.displayName = 'AlertDescription'

export { Alert, AlertDescription, AlertIcon, AlertTitle }