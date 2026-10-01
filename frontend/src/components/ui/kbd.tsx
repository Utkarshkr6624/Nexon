import * as React from 'react'

import { cn } from '@/lib/utils'

/** Keyboard key hint, e.g. `<Kbd>⌘</Kbd><Kbd>K</Kbd>`. */
function Kbd({ className, ...props }: React.ComponentPropsWithoutRef<'kbd'>) {
  return (
    <kbd
      className={cn(
        'pointer-events-none inline-flex h-5 select-none items-center justify-center gap-1 rounded border border-border bg-secondary px-1.5 font-mono text-[0.6875rem] font-medium text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}
Kbd.displayName = 'Kbd'

export { Kbd }
