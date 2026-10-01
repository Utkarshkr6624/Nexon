/**
 * Modal dialog primitives.
 *
 * Hand-rolled in place of `@radix-ui/react-dialog`, which is not installed and
 * cannot be added: the project builds offline with a frozen dependency set.
 * Radix would normally supply everything behavioural a modal owes the user, and
 * all of it is reimplemented here explicitly rather than approximated with CSS:
 *
 * - focus movement into the panel on open, a Tab/Shift+Tab trap while open, and
 *   focus restoration to the opener on close;
 * - Escape and outside-click dismissal, with only the innermost dialog
 *   responding so stacked dialogs do not all close at once;
 * - the body scroll lock, and the `aria-labelledby`/`aria-describedby` wiring
 *   between the panel and its title and description.
 *
 * The trade is deliberate: more code in this one file in exchange for no new
 * dependency. Keep it that way — this is not an oversight to be "fixed" by
 * installing the primitive later.
 */
import { Slot } from '@radix-ui/react-slot'
import { X } from 'lucide-react'
import * as React from 'react'
import { createPortal } from 'react-dom'

import { cn } from '@/lib/utils'

import { Button } from './button'

/**
 * One count for the whole module, not per instance: the scroll lock is a
 * property of the document, so two stacked dialogs must not each restore the
 * body overflow the other just overwrote.
 */
let openDialogCount = 0
let bodyOverflowBeforeLock = ''

/** Innermost dialog last — Escape and focus return unwind in this order. */
interface OpenDialogEntry {
  panel: HTMLElement
  previouslyFocused: HTMLElement | null
}
const openDialogs: OpenDialogEntry[] = []

function lockBodyScroll(): void {
  if (openDialogCount === 0) {
    bodyOverflowBeforeLock = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  openDialogCount += 1
}

function unlockBodyScroll(): void {
  openDialogCount = Math.max(0, openDialogCount - 1)
  if (openDialogCount === 0) {
    document.body.style.overflow = bodyOverflowBeforeLock
  }
}

/**
 * `Tab` order, minus anything the user cannot reach. Offset visibility is
 * deliberately not tested: it is always false under jsdom, and the structural
 * checks below cover the cases that actually occur.
 */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function getFocusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      !element.hasAttribute('disabled') &&
      !element.hidden &&
      !(element instanceof HTMLInputElement && element.type === 'hidden'),
  )
}

interface DialogContextValue {
  open: boolean
  setOpen: (next: boolean) => void
  contentId: string
  titleId: string
  descriptionId: string
  titlePresent: boolean
  descriptionPresent: boolean
  registerTitle: () => () => void
  registerDescription: () => () => void
}

const DialogContext = React.createContext<DialogContextValue | null>(null)

function useDialogContext(component: string): DialogContextValue {
  const context = React.useContext(DialogContext)
  if (!context) {
    throw new Error(`\`${component}\` must be rendered inside a \`Dialog\`.`)
  }
  return context
}

export interface DialogProps {
  open?: boolean
  defaultOpen?: boolean
  onOpenChange?: (open: boolean) => void
  children?: React.ReactNode
}

function Dialog({ open, defaultOpen = false, onOpenChange, children }: DialogProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen)
  const isControlled = open !== undefined
  const isOpen = isControlled ? open : uncontrolledOpen

  // Presence is state, not a prop: a title may be rendered conditionally, and a
  // panel that points at a missing id is an unlabelled dialog.
  const [titleCount, setTitleCount] = React.useState(0)
  const [descriptionCount, setDescriptionCount] = React.useState(0)

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolledOpen(next)
      onOpenChange?.(next)
    },
    [isControlled, onOpenChange],
  )

  const registerTitle = React.useCallback(() => {
    setTitleCount((count) => count + 1)
    return () => setTitleCount((count) => Math.max(0, count - 1))
  }, [])

  const registerDescription = React.useCallback(() => {
    setDescriptionCount((count) => count + 1)
    return () => setDescriptionCount((count) => Math.max(0, count - 1))
  }, [])

  const id = React.useId()
  const contextValue = React.useMemo<DialogContextValue>(
    () => ({
      open: isOpen,
      setOpen,
      contentId: `${id}-content`,
      titleId: `${id}-title`,
      descriptionId: `${id}-description`,
      titlePresent: titleCount > 0,
      descriptionPresent: descriptionCount > 0,
      registerTitle,
      registerDescription,
    }),
    [
      id,
      isOpen,
      setOpen,
      titleCount,
      descriptionCount,
      registerTitle,
      registerDescription,
    ],
  )

  return <DialogContext.Provider value={contextValue}>{children}</DialogContext.Provider>
}
Dialog.displayName = 'Dialog'

