/**
 * Toggle switch.
 *
 * Hand-rolled in place of `@radix-ui/react-switch`, which is not installed and
 * cannot be added. A native `<button role="switch">` already provides the
 * semantics, the keyboard activation, and the focus behaviour; only the visual
 * track and thumb are added here. Colour comes from `checked`, so a caller
 * cannot paint an "on" switch in the off colour.
 */
import * as React from 'react'

import { cn } from '@/lib/utils'

export interface SwitchProps
  extends Omit<React.ComponentPropsWithoutRef<'button'>, 'onChange' | 'value'> {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  /** Required unless the switch is named by an external `<label>`. */
  'aria-label'?: string
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, onCheckedChange, disabled = false, className, onClick, type, ...props }, ref) => (
    <button
      ref={ref}
      // A native button activates on both Space and Enter, which is exactly the
      // switch contract, so the browser handles the keyboard for us.
      type={type ?? 'button'}
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      data-slot="switch"
      data-state={checked ? 'checked' : 'unchecked'}
      onClick={(event) => {
        onClick?.(event)
        if (!event.defaultPrevented && !disabled) onCheckedChange(!checked)
      }}
      className={cn(
        'inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-input',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        data-slot="switch-thumb"
        className={cn(
          'pointer-events-none block size-4 rounded-full bg-background shadow-sm transition-transform duration-200 ease-out',
          checked ? 'translate-x-4' : 'translate-x-0',
        )}
      />
    </button>
  ),
)
Switch.displayName = 'Switch'

export { Switch }