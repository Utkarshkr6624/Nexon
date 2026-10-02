import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowUpRight,
  BrainCircuit,
  CheckCircle2,
  Clock,
  Database,
  FlaskConical,
  FolderKanban,
  ListTodo,
  RefreshCw,
  Server,
  Settings,
  Sparkles,
} from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { useHealth } from '@/features/health/use-health'
import { MODULES, SETTINGS_MODULE, getModule } from '@/features/modules/catalog'
import {
  formatLatency,
  formatRelative,
  formatTimestamp,
  formatUptime,
  greetingFor,
  longDate,
} from '@/features/health/format'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import { toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'

interface KpiCardProps {
  label: string
  value: string
  hint: string
  icon: LucideIcon
  live?: boolean
  tone?: 'default' | 'success' | 'warning' | 'muted'
}

function KpiCard({ label, value, hint, icon: Icon, live = false, tone = 'default' }: KpiCardProps) {
  return (
    <Card className="overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            {label}
          </p>
          <span
            aria-hidden="true"
            className={cn(
              'flex size-7 shrink-0 items-center justify-center rounded-md border border-border',
              tone === 'muted' ? 'bg-muted text-muted-foreground' : 'bg-muted text-foreground',
            )}
          >
            <Icon className="size-3.5" />
          </span>
        </div>

        <p
          className={cn(
            'mt-2 text-2xl font-semibold tabular-nums tracking-tight',
            tone === 'muted' ? 'text-muted-foreground/50' : 'text-foreground',
          )}
        >
          {value}
        </p>

        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>

        {live && (
          <span className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-medium text-success">
            <span className="size-1.5 animate-pulse rounded-full bg-success" aria-hidden="true" />
            Live
          </span>
        )}
      </CardContent>
    </Card>
  )
}

function HealthRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-xs text-foreground">{value}</dd>
    </div>
  )
}

const ACTIVITY_SAMPLE: ReadonlyArray<{
  icon: LucideIcon
  title: string
  detail: string
  when: string
}> = [
  {
    icon: FolderKanban,
    title: 'Project moved to review',
    detail: 'Atlas rewrite · 4 tasks closed',
    when: 'Today',
  },
  {
    icon: ListTodo,
    title: 'Task completed',
    detail: 'Close out the release checklist',
    when: 'Yesterday',
  },
  {
    icon: BrainCircuit,
    title: 'Note linked',
    detail: 'Linked to 2 decisions in Atlas rewrite',
    when: 'Yesterday',
  },
  {
    icon: Clock,
    title: 'Focus block logged',
    detail: '2h 30m · deep work',
    when: 'Monday',
  },
  {
    icon: FlaskConical,
    title: 'Experiment kept',
    detail: 'Local-first sync prototype',
    when: 'Monday',
  },
]

/**
 * The module build sequence, read from the registry rather than restated here:
 * one row per phase, naming the destinations that ship in it. Deriving it means
 * a module added to `catalog.ts` cannot quietly go missing from this list.
 */
const BUILD_SEQUENCE: ReadonlyArray<{ phase: number; modules: string; live: boolean }> = (() => {
  const byPhase = new Map<number, string[]>()
  for (const module of MODULES) {
    const existing = byPhase.get(module.phase)
    if (existing) existing.push(module.label)
    else byPhase.set(module.phase, [module.label])
  }
  // Settings shares the Dashboard's phase and is not in `MODULES`, so it is
  // named alongside it rather than leaving that row one destination short.
  byPhase.set(1, [getModule('/dashboard').label, SETTINGS_MODULE.label])
  return [...byPhase.entries()]
    .sort(([a], [b]) => a - b)
    .map(([phase, labels]) => ({ phase, modules: labels.join(', '), live: phase === 1 }))
})()

const QUICK_ACTIONS: ReadonlyArray<{ to: string; label: string; icon: LucideIcon }> = [
  { to: '/projects', label: 'Review projects', icon: FolderKanban },
  { to: '/tasks', label: 'Triage tasks', icon: ListTodo },
  { to: '/knowledge', label: 'Open knowledge base', icon: BrainCircuit },
  { to: '/assistant', label: 'Ask the assistant', icon: Sparkles },
]

export default function DashboardPage() {
  const user = useAuthStore((state) => state.user)
  const { data, error, isPending, isFetching, dataUpdatedAt, refetch } = useHealth()

  const now = useMemo(() => new Date(), [])
  const apiError = error ? toApiError(error) : null
  const hasData = data !== undefined

  const dbConnected = data?.database.status === 'connected'
  const degraded = data?.status === 'degraded'

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            {longDate(now)}
          </p>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            {greetingFor(now)}, {selectDisplayName(user)}
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            The workspace foundation and the full account system are live: sign-in, sessions,
            password changes and account deletion. Backend health is on this page; every other
            module is wired and waiting for the phase it ships in.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button type="button" variant="outline" asChild>
            <Link to="/tasks">
              Open tasks
              <ArrowUpRight aria-hidden="true" />
            </Link>
          </Button>
          <Button type="button" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
            Refresh status
          </Button>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Backend status"
          icon={Server}
          live
          tone={hasData ? (degraded ? 'warning' : 'success') : 'muted'}
          value={hasData ? data.status : isPending ? '—' : 'Offline'}
          hint={
            hasData
              ? `v${data.version} · ${data.environment}`
              : 'Waiting for GET /api/v1/health'
          }
        />
        <KpiCard
          label="Database latency"
          icon={Database}
          live={hasData}
          tone={hasData ? (dbConnected ? 'success' : 'warning') : 'muted'}
          value={hasData ? (dbConnected ? formatLatency(data.database.latency_ms) : 'Down') : '—'}
          hint={hasData ? `PostgreSQL ${data.database.status}` : 'No round trip recorded yet'}
        />
        <KpiCard
          label="Workspace records"
          icon={FolderKanban}
          tone="muted"
          value="—"
          hint="Projects, tasks and notes start storing records as those modules ship"
        />
        <KpiCard
          label="Decisions to review"
          icon={BrainCircuit}
          tone="muted"
          value="—"
          hint="The decision log arrives with the Knowledge module in Phase 4"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle>Backend health</CardTitle>
              <CardDescription>
                Live from <span className="font-mono">GET /api/v1/health</span>
              </CardDescription>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Refresh health"
              onClick={() => void refetch()}
              disabled={isFetching}
            >
              {isFetching ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
            </Button>
          </CardHeader>

          <CardContent>
            {isPending && (
              <div className="space-y-3">
                {Array.from({ length: 5 }, (_, index) => (
                  <div key={index} className="flex items-center justify-between gap-4 py-2">
                    <Skeleton className="h-3.5 w-28" />
                    <Skeleton className="h-3.5 w-36" />
                  </div>
                ))}
              </div>
            )}

            {!isPending && apiError && !hasData && (
              <ErrorState error={apiError} onRetry={() => void refetch()} compact />
            )}

            {hasData && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    variant={degraded ? 'warning' : 'success'}
                    className="gap-1.5 uppercase tracking-[0.08em]"
                  >
                    <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
                    {data.status}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    Checked {formatRelative(dataUpdatedAt)}
                  </span>
                </div>

                <dl className="mt-3 divide-y divide-border">
                  <HealthRow label="Application" value={data.app} />
                  <HealthRow label="Version" value={data.version} />
                  <HealthRow label="Environment" value={data.environment} />
                  <HealthRow
                    label="Database"
                    value={dbConnected ? 'connected' : 'unavailable'}
                  />
                  <HealthRow label="Round trip" value={formatLatency(data.database.latency_ms)} />
                  <HealthRow label="Uptime" value={formatUptime(data.uptime_seconds)} />
                  <HealthRow label="Reported at" value={formatTimestamp(data.timestamp)} />
                </dl>

                {apiError && (
                  <p className="mt-3 text-xs text-muted-foreground">
                    The most recent refresh failed; showing the last successful reading.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader>
            <CardTitle>Quick actions</CardTitle>
            <CardDescription>Shortcuts into the surfaces you will use most.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5">
              {QUICK_ACTIONS.map((action) => (
                <li key={action.to}>
                  <Link
                    to={action.to}
                    className="group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm text-muted-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                  >
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground transition-colors duration-150 ease-out group-hover:text-foreground">
                      <action.icon className="size-3.5" aria-hidden="true" />
                    </span>
                    <span className="flex-1 truncate">{action.label}</span>
                    <ArrowUpRight
                      className="size-3.5 shrink-0 text-muted-foreground/60"
                      aria-hidden="true"
                    />
                  </Link>
                </li>
              ))}
              <li>
                <Link
                  to="/settings"
                  className="group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm text-muted-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                >
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground transition-colors duration-150 ease-out group-hover:text-foreground">
                    <Settings className="size-3.5" aria-hidden="true" />
                  </span>
                  <span className="flex-1 truncate">Adjust preferences</span>
                  <ArrowUpRight
                    className="size-3.5 shrink-0 text-muted-foreground/60"
                    aria-hidden="true"
                  />
                </Link>
              </li>
            </ul>

            <Separator className="my-4" />

            <p className="text-xs leading-relaxed text-muted-foreground">
              Creating and editing flows ship with each module. Until then these routes land on
              the module brief so you can see exactly what is coming.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle>Recent activity</CardTitle>
              <CardDescription>
                Illustrative entries. Real activity is recorded as modules start storing events.
              </CardDescription>
            </div>
            <Badge variant="secondary" className="font-normal">
              Sample
            </Badge>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {ACTIVITY_SAMPLE.map((entry) => (
                <li key={entry.title} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                  <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
                    <entry.icon className="size-3.5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{entry.title}</p>
                    <p className="truncate text-xs text-muted-foreground">{entry.detail}</p>
                  </div>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {entry.when}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader>
            <CardTitle>Build sequence</CardTitle>
            <CardDescription>
              Every destination in the sidebar, ordered by the phase it ships in.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {BUILD_SEQUENCE.map((entry) => (
                <li key={entry.phase} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
                  <span
                    className={cn(
                      'flex size-6 shrink-0 items-center justify-center rounded-md border text-[11px] font-semibold tabular-nums',
                      entry.live
                        ? 'border-transparent bg-primary/15 text-primary'
                        : 'border-border bg-muted text-muted-foreground',
                    )}
                  >
                    {entry.phase}
                  </span>
                  <span className="flex-1 truncate text-sm text-foreground">{entry.modules}</span>
                  {entry.live && (
                    <>
                      <CheckCircle2 className="size-3.5 shrink-0 text-success" aria-hidden="true" />
                      <span className="sr-only">Available now</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}