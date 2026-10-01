import { useLocation } from 'react-router-dom'
import { Menu } from 'lucide-react'

import { CommandPaletteTrigger } from '@/components/layout/command-palette-trigger'
import { NotificationsMenu } from '@/components/layout/notifications-menu'
import { ThemeToggle } from '@/components/layout/theme-toggle'
import { UserMenu } from '@/components/layout/user-menu'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { NAV_GROUPS, findModule } from '@/features/modules/catalog'

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

export function TopBar({ onOpenMobileNav }: TopBarProps) {
  const { title, group } = useBreadcrumb()

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background px-3 sm:px-4">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="text-muted-foreground lg:hidden"
        onClick={onOpenMobileNav}
        aria-label="Open navigation"
      >
        <Menu className="size-5" aria-hidden="true" />
      </Button>

      <div className="flex min-w-0 items-center gap-2">
        <span className="hidden text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground lg:inline">
          {group}
        </span>
        <span aria-hidden="true" className="hidden text-muted-foreground/40 lg:inline">
          /
        </span>
        <h2 className="truncate text-sm font-medium text-foreground">{title}</h2>
      </div>

      <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
        <CommandPaletteTrigger />
        <ThemeToggle />
        <NotificationsMenu />
        <Separator orientation="vertical" className="mx-0.5 hidden h-5 md:block" />
        <UserMenu />
      </div>
    </header>
  )
}