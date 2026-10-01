import { useLocation } from 'react-router-dom'
import { Menu, Search } from 'lucide-react'

import { NotificationsMenu } from '@/components/layout/notifications-menu'
import { ThemeToggle } from '@/components/layout/theme-toggle'
import { UserMenu } from '@/components/layout/user-menu'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useCommandPaletteStore } from '@/features/command-palette/command-palette-store'
import { NAV_GROUPS, findModule } from '@/features/modules/catalog'
import { usesCommandKey } from '@/hooks/use-command-palette'
import { cn } from '@/lib/utils'

export interface TopBarProps {
  onOpenMobileNav: () => void
}

/** Group › page, resolved from the module registry rather than route nesting. */
function useBreadcrumb() {
  const { pathname } = useLocation()
  const module = findModule(pathname)
  const group = NAV_GROUPS.find((candidate) =>
    candidate.items.some((item) => item.to === pathname),
  )
  return { title: module?.label ?? 'Not found', group: group?.label ?? 'Platform' }
}

/**
 * The bar's anchor. It is drawn as a field rather than a button — bordered,
 * rounded, a leading search glyph and a muted placeholder — because a product
 * with this much navigation needs one control that looks like the way in. The
 * hint shows the platform-correct modifier, so the same control reads as ⌘K on a
 * Mac and Ctrl+K everywhere else. Below `sm` it collapses to the glyph alone so
 * a 320px viewport is not asked to fit a field.
 */
function GlobalSearchTrigger() {
  const setOpen = useCommandPaletteStore((state) => state.setOpen)
  const modKey = usesCommandKey() ? '⌘' : 'Ctrl'

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          onClick={() => setOpen(true)}
          aria-label={`Search modules (${modKey} K)`}
          className={cn(
            'w-8 justify-center gap-2 bg-muted/50 px-0 text-muted-foreground',
            'hover:bg-muted hover:text-foreground',
            'sm:w-56 sm:justify-start sm:px-2.5 lg:w-72',
          )}
        >
          <Search aria-hidden="true" className="size-4 shrink-0" />
          <span className="hidden min-w-0 flex-1 truncate text-left font-normal sm:inline">
            Search modules
          </span>
          <span aria-hidden="true" className="hidden items-center gap-1 md:flex">
            <Kbd>{modKey}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">Jump to a module</TooltipContent>
    </Tooltip>
  )
}

export function TopBar({ onOpenMobileNav }: TopBarProps) {
  const { title, group } = useBreadcrumb()

  return (
    // h-14 and the same hairline as the rail header, so the two bars read as one
    // frame. The bar is opaque rather than translucent: the content beneath it
    // scrolls, and a blurred bar just adds a second thing moving.
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background px-3 sm:px-4">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="shrink-0 text-muted-foreground lg:hidden"
        onClick={onOpenMobileNav}
        aria-label="Open navigation"
      >
        <Menu aria-hidden="true" />
      </Button>

      {/* min-w-0 plus flex-1 is what keeps a long page title truncating instead
          of pushing the controls off the bar at narrow widths. */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="hidden text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground lg:inline">
          {group}
        </span>
        <span aria-hidden="true" className="hidden text-muted-foreground/40 lg:inline">
          /
        </span>
        <h2 className="truncate text-sm font-medium text-foreground">{title}</h2>
      </div>

      <div className="flex shrink-0 items-center gap-1 sm:gap-1.5">
        <GlobalSearchTrigger />
        <ThemeToggle />
        <NotificationsMenu />
        <Separator orientation="vertical" className="mx-0.5 hidden h-5 md:block" />
        <UserMenu />
      </div>
    </header>
  )
}
