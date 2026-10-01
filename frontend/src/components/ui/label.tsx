import * as LabelPrimitive from '@radix-ui/react-label'
import * as React from 'react'

import { cn } from '@/lib/utils'

export interface LabelProps
  extends React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root> {
  /**
   * Marks a field as not required. The muted affordance is the opposite of a
   * required marker: on a form where most fields are mandatory, a word costs
   * less than an asterisk, and a screen reader announces it as part of the
   * label rather than as a separate symbol.
   */
  optional?: boolean
}

const Label = React.forwardRef<React.ComponentRef<typeof LabelPrimitive.Root>, LabelProps>(
  ({ className, optional = false, children, ...props }, ref) => (
    <LabelPrimitive.Root
      ref={ref}
      className={cn(
        'text-sm font-medium leading-none text-foreground peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
        className,
      )}
      {...props}
    >
      {children}
      {optional ? (
        <span className="ml-1.5 text-xs font-normal text-muted-foreground">Optional</span>
      ) : null}
    </LabelPrimitive.Root>
  ),
)
Label.displayName = LabelPrimitive.Root.displayName

export { Label }
