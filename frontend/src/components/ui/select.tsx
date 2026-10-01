import { ChevronDown } from 'lucide-react'
import * as React from 'react'

import { cn } from '@/lib/utils'

export type SelectProps = React.ComponentPropsWithoutRef<'select'>

/**
 * A styled wrapper around the NATIVE `<select>`, not a listbox reimplementation.
 *
 * Rebuilding the popup from scratch would mean re-deriving keyboard navigation,
 * type-ahead, screen-reader announcements, the mobile picker and the OS
 * dropdown — all of which the platform already gets right and all of which a
 * hand-rolled version does worse. Only the chrome is ours: the platform arrow is
 * suppressed with `appearance-none` and replaced by a decorative chevron.
 *
 * Children are plain `<option>` elements; there is deliberately no `SelectOption`
 * wrapper, because that indirection would only add a component without adding
 * any behaviour a native option does not already have.
 *
 * The chevron is absolutely positioned, so the control always needs the relative
 * wrapper below it and takes its width from the parent.
 */
const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => (
    <span className="relative block w-full">
      <select
        ref={ref}
        className={cn(
          'flex h-9 w-full appearance-none rounded-md border border-input bg-background px-3 py-1 pr-9 text-sm shadow-sm transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </span>
  ),
)
Select.displayName = 'Select'

export { Select }
