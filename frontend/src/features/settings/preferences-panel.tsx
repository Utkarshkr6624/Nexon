import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Monitor, Moon, PanelLeftClose, Sun, Waves } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/theme-store'
import type { ThemePreference } from '@/stores/theme-store'

const THEME_OPTIONS: ReadonlyArray<{
  value: ThemePreference
  label: string
  description: string
  icon: typeof Sun
}> = [
  { value: 'light', label: 'Light', description: 'Always the light palette.', icon: Sun },
  { value: 'dark', label: 'Dark', description: 'Always the dark palette.', icon: Moon },
  { value: 'system', label: 'System', description: 'Follow the operating system.', icon: Monitor },
]

/**
 * The same key `app-shell.tsx` reads and writes for the navigation rail. Reading
 * and writing it directly is the point: a second store here would leave the rail
 * and this switch disagreeing about the same preference.
 */
const SIDEBAR_COLLAPSED_KEY = 'nexus.sidebar.collapsed'

/**
 * Device-local override for motion. The OS preference is honoured by the
 * `prefers-reduced-motion` block in `src/index.css`; this setting can only add
 * the same reduction on top of it, never subtract it.
 */
const MOTION_KEY = 'nexus.motion'
const MOTION_ATTRIBUTE = 'data-nexus-motion'
const MOTION_STYLE_ID = 'nexus-motion-override'

/**
 * Deliberately the same declarations as the reduced-motion block in
 * `src/index.css`, scoped to a document attribute. Its selector is more
 * specific than the bare `*` in the media query, so the two compose instead of
 * fighting: the OS preference still applies, this only adds to it.
 */
const REDUCED_MOTION_CSS = `
  html[${MOTION_ATTRIBUTE}='reduced'] *,
  html[${MOTION_ATTRIBUTE}='reduced'] *::before,
  html[${MOTION_ATTRIBUTE}='reduced'] *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
`

function readBoolean(key: string, fallback: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(key)
    if (raw === null) return fallback
    return raw === 'true' || raw === 'reduced'
  } catch {
    return fallback
  }
}

function writeBoolean(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // Storage unavailable: the preference still applies for this page load.
  }
}

/**
 * Applies the override document-wide. The style element is created once and
 * then edited in place rather than removed, so the preference keeps working
 * after the user navigates away from settings.
 */
function applyMotionOverride(reduced: boolean): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (reduced) {
    root.setAttribute(MOTION_ATTRIBUTE, 'reduced')
  } else {
    root.removeAttribute(MOTION_ATTRIBUTE)
  }

  let style = document.getElementById(MOTION_STYLE_ID)
  if (!style) {
    const element = document.createElement('style')
    element.id = MOTION_STYLE_ID
    document.head.appendChild(element)
    style = element
  }
  style.textContent = reduced ? REDUCED_MOTION_CSS : ''
}

export function PreferencesPanel() {
  const preference = useThemeStore((state) => state.preference)
  const resolvedTheme = useThemeStore((state) => state.resolvedTheme)
  const setTheme = useThemeStore((state) => state.setTheme)
  const systemAsksForReducedMotion = usePrefersReducedMotion()
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])

  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    readBoolean(SIDEBAR_COLLAPSED_KEY, false),
  )
  const [reducedMotion, setReducedMotion] = useState(() => readBoolean(MOTION_KEY, false))

  // Re-applied on mount so a stored preference is live before the user touches
  // anything, and the stylesheet is the one place the two preferences are applied.
  useEffect(() => {
    applyMotionOverride(reducedMotion)
  }, [reducedMotion])

  function selectAt(index: number) {
    const option = THEME_OPTIONS[index]
    if (!option) return
    setTheme(option.value)
    optionRefs.current[index]?.focus()
  }

  /** Roving-tabindex keyboard support; Space and Enter arrive as a native click. */
  function onThemeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const current = THEME_OPTIONS.findIndex((option) => option.value === preference)
    if (current === -1) return
    const last = THEME_OPTIONS.length - 1
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault()
      selectAt(current === last ? 0 : current + 1)
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault()
      selectAt(current === 0 ? last : current - 1)
    } else if (event.key === 'Home') {
      event.preventDefault()
      selectAt(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      selectAt(last)
    }
  }

  function onSidebarCollapsedChange(next: boolean) {
    setSidebarCollapsed(next)
    writeBoolean(SIDEBAR_COLLAPSED_KEY, next)
  }

  function onReducedMotionChange(next: boolean) {
    setReducedMotion(next)
    writeBoolean(MOTION_KEY, next)
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>
            Stored in this browser only. Applied before first paint, so reloading never flashes
            the wrong theme.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div
            role="radiogroup"
            aria-label="Theme"
            onKeyDown={onThemeKeyDown}
            className="grid gap-2 sm:grid-cols-3"
          >
            {THEME_OPTIONS.map((option, index) => {
              const active = preference === option.value
              const Icon = option.icon
              return (
                <button
                  key={option.value}
                  ref={(node) => {
                    optionRefs.current[index] = node
                  }}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  tabIndex={active ? 0 : -1}
                  onClick={() => setTheme(option.value)}
                  className={cn(
                    'flex flex-col gap-1 rounded-md border p-3 text-left transition-colors duration-150 ease-out',
                    active
                      ? 'border-primary bg-primary/[0.06]'
                      : 'border-border hover:bg-accent hover:text-accent-foreground',
                  )}
                >
                  <span className="flex items-center gap-2">
                    <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                    <span className="text-sm font-medium text-foreground">{option.label}</span>
                  </span>
                  <span className="text-xs leading-relaxed text-muted-foreground">
                    {option.description}
                  </span>
                </button>
              )
            })}
          </div>

          <Separator className="my-4" />

          <p className="text-xs text-muted-foreground">
            Effective theme right now:{' '}
            <span className="font-medium text-foreground">{resolvedTheme}</span>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Layout and motion</CardTitle>
          <CardDescription>
            Preferences for this browser. Nothing here is stored on the account, so a different
            device keeps its own settings.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-start justify-between gap-6">
            <div className="min-w-0 space-y-1">
              <Label htmlFor="pref-sidebar-collapsed" className="cursor-pointer">
                Collapsed sidebar
              </Label>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Show the navigation rail as icons only, for more room on wide screens. The rail
                picks the new layout up the next time NEXUS loads.
              </p>
            </div>
            <Switch
              id="pref-sidebar-collapsed"
              checked={sidebarCollapsed}
              onCheckedChange={onSidebarCollapsedChange}
              className="mt-0.5"
            />
          </div>

          <Separator />

          <div className="flex items-start justify-between gap-6">
            <div className="min-w-0 space-y-1">
              <Label htmlFor="pref-reduced-motion" className="flex cursor-pointer items-center gap-2">
                <Waves className="size-3.5 text-muted-foreground" aria-hidden="true" />
                Reduce motion
              </Label>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Collapses transitions and animations across NEXUS in this browser. NEXUS already
                follows your operating system setting; turn this on to force the reduced treatment
                regardless of what the system says.
              </p>
              <p className="text-xs text-muted-foreground">
                Your system currently{' '}
                <span className="font-medium text-foreground">
                  {systemAsksForReducedMotion ? 'asks for reduced motion' : 'allows motion'}
                </span>
                .
              </p>
            </div>
            <Switch
              id="pref-reduced-motion"
              checked={reducedMotion}
              onCheckedChange={onReducedMotionChange}
              className="mt-0.5"
            />
          </div>

          <Separator />

          <div className="flex items-start gap-3 rounded-md border border-dashed border-border p-3">
            <PanelLeftClose className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <p className="text-xs leading-relaxed text-muted-foreground">
              Notification, export and retention preferences are not here yet. Each one needs a
              stored record or a backend stream, so it arrives with the module that owns that
              data rather than as a control that would not do anything.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