export interface DialogTriggerProps extends React.ComponentPropsWithoutRef<'button'> {
  /** Render the child element instead of a `<button>`, keeping the styles. */
  asChild?: boolean
}

const DialogTrigger = React.forwardRef<HTMLButtonElement, DialogTriggerProps>(
  ({ asChild = false, onClick, ...props }, ref) => {
    const { open, setOpen } = useDialogContext('DialogTrigger')
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-state={open ? 'open' : 'closed'}
        {...(asChild ? {} : { type: (props as { type?: 'button' | 'submit' | 'reset' }).type ?? 'button' })}
        onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
          onClick?.(event)
          if (!event.defaultPrevented) setOpen(!open)
        }}
        {...props}
      />
    )
  },
)
DialogTrigger.displayName = 'DialogTrigger'

function DialogPortal({ children }: { children?: React.ReactNode }) {
  const { open } = useDialogContext('DialogPortal')
  if (!open || typeof document === 'undefined') return null
  return createPortal(children, document.body)
}
DialogPortal.displayName = 'DialogPortal'

const DialogOverlay = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, onClick, ...props }, ref) => {
    const { setOpen } = useDialogContext('DialogOverlay')
    return (
      <div
        ref={ref}
        data-slot="dialog-overlay"
        data-state="open"
        // The panel is a sibling, never a child, so an inside click cannot reach
        // this handler — the dismissal rule needs no containment check.
        onClick={(event) => {
          onClick?.(event)
          if (!event.defaultPrevented) setOpen(false)
        }}
        className={cn(
          'fixed inset-0 z-50 bg-foreground/20 backdrop-blur-[2px]',
          'data-[state=open]:animate-in data-[state=open]:fade-in-0',
          className,
        )}
        {...props}
      />
    )
  },
)
DialogOverlay.displayName = 'DialogOverlay'

export interface DialogContentProps extends React.ComponentPropsWithoutRef<'div'> {
  /** Renders the dismiss button; off when the dialog supplies its own footer action. */
  showClose?: boolean
}

const DialogContent = React.forwardRef<HTMLDivElement, DialogContentProps>(
  ({ className, children, showClose = true, ...props }, ref) => {
    const {
      open,
      setOpen,
      contentId,
      titleId,
      descriptionId,
      titlePresent,
      descriptionPresent,
    } = useDialogContext('DialogContent')

    const panelRef = React.useRef<HTMLDivElement | null>(null)
    const setPanelRef = React.useCallback(
      (node: HTMLDivElement | null) => {
        panelRef.current = node
        if (typeof ref === 'function') ref(node)
        else if (ref) ref.current = node
      },
      [ref],
    )

    React.useEffect(() => {
      if (!open) return
      const panel = panelRef.current
      if (!panel) return

      const active = document.activeElement
      openDialogs.push({
        panel,
        previouslyFocused: active instanceof HTMLElement ? active : null,
      })
      lockBodyScroll()

      const focusables = getFocusableElements(panel)
      const first = focusables[0]
      // With nothing focusable inside, the panel itself is the focus target —
      // an element with tabIndex={-1}, so it holds focus without joining the
      // page's tab order.
      ;(first ?? panel).focus()

      return () => {
        const index = openDialogs.findIndex((entry) => entry.panel === panel)
        const entry = index === -1 ? undefined : openDialogs.splice(index, 1)[0]
        unlockBodyScroll()
        const restoreTo = entry?.previouslyFocused
        if (restoreTo && restoreTo !== document.body && restoreTo.isConnected) {
          restoreTo.focus()
        }
      }
    }, [open])

    React.useEffect(() => {
      if (!open) return
      const onKeyDown = (event: KeyboardEvent): void => {
        const panel = panelRef.current
        if (!panel) return
        // Only the innermost dialog owns the keyboard: a stacked one must not
        // close or steal focus for the dialog beneath it.
        if (openDialogs[openDialogs.length - 1]?.panel !== panel) return

        if (event.key === 'Escape') {
          event.preventDefault()
          setOpen(false)
          return
        }
        if (event.key !== 'Tab') return

        const focusables = getFocusableElements(panel)
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        if (!first || !last) {
          event.preventDefault()
          panel.focus()
          return
        }
        const active = document.activeElement
        const outsidePanel = !panel.contains(active)
        if (event.shiftKey && (active === first || outsidePanel)) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && (active === last || outsidePanel)) {
          event.preventDefault()
          first.focus()
        }
      }
      document.addEventListener('keydown', onKeyDown)
      return () => document.removeEventListener('keydown', onKeyDown)
    }, [open, setOpen])

    if (!open) return null

    return (
      <DialogPortal>
        <DialogOverlay />
        <div
          ref={setPanelRef}
          id={contentId}
          role="dialog"
          aria-modal="true"
          data-slot="dialog-content"
          data-state="open"
          tabIndex={-1}
          {...(titlePresent
            ? { 'aria-labelledby': titleId }
            : // Never leave the panel unlabelled: a dialog with no DialogTitle
              // still has to announce something to a screen reader.
              { 'aria-label': (props['aria-label'] as string | undefined) ?? 'Dialog' })}
          {...(descriptionPresent ? { 'aria-describedby': descriptionId } : {})}
          className={cn(
            'fixed left-1/2 top-1/2 z-50 grid w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4',
            'max-h-[90vh] overflow-y-auto overscroll-contain rounded-lg border border-border bg-card p-6 text-card-foreground shadow-lg',
            'duration-200 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
            'focus-visible:outline-none',
            className,
          )}
          {...props}
        >
          {children}
          {showClose ? (
            <Button
              variant="ghost"
              size="icon"
              className="absolute right-4 top-4 rounded-md text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(false)}
            >
              <X aria-hidden="true" />
              <span className="sr-only">Close</span>
            </Button>
          ) : null}
        </div>
      </DialogPortal>
    )
  },
)
DialogContent.displayName = 'DialogContent'

