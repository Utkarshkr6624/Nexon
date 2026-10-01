import { Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useCommandPaletteStore } from '@/features/command-palette/command-palette-store'
import { usesCommandKey } from '@/hooks/use-command-palette'

/**
 * Opens the global palette. The hint shows the platform-correct modifier, so
 * the same control reads as ⌘K on a Mac and Ctrl+K everywhere else.
 */
export function CommandPaletteTrigger() {
  const setOpen = useCommandPaletteStore((state) => state.setOpen)
  const modKey = usesCommandKey() ? '⌘' : 'Ctrl'

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setOpen(true)}
          aria-label={`Search modules (${modKey} K)`}
          className="h-8 gap-2 border-border bg-card pl-2.5 pr-2 text-muted-foreground"
        >
          <Search className="size-3.5" aria-hidden="true" />
          <span className="hidden text-xs sm:inline">Search modules</span>
          <span aria-hidden="true" className="hidden items-center gap-0.5 sm:flex">
            <Kbd>{modKey}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">Jump to a module</TooltipContent>
    </Tooltip>
  )
}