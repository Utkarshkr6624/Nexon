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
import { cn } from '@/lib/utils'

/**
 * No notification records exist yet, so the unread count is a real zero rather
 * than a sample number. The badge stays mounted so the affordance is visible.
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
              aria-label={`Notifications (${UNREAD_COUNT} unread)`}
            >
              <Bell className="size-4" aria-hidden="true" />
              <span
                aria-hidden="true"
                className={cn(
                  'absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums',
                  UNREAD_COUNT > 0
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                {UNREAD_COUNT}
              </span>
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">Notifications</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between px-3 py-2.5">
          <DropdownMenuLabel className="p-0 text-sm">Notifications</DropdownMenuLabel>
          <span className="text-xs tabular-nums text-muted-foreground">
            {UNREAD_COUNT} unread
          </span>
        </div>
        <DropdownMenuSeparator className="my-0" />
        <ScrollArea className="max-h-80">
          <EmptyState
            compact
            icon={Bell}
            title="Nothing needs you right now"
            description="Alerts from projects, tasks and the assistant will collect here. Nothing is generated until those modules exist, so this stays empty for now."
          />
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
