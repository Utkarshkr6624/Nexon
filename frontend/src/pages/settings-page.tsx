import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { BadgeCheck, CalendarDays, KeyRound, ShieldCheck, UserRound } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SETTINGS_MODULE } from '@/features/modules/catalog'
import { DangerZone } from '@/features/settings/danger-zone'
import { PasswordForm } from '@/features/settings/password-form'
import { PreferencesPanel } from '@/features/settings/preferences-panel'
import { ProfileForm } from '@/features/settings/profile-form'
import { SessionsPanel } from '@/features/settings/sessions-panel'
import { formatRelativeTime } from '@/features/auth/session-labels'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import type { ISODateTimeString } from '@/types'

/**
 * The sections, in tab order. The URL carries the selection as `?tab=`, so a
 * section is linkable (`/settings?tab=security`) and the browser's back and
 * forward buttons move between them like any other navigation.
 */
const SECTIONS = ['profile', 'account', 'security', 'sessions', 'preferences'] as const

type Section = (typeof SECTIONS)[number]

const DEFAULT_SECTION: Section = 'profile'

function isSection(value: string | null): value is Section {
  return SECTIONS.includes(value as Section)
}

/**
 * The panel measure, applied to every section. Bounding it is the point: a
 * settings form is read line by line, and inputs stretched across a 1300px
 * canvas cost more to scan than the whitespace beside them costs to look at.
 * The forms inside each card fill this width exactly, so no card is wider than
 * the fields it holds.
 */
const PANEL_MEASURE = 'max-w-2xl'

function formatDate(iso: ISODateTimeString | null): string {
  if (!iso) return 'Never'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function Detail({
  icon: Icon,
  label,
  children,
}: {
  icon: LucideIcon
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-3 py-3">
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="w-32 shrink-0 text-sm text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm text-foreground">
        {children}
      </div>
    </div>
  )
}

function AccountOverview() {
  const user = useAuthStore((state) => state.user)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Account</CardTitle>
        <CardDescription>
          The identity this account signs in with, and what the backend knows about it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          <Detail icon={UserRound} label="Username">
            <span className="font-mono text-sm">@{user?.username ?? '—'}</span>
          </Detail>
          <Detail icon={BadgeCheck} label="Email">
            <span className="truncate font-mono text-sm">{user?.email ?? 'Not signed in'}</span>
            <Badge variant={user?.is_verified ? 'success' : 'warning'}>
              {user?.is_verified ? 'Verified' : 'Not verified'}
            </Badge>
          </Detail>
          <Detail icon={ShieldCheck} label="Role">
            <span className="capitalize">{user?.role ?? '—'}</span>
            {user && user.permissions.length > 0 ? (
              <span className="text-xs text-muted-foreground">
                {user.permissions.length} permission
                {user.permissions.length === 1 ? '' : 's'}
              </span>
            ) : null}
          </Detail>
          <Detail icon={CalendarDays} label="Member since">
            <span>{formatDate(user?.created_at ?? null)}</span>
          </Detail>
          <Detail icon={KeyRound} label="Last sign-in">
            <span>{formatRelativeTime(user?.last_login_at ?? null)}</span>
          </Detail>
        </div>

        <div className="mt-6">
          <DangerZone />
        </div>
      </CardContent>
    </Card>
  )
}

export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // An absent or unrecognised `?tab=` lands on Profile rather than an empty
  // panel: the tablist is still rendered and still selected, so a stale or
  // hand-typed URL is never a dead end.
  const requested = searchParams.get('tab')
  const section: Section = isSection(requested) ? requested : DEFAULT_SECTION

  // Every switch is a history entry, including a return to the default, so back
  // and forward retrace the sections in the order they were visited.
  function selectSection(next: string) {
    if (next === DEFAULT_SECTION) {
      setSearchParams({})
      return
    }
    setSearchParams({ tab: next })
  }

  return (
    <div className="app-container py-6 lg:py-8">
      <PageHeader
        title="Settings"
        eyebrow="Platform"
        description={SETTINGS_MODULE.summary}
      />

      <Tabs value={section} onValueChange={selectSection} className={cn('mt-6', PANEL_MEASURE)}>
        {/* The list scrolls sideways on narrow screens rather than wrapping into
            a block that would push the panel off the page. */}
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList className="w-max" aria-label="Settings sections">
            <TabsTrigger value="profile">Profile</TabsTrigger>
            <TabsTrigger value="account">Account</TabsTrigger>
            <TabsTrigger value="security">Security</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="preferences">Preferences</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="profile" className="mt-6">
          <ProfileForm />
        </TabsContent>

        <TabsContent value="account" className="mt-6">
          <AccountOverview />
        </TabsContent>

        <TabsContent value="security" className="mt-6">
          <PasswordForm />
        </TabsContent>

        <TabsContent value="sessions" className="mt-6">
          <SessionsPanel />
        </TabsContent>

        <TabsContent value="preferences" className="mt-6">
          <PreferencesPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
