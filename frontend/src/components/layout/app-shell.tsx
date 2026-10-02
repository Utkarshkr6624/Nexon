import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

import { CommandPalette } from '@/components/layout/command-palette'
import { RouteFallback } from '@/components/feedback/loading-state'
import {
  AppSidebar,
  SIDEBAR_WIDTH_COLLAPSED,
  SIDEBAR_WIDTH_EXPANDED,
} from '@/components/layout/sidebar'
import { TopBar } from '@/components/layout/top-bar'
import { useIsDesktop } from '@/hooks/use-media-query'

const SIDEBAR_COLLAPSED_KEY = 'nexus.sidebar.collapsed'

const MAIN_CONTENT_ID = 'main-content'

function readCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
  } catch {
    return false
  }
}

/** The hamburger lives in the top bar, so it is addressed by its label. */
function navTrigger(): HTMLElement | null {
  return document.querySelector<HTMLElement>('button[aria-label="Open navigation"]')
}

export interface AppShellProps {
  children: ReactNode
}

/**
 * The application shell: fixed sidebar + slim top bar + scrolling content
 * region. Below `lg` the sidebar becomes an off-canvas sheet driven by the
 * hamburger; above it, it can collapse to icons. The sidebar width is exposed
 * as a CSS variable so the content gutter animates with it. While the sheet is
 * open it is modal: the region behind it is inert and focus stays in the sheet.
 */
export function AppShell({ children }: AppShellProps) {
  const isDesktop = useIsDesktop()

  const [collapsed, setCollapsed] = useState(readCollapsedPreference)
  const [mobileOpen, setMobileOpen] = useState(false)

  const openerRef = useRef<HTMLElement | null>(null)
  const sheetOpenedRef = useRef(false)

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed))
    } catch {
      // Collapse state is a convenience; storage being unavailable is fine.
    }
  }, [collapsed])

  // The sheet only exists below `lg`. Deriving it means resizing to desktop
  // cannot strand an open sheet behind the persistent sidebar.
  const sheetOpen = !isDesktop && mobileOpen

  const openMobile = useCallback(() => {
    openerRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    setMobileOpen(true)
  }, [])
  const closeMobile = useCallback(() => setMobileOpen(false), [])
  const toggleCollapsed = useCallback(() => setCollapsed((value) => !value), [])

  useEffect(() => {
    if (!sheetOpen) return undefined
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setMobileOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [sheetOpen])

  // The sheet is transient: whatever opened it takes focus back when it closes,
  // so dismissing it never strands the keyboard user on the page behind.
  useEffect(() => {
    if (sheetOpen) {
      sheetOpenedRef.current = true
      return
    }
    if (!sheetOpenedRef.current) return
    sheetOpenedRef.current = false

    const opener = openerRef.current
    openerRef.current = null
    const target = opener?.isConnected ? opener : navTrigger()
    target?.focus()
  }, [sheetOpen])

  const style = {
    '--sidebar-width': collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED,
  } as CSSProperties

  return (
    <div style={style} className="h-screen overflow-hidden">
      <a
        href={`#${MAIN_CONTENT_ID}`}
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-lg"
      >
        Skip to content
      </a>

      <AppSidebar
        collapsed={isDesktop && collapsed}
        mobileOpen={sheetOpen}
        inert={!isDesktop && !sheetOpen}
        onCollapseToggle={toggleCollapsed}
        onNavigate={closeMobile}
      />

      {/* While the sheet covers the page, the page itself is inert: the sheet
          holds focus, so the content behind it should not be reachable at all. */}
      <div
        inert={sheetOpen}
        className="flex h-full min-w-0 flex-col lg:pl-[var(--sidebar-width)]"
      >
        <TopBar onOpenMobileNav={openMobile} />
        <main id={MAIN_CONTENT_ID} tabIndex={-1} className="app-scroll bg-background">
          <Suspense fallback={<RouteFallback />}>{children}</Suspense>
        </main>
      </div>

      <CommandPalette />
    </div>
  )
}