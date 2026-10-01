import { create } from 'zustand'

interface CommandPaletteState {
  open: boolean
  setOpen: (open: boolean) => void
  toggle: () => void
}

export const useCommandPaletteStore = create<CommandPaletteState>()((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  toggle: () => set((state) => ({ open: !state.open })),
}))

/** Closes the palette and drops focus so the trigger regains it on close. */
export function closeCommandPalette() {
  useCommandPaletteStore.getState().setOpen(false)
}
