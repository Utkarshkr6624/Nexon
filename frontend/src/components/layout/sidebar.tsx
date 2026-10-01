import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronsLeft, PanelLeft } from 'lucide-react'

import { Brand } from '@/components/brand/logo'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { NAV_GROUPS, SETTINGS_MODULE } from '@/features/modules/catalog'
import type { ModuleDefinition } from '@/features/modules/catalog'
import { cn } from '@/lib/utils'

export const SIDEBAR_WIDTH_EXPANDED = '260px'
export const SIDEBAR_WIDTH_COLLAPSED = '68px'

const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'

export interface AppSidebarProps {
  collapsed: boolean
  mobileOpen: boolean
  /** True while the sheet is parked off-screen below `lg`. */
  inert?: boolean
  onCollapseToggle: () => void
  onNavigate: () => void
}

function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => window.getComputedStyle(element).display !== 'none',
  )
}

interface NavItemProps {
  item: ModuleDefinition
  collapsed: boolean
  onNavigate: () => void
}

function NavItem({ item, collapsed, onNavigate }: NavItemProps) {
  const Icon = item.icon

  return (
    <NavLink
      to={item.to}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          'group relative flex h-9 items-center gap-3 rounded-md px-3 text-sm transition-colors duration-150 ease-out',
          'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          isActive && 'bg-accent/70 font-medium text-accent-foreground',
          collapsed && 'justify-center px-0',
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            aria-hidden="true"
            className={cn(
              'absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-primary transition-opacity duration-150 ease-out',
              isActive ? 'opacity-100' : 'opacity-0',
            )}
          />
          <Icon
            aria-hidden="true"
            className={cn(
              'size-4 shrink-0 transition-colors duration-150 ease-out',
              isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground',
            )}
          />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )
}

function CollapsibleNavItem({ item, collapsed, onNavigate }: NavItemProps) {
  if (!collapsed) return <NavItem item={item} collapsed={collapsed} onNavigate={onNavigate} />

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <NavItem item={item} collapsed onNavigate={onNavigate} />
      </TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  )
}

export function AppSidebar({
  collapsed,
  mobileOpen,
  inert = false,
  onCollapseToggle,
  onNavigate,
}: AppSidebarProps) {
  const rootRef = useRef<HTMLDivElement>(null)

  const style = {
    '--sidebar-width': collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED,
  } as CSSProperties

  // An open sheet covers the application, so it behaves modally: focus starts on
  // the first destination and Tab stays inside until the sheet closes.
  useEffect(() => {
    if (!mobileOpen) return undefined
    const root = rootRef.current
    if (!root) return undefined

    root.querySelector<HTMLElement>('nav a[href]')?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !root) return

      const items = focusableWithin(root)
      if (items.length === 0) return

      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) return
      const active = document.activeElement

      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mobileOpen])

  return (
    <div
      ref={rootRef}
      inert={inert}
      style={style}
      className={cn(
        'fixed inset-y-0 left-0 z-40 flex w-[260px] flex-col border-r border-border bg-card',
        'transition-[width,transform] duration-200 ease-out',
        'lg:w-[var(--sidebar-width)]',
        mobileOpen ? 'translate-x-0 shadow-2xl' : '-translate-x-full lg:translate-x-0',
      )}
    >
      <div
        className={cn(
          'flex h-14 shrink-0 items-center border-b border-border',
          collapsed ? 'justify-center px-2' : 'justify-between px-4',
        )}
      >
        <Brand markOnly={collapsed} />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="hidden size-8 shrink-0 text-muted-foreground lg:inline-flex"
          onClick={onCollapseToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <PanelLeft aria-hidden="true" />
          ) : (
            <ChevronsLeft aria-hidden="true" />
          )}
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <nav className="space-y-6 px-3 py-4" aria-label="Primary">
          {NAV_GROUPS.map((group) => (
            <div key={group.id} className="space-y-1">
              {collapsed ? (
                <div aria-hidden="true" className="mx-3 mb-3 h-px bg-border" />
              ) : (
                <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
                  {group.label}
                </p>
              )}
              {group.items.map((item) => (
                <CollapsibleNavItem
                  key={item.to}
                  item={item}
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          ))}
        </nav>
      </ScrollArea>

      <div className="shrink-0 border-t border-border p-3">
        <CollapsibleNavItem
          item={SETTINGS_MODULE}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
        {!collapsed && (
          <p className="px-3 pt-3 text-[11px] leading-relaxed text-muted-foreground/70">
            Phase 1 · shell and platform foundations
          </p>
        )}
      </div>
    </div>
  )
}