const DialogHeader = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="dialog-header"
      className={cn('flex flex-col space-y-1.5 text-center sm:text-left', className)}
      {...props}
    />
  ),
)
DialogHeader.displayName = 'DialogHeader'

const DialogFooter = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<'div'>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="dialog-footer"
      className={cn('flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', className)}
      {...props}
    />
  ),
)
DialogFooter.displayName = 'DialogFooter'

const DialogTitle = React.forwardRef<HTMLHeadingElement, React.ComponentPropsWithoutRef<'h2'>>(
  ({ className, id, ...props }, ref) => {
    const { titleId, registerTitle } = useDialogContext('DialogTitle')
    React.useEffect(() => registerTitle(), [registerTitle])
    return (
      <h2
        ref={ref}
        id={id ?? titleId}
        data-slot="dialog-title"
        className={cn('text-lg font-semibold leading-none tracking-tight', className)}
        {...props}
      />
    )
  },
)
DialogTitle.displayName = 'DialogTitle'

const DialogDescription = React.forwardRef<HTMLParagraphElement, React.ComponentPropsWithoutRef<'p'>>(
  ({ className, id, ...props }, ref) => {
    const { descriptionId, registerDescription } = useDialogContext('DialogDescription')
    React.useEffect(() => registerDescription(), [registerDescription])
    return (
      <p
        ref={ref}
        id={id ?? descriptionId}
        data-slot="dialog-description"
        className={cn('text-sm text-muted-foreground', className)}
        {...props}
      />
    )
  },
)
DialogDescription.displayName = 'DialogDescription'

export interface DialogCloseProps extends React.ComponentPropsWithoutRef<'button'> {
  /** Render the child element instead of a `<button>`, keeping the styles. */
  asChild?: boolean
}

const DialogClose = React.forwardRef<HTMLButtonElement, DialogCloseProps>(
  ({ asChild = false, className, onClick, ...props }, ref) => {
    const { setOpen } = useDialogContext('DialogClose')
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        data-slot="dialog-close"
        {...(asChild
          ? {}
          : { type: (props as { type?: 'button' | 'submit' | 'reset' }).type ?? 'button' })}
        onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
          onClick?.(event)
          if (!event.defaultPrevented) setOpen(false)
        }}
        className={cn(
          'inline-flex items-center justify-center gap-2 rounded-md bg-transparent text-sm font-medium text-muted-foreground transition-colors',
          'hover:bg-accent hover:text-accent-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:pointer-events-none disabled:opacity-50',
          className,
        )}
        {...props}
      />
    )
  },
)
DialogClose.displayName = 'DialogClose'

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}