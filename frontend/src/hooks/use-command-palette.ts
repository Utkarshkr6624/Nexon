import { useEffect } from 'react'
import { useCommandPaletteStore } from '@/features/command-palette/command-palette-store'

export interface CommandPaletteApi {
  open: boolean
  setOpen: (open: boolean) => void
  toggle: () => void
}

/**
 * Wires the global ⌘K / Ctrl+K hotkey to the command palette store.
 * The listener is installed on `window` with `capture` so the shortcut works
 * even while focus sits inside an input, and it is a no-op while a modifier
 * combination other than the palette shortcut is pressed.
 */
export function useCommandPalette(): CommandPaletteApi {
  const open = useCommandPaletteStore((state) => state.open)
  const setOpen = useCommandPaletteStore((state) => state.setOpen)
  const toggle = useCommandPaletteStore((state) => state.toggle)

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return
      if (event.key.toLowerCase() !== 'k') return
      if (!event.metaKey && !event.ctrlKey) return
      event.preventDefault()
      toggle()
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggle])

  return { open, setOpen, toggle }
}

/** True when the platform's primary shortcut modifier is Command, not Control. */
export function usesCommandKey(): boolean {
  if (typeof navigator === 'undefined') return false
  return /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
}
