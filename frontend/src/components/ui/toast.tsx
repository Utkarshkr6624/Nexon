import { cva, type VariantProps } from 'class-variance-authority'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import * as React from 'react'

import { cn } from '@/lib/utils'
import type { ToastVariant } from '@/stores/toast-store'

/**
 * The variant map stays module-local: the fast-refresh checker only tolerates a
 * cva export that has been whitelisted by name, and this file's whitelist entry
 * would be a lint-config change outside this component's ownership.
 */
const toastVariants = cva(
  [
    'pointer-events-auto relative flex w-full items-start gap-3 overflow-hidden rounded-lg',
    'border bg-popover p-4 pr-10 text-popover-foreground shadow-lg',
    'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-bottom-2',
    'data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-bottom-2',
  ],
  {
    variants: {
      variant: {
        default: 'border-border',
        // A saturated status rail rather than a tinted panel: the surface stays
        // readable at any severity and the colour is a glance, not a wash.
        success: 'border-success/40 border-l-4 border-l-success',
        warning: 'border-warning/40 border-l-4 border-l-warning',
        destructive: 'border-destructive/40 border-l-4 border-l-destructive',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

const STATUS_ICON: Record<ToastVariant, LucideIcon> = {
  default: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
}

const STATUS_ICON_CLASS: Record<ToastVariant, string> = {
  default: 'text-muted-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
}

export interface ToastProps
  extends React.ComponentPropsWithoutRef<'div'>,
    VariantProps<typeof toastVariants> {}

const Toast = React.forwardRef<HTMLDivElement, ToastProps>(
  ({ className, variant, children, ...props }, ref) => {
    // `VariantProps` admits null as well as undefined, so the status rail and
    // its icon are resolved from one normalised variant.
    const tone: ToastVariant = variant ?? 'default'
    const StatusIcon = STATUS_ICON[tone]
    return (
      <div ref={ref} className={cn(toastVariants({ variant: tone }), className)} {...props}>
        <StatusIcon
          aria-hidden="true"
          className={cn('mt-0.5 h-4 w-4 shrink-0', STATUS_ICON_CLASS[tone])}
        />
        {children}
      </div>
    )
  },
)
Toast.displayName = 'Toast'

const ToastTitle = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('text-sm font-semibold leading-snug tracking-tight', className)}
      {...props}
    />
  ),
)
ToastTitle.displayName = 'ToastTitle'

const ToastDescription = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('text-sm leading-snug text-muted-foreground', className)}
      {...props}
    />
  ),
)
ToastDescription.displayName = 'ToastDescription'

export interface ToastActionProps extends React.ComponentPropsWithoutRef<'button'> {
  label: string
  onClick: () => void
}

/**
 * A toast's single call to action. Invoking it never dismisses the toast on its
 * own — the owner of the record decides, because an action usually leads
 * somewhere the toast is still describing.
 */
const ToastAction = React.forwardRef<HTMLButtonElement, ToastActionProps>(
  ({ className, label, onClick, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      className={cn(
        'mt-1 self-start text-sm font-medium text-primary underline-offset-4 hover:underline',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        className,
      )}
      {...props}
    >
      {label}
    </button>
  ),
)
ToastAction.displayName = 'ToastAction'

const ToastClose = React.forwardRef<HTMLButtonElement, React.ComponentPropsWithoutRef<'button'>>(
  ({ className, children, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      className={cn(
        'absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground',
        'transition-colors hover:bg-accent hover:text-accent-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        className,
      )}
      {...props}
    >
      {children ?? <X aria-hidden="true" className="h-4 w-4" />}
    </button>
  ),
)
ToastClose.displayName = 'ToastClose'

export { Toast, ToastAction, ToastClose, ToastDescription, ToastTitle }
