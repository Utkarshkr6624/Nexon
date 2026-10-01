import { Bell } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/**
 * There is no notification store in Phase 2 — no backend, no persisted
 * preferences, nothing to fake. The count below is a real zero rather than a
 * sample number, and the panel says plainly why it is empty. A control that
 * opened onto a fabricated feed would be worse than one that admits the feature
 * is not built yet.
 */
const UNREAD_COUNT = 0

export function NotificationsMenu() {
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="relative text-muted-foreground"
              aria-label={UNREAD_COUNT > 0 ? `Notifications, ${UNREAD_COUNT} unread` : 'Notifications'}
            >
              <Bell aria-hidden="true" />
              {/* A muted "0" chip is noise, so the marker only exists once
                  there is genuinely something unread. */}
              {UNREAD_COUNT > 0 && (
                <span
                  aria-hidden="true"
                  className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold tabular-nums text-primary-foreground"
                >
                  {UNREAD_COUNT}
                </span>
              )}
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">Notifications</TooltipContent>
      </Tooltip>

      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between gap-2 px-3 py-2.5">
          <DropdownMenuLabel className="p-0 text-sm">Notifications</DropdownMenuLabel>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {UNREAD_COUNT} unread
          </span>
        </div>
        <DropdownMenuSeparator className="my-0" />
        <ScrollArea className="max-h-80">
          <EmptyState
            compact
            icon={Bell}
            title="Nothing needs you right now"
            description="Alerts from projects, tasks and the assistant collect here. None of those surfaces ship a feed yet, so an empty panel is the accurate state rather than a broken one."
          />
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
