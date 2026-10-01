import { useEffect, useRef, useState } from 'react'

import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastTitle,
} from '@/components/ui/toast'
import { useToastStore } from '@/stores/toast-store'
import type { ToastRecord } from '@/stores/toast-store'

/** Matches the entry/exit motion the toast primitive animates with. */
const EXIT_DURATION_MS = 150

interface ToastItemProps {
  toast: ToastRecord
  state: 'open' | 'closed'
  onDismiss: (id: string) => void
}

function ToastItem({ toast, state, onDismiss }: ToastItemProps) {
  return (
    <Toast
      variant={toast.variant}
      data-state={state}
      // A polite region cannot be made to interrupt on demand, so failures
      // opt out of it and are announced assertively in their own right.
      role={toast.variant === 'destructive' ? 'alert' : undefined}
    >
      <div className="flex-1 space-y-1">
        <ToastTitle>{toast.title}</ToastTitle>
        {toast.description ? <ToastDescription>{toast.description}</ToastDescription> : null}
        {toast.action ? <ToastAction label={toast.action.label} onClick={toast.action.onClick} /> : null}
      </div>
      <ToastClose onClick={() => onDismiss(toast.id)} aria-label="Dismiss notification" />
    </Toast>
  )
}

/**
 * Renders the store's stack. Mounted once at the app root, below the theme
 * provider so a toast picks up the same palette as the page it reports on.
 */
export function Toaster() {
  const toasts = useToastStore((state) => state.toasts)
  const dismiss = useToastStore((state) => state.dismiss)
  const [closing, setClosing] = useState<ToastRecord[]>([])
  const live = useRef<ToastRecord[]>(toasts)
  const exitTimers = useRef<Set<number>>(new Set())

  // The store drops a record the instant it is dismissed, but the toast needs
  // one more beat on screen to leave. Removals are held here for exactly the
  // length of the exit so a dismissal reads as motion rather than a blink.
  useEffect(() => {
    const liveIds = new Set(toasts.map((toast) => toast.id))
    const removed = live.current.filter((toast) => !liveIds.has(toast.id))
    live.current = toasts
    if (removed.length === 0) return

    setClosing((previous) => [
      ...previous.filter((toast) => !removed.some((gone) => gone.id === toast.id)),
      ...removed,
    ])

    const timer = window.setTimeout(() => {
      exitTimers.current.delete(timer)
      setClosing((previous) =>
        previous.filter((toast) => !removed.some((gone) => gone.id === toast.id)),
      )
    }, EXIT_DURATION_MS)
    exitTimers.current.add(timer)
  }, [toasts])

  // A toast that outlives the view would call setState on an unmounted tree.
  useEffect(() => {
    const timers = exitTimers.current
    return () => {
      for (const timer of timers) window.clearTimeout(timer)
      timers.clear()
    }
  }, [])

  const liveIds = new Set(toasts.map((toast) => toast.id))
  const stack: ToastRecord[] = [...closing, ...toasts]

  return (
    // The region is mounted empty and stays mounted: a live region that arrives
    // together with its first message is often not announced at all.
    <div
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-stretch gap-2 p-4 sm:inset-x-auto sm:w-[380px] sm:items-end sm:pb-6 sm:pr-6"
    >
      {stack.map((toast) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          state={liveIds.has(toast.id) ? 'open' : 'closed'}
          onDismiss={dismiss}
        />
      ))}
    </div>
  )
}
