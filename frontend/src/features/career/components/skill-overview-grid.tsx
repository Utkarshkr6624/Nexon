import type { ReactNode } from 'react'
import { CircleDot, Flag, Layers, Timer } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { LevelOriginBadge } from '@/features/career/components/career-badges'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import {
  describeCareerEvidence,
  describeCareerLastActivity,
  describeCareerLevel,
  formatCareerLevel,
} from '@/features/career/components/career-format'
import { CAREER_LEVEL_SCALE, CAREER_LEVEL_SOURCE_META } from '@/features/career/components/career-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { SkillRead } from '@/types/learning'

/**
 * The tracked skills, as a career profile shows them.
 *
 * ## Current and target, and the source of each
 *
 * A career profile is read by someone deciding whether to trust it, so every
 * tile shows both levels **and who set each one**: the current level goes out
 * through `describeCareerLevel`, which cannot render a bare number, and a
 * `LevelOriginBadge` sits beside it. The target is rendered as "the level you
 * set", because it is the same kind of claim and the tile should not make the
 * current one look more authoritative than the target.
 *
 * ## Evidence is a count of records
 *
 * `evidence_count` is how many learning activities were recorded against the
 * skill, all time. Zero is a real measurement and is said in words; a dash is
 * reserved for a figure the response did not carry. Nothing here converts that
 * count into a claim about ability.
 *
 * ## Levels are never compared across skills
 *
 * The grid is not sorted by level, not coloured by level and offers no "strongest
 * skill". A 5/5 in one row and a 1/5 in the next is two claims the user made on
 * different scales, and ranking them would be a comparison NEXUS has no standing
 * to make — the order is the order the caller passed in.
 */
export interface CareerLevelMeterProps {
  level: number
  scale?: number
  className?: string
}

/**
 * The decorative level meter beside a number.
 *
 * `aria-hidden` and no text of its own: five filled squares read as a rating,
 * which is the one thing this surface must not do, so the sentence beside the
 * meter carries the meaning and a reader who cannot see it loses nothing.
 */
export function CareerLevelMeter({
  level,
  scale = CAREER_LEVEL_SCALE,
  className,
}: CareerLevelMeterProps) {
  const cells = Number.isFinite(scale) && scale > 0 ? Math.trunc(scale) : CAREER_LEVEL_SCALE
  const filled = Number.isFinite(level) ? Math.min(Math.max(Math.trunc(level), 0), cells) : 0

  return (
    <span aria-hidden="true" className={cn('flex items-center gap-0.5', className)}>
      {Array.from({ length: cells }, (_, index) => (
        <span
          key={index}
          className={cn('size-2 rounded-[2px]', index < filled ? 'bg-chart-1' : 'bg-muted')}
        />
      ))}
    </span>
  )
}

export interface SkillOverviewTileProps {
  skill: SkillRead
  titleLevel?: 'h3' | 'h4'
  /** The page's own row actions, rendered under the body. */
  actions?: ReactNode
  className?: string
}

export function SkillOverviewTile({
  skill,
  titleLevel = 'h3',
  actions,
  className,
}: SkillOverviewTileProps) {
  const source = CAREER_LEVEL_SOURCE_META[skill.level_source]

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-2">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <CardTitle level={titleLevel} className="truncate text-sm leading-snug">
              {skill.name}
            </CardTitle>
          </div>
          {skill.category && (
            <span className="shrink-0 truncate text-xs text-muted-foreground">{skill.category}</span>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <section className="space-y-1.5" aria-labelledby={`overview-${skill.id}-current`}>
          <h4
            id={`overview-${skill.id}-current`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Current level
          </h4>
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <CareerLevelMeter level={skill.current_level} />
            <span className="font-medium text-foreground">
              {describeCareerLevel(skill.current_level, skill.level_source)}
            </span>
            <LevelOriginBadge source={skill.level_source} size="sm" />
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">{source.description}</p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`overview-${skill.id}-target`}>
          <h4
            id={`overview-${skill.id}-target`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Target level
          </h4>
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <Flag aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium tabular-nums text-foreground">
              {formatCareerLevel(skill.target_level)}
            </span>
            <span className="text-muted-foreground">— the level you set for this skill.</span>
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`overview-${skill.id}-evidence`}>
          <h4
            id={`overview-${skill.id}-evidence`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Evidence
          </h4>
          <p className="flex min-w-0 items-start gap-1.5 text-sm">
            <Layers aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 text-foreground">
              {describeCareerEvidence(skill.evidence_count)}
            </span>
          </p>
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <Timer aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
            <span>{describeCareerLastActivity(skill)}</span>
          </p>
        </section>

        {actions && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">{actions}</div>
        )}
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The tile grid's loading silhouette.
 *
 * **No digits and no filled meter cells.** The real tile shows a five-cell level
 * meter, so a skeleton with three cells lit would be publishing a level nobody
 * claimed. Every block is `bg-muted` for the same reason a placeholder `0` is
 * avoided everywhere else on this surface.
 */
export function SkillOverviewTileSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="space-y-2">
        <Skeleton className="h-4 w-2/3" />
      </CardHeader>
      <CardContent className="space-y-3">
        {[0, 1, 2].map((section) => (
          <div key={section} className="space-y-2">
            <Skeleton className="h-2.5 w-24" />
            <div className="flex items-center gap-2">
              <div className="flex gap-0.5">
                {Array.from({ length: CAREER_LEVEL_SCALE }, (_, index) => (
                  <div key={index} className="size-2 rounded-[2px] bg-muted" />
                ))}
              </div>
              <Skeleton className="h-3.5 w-3/5" />
            </div>
            <Skeleton className="h-3 w-full" />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

export interface SkillOverviewGridSkeletonProps {
  count?: number
  className?: string
}

/** The overview grid while its first read is in flight. */
export function SkillOverviewGridSkeleton({
  count = 6,
  className,
}: SkillOverviewGridSkeletonProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-3', className)}
    >
      <span className="sr-only">Loading tracked skills</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <SkillOverviewTileSkeleton key={index} />
      ))}
    </div>
  )
}

export interface SkillOverviewGridProps {
  skills: readonly SkillRead[]
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  renderActions?: (skill: SkillRead) => ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The overview grid, with its loading, empty, error and stale states.
 *
 * `min-w-0` on the grid *and* on every tile: a skill name is free text, and
 * without it one long name pushes its column — and on a phone the page — into a
 * horizontal scroll.
 */
export function SkillOverviewGrid({
  skills,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  renderActions,
  skeletonCount = 6,
  titleLevel = 'h3',
  className,
}: SkillOverviewGridProps) {
  if (isLoading) {
    return <SkillOverviewGridSkeleton count={skeletonCount} className={className} />
  }

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <CareerRegionError error={error} onRetry={onRetry} subject="the tracked skills" />
      </div>
    )
  }

  if (skills.length === 0) {
    return (
      <CareerEmptyState
        variant="skills"
        reason={emptyReason}
        action={emptyAction}
        className={cn('rounded-lg border border-border bg-card', 'min-h-[16rem]', className)}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <CareerStaleNotice isStale={isStale} subject="the skill overview" />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {skills.map((skill) => (
          <SkillOverviewTile
            key={skill.id}
            skill={skill}
            titleLevel={titleLevel}
            actions={renderActions?.(skill)}
          />
        ))}
      </div>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CircleDot aria-hidden="true" className="size-3" />
        {formatNumber(skills.length)} {skills.length === 1 ? 'skill' : 'skills'} shown, in the order
        they were listed. Levels are not ranked against each other.
      </p>
    </div>
  )
}