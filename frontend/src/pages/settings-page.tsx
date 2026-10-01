import { useRef } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { SETTINGS_MODULE } from '@/features/modules/catalog'
import { useThemeStore } from '@/stores/theme-store'
import type { ThemePreference } from '@/stores/theme-store'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'

const THEME_OPTIONS: ReadonlyArray<{
  value: ThemePreference
  label: string
  description: string
  icon: typeof Sun
}> = [
  {
    value: 'light',
    label: 'Light',
    description: 'Always the light palette.',
    icon: Sun,
  },
  {
    value: 'dark',
    label: 'Dark',
    description: 'Always the dark palette.',
    icon: Moon,
  },
  {
    value: 'system',
    label: 'System',
    description: 'Follow the operating system appearance.',
    icon: Monitor,
  },
]

export default function SettingsPage() {
  const preference = useThemeStore((state) => state.preference)
  const resolvedTheme = useThemeStore((state) => state.resolvedTheme)
  const setTheme = useThemeStore((state) => state.setTheme)
  const user = useAuthStore((state) => state.user)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])

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

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <header className="flex flex-col gap-4 border-b border-border pb-6 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Platform
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-foreground">Settings</h1>
            <Badge variant="outline" className="font-normal">
              Phase 1 · partially live
            </Badge>
          </div>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {SETTINGS_MODULE.summary}
          </p>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>
              Stored in this browser only. Applied before first paint, so reloading never
              flashes the wrong theme.
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

        <Card className="lg:col-span-5">
          <CardHeader>
            <CardTitle>Session</CardTitle>
            <CardDescription>
              Held in this browser. Signing out revokes the session on the backend.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Row label="Signed in as" value={user?.email ?? 'Unknown'} />
            <Separator />
            <Row label="Display name" value={selectDisplayName(user)} />
            <Separator />
            <Row label="Account verified" value={user?.is_verified ? 'Yes' : 'Not yet'} />
            <Separator />
            <Row label="API base URL" value={import.meta.env.VITE_API_BASE_URL ?? '/api/v1'} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Planned preferences</CardTitle>
          <CardDescription>
            Each of these needs a stored profile or a backend stream, so they arrive with the
            module that owns the underlying data.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-border">
            {[
              { label: 'Profile and account details', phase: 'Phase 8 · Career' },
              { label: 'Notification preferences', phase: 'Phase 2 · Projects' },
              { label: 'Data export and retention', phase: 'Phase 5 · Analytics' },
              { label: 'Local model configuration', phase: 'Phase 9 · Assistant' },
            ].map((item) => (
              <li key={item.label} className="flex items-center justify-between gap-4 py-3">
                <span className="text-sm text-foreground">{item.label}</span>
                <Badge variant="secondary" className="shrink-0 font-normal">
                  {item.phase}
                </Badge>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="truncate font-mono text-xs text-foreground">{value}</span>
    </div>
  )
}