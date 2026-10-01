import type { ReactNode } from 'react'
import { Clock, Layers } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { NAV_GROUPS } from '@/features/modules/catalog'
import type { ModuleDefinition } from '@/features/modules/catalog'
import { cn } from '@/lib/utils'

export interface ModulePageProps {
  module: ModuleDefinition
  /** Module-specific content appended below the standard body. */
  children?: ReactNode
}

function groupLabelFor(module: ModuleDefinition): string {
  const group = NAV_GROUPS.find((candidate) =>
    candidate.items.some((item) => item.to === module.to),
  )
  return group?.label ?? 'Platform'
}

function MetricTile({ label, hint }: { label: string; hint: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {label}
        </p>
        <p className="mt-2 text-2xl font-semibold tabular-nums text-muted-foreground/50">—</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

/**
 * The body every not-yet-built module renders. It states what the module is for,
 * when it ships and what will be possible — so an empty screen explains itself
 * instead of apologising.
 */
export function ModulePage({ module, children }: ModulePageProps) {
  const Icon = module.icon

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        eyebrow={groupLabelFor(module)}
        title={module.label}
        description={module.summary}
        badges={
          <Badge variant="outline" className="gap-1 font-normal">
            <Clock aria-hidden="true" />
            Planned — Phase {module.phase}
          </Badge>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {module.metrics.map((metric) => (
          <MetricTile key={metric.label} label={metric.label} hint={metric.hint} />
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader>
            <div className="flex items-center gap-2.5">
              <span className="flex size-7 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <CardTitle>What {module.label.toLowerCase()} will do</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm leading-relaxed text-muted-foreground">{module.vision}</p>

            <Separator />

            <ul className="divide-y divide-border">
              {module.capabilities.map((capability) => (
                <li key={capability.title} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                  <span
                    aria-hidden="true"
                    className="mt-1 size-1.5 shrink-0 rounded-full bg-border"
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{capability.title}</p>
                    <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">
                      {capability.description}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card className={cn('lg:col-span-5')}>
          <CardHeader>
            <CardTitle>Available today</CardTitle>
            <CardDescription>
              Nothing is stored here until Phase {module.phase}.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={Layers}
              title={`No ${module.label} records yet`}
              description="This surface is wired up and waiting for its backend module. Until then there is nothing to show, and no sample data pretending otherwise."
            />
          </CardContent>
        </Card>
      </div>

      {children}
    </div>
  )
}