import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CircleDot, Flag, Layers, ShieldQuestion, Tag } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { LevelSourceBadge } from '@/features/learning/components/learning-badges'
import {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from '@/features/learning/components/learning-empty-state'
import {
  describeEvidenceCount,
  describeLastActivity,
  describeLevelClaim,
} from '@/features/learning/components/learning-format'
import {
  LEARNING_LEVEL_SCALE,
  LEVEL_SOURCE_META,
  NOT_ENOUGH_DATA_TITLE,
} from '@/features/learning/components/learning-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { SkillRead } from '@/types/learning'

/**
 * One skill, and the level on it.
 *
 * ## The level is never a bare number
 *
 * This is the component the whole phase's honesty rule lives in. A skill card
 * has no way to render `3/5` on its own: `describeLevelClaim` takes the
 * `level_source` and produces `"3 of 5, self-assessed by you"` or
 * `"3 of 5, estimated by NEXUS from recorded activities"`, and a `LevelSourceBadge`
 * sits beside it. There is no prop to switch either off. The alternative — a
 * number whose provenance a caller could drop — is a number that would be
 * dropped eventually, and "3/5" on its own is a verdict about a person.
 *
 * ## An estimate shows its working, and a self-assessment does not need to
 *
 * `level_source: 'system_estimate'` rows carry `confidence` and
 * `evidence_count`, and this card renders both plus the sentence explaining what
 * an estimate is derived from. A `user_defined` level carries `confidence: 0` —
 * a real measurement meaning "nothing is inferred here" — and it is **not**
 * rendered as a confidence of zero percent, because that would read as "NEXUS
 * thinks this claim is worthless". The evidence count is shown either way: it is
 * a count of records, not a grade.
 *
 * ## A zero evidence count is a measurement
 *
 * `evidence_count` is a plain `number`, so `0` means nothing has been recorded
 * and is said in words. It is never a dash, and never confused with the
 * insufficient-data state, which arrives as `available: false` plus a reason on
 * the *gap* payload rather than here.
 */
export interface SkillLevelMeterProps {
  level: number
  /** How many cells the scale has. Defaults to the 1–5 the backend enforces. */
  scale?: number
  /** Colour of the filled cells; the sentence beside the meter is the truth. */
  className?: string
}

/**
 * The decorative level meter.
 *
 * `aria-hidden` and no text of its own: five filled squares read as a rating,
 * which is the one thing this surface must not do, so the sentence immediately
 * after it carries the meaning and the pips are only there to make the levels
 * orderable at a glance. A reader who cannot see them loses nothing the sentence
 * did not already say.
 */
export function SkillLevelMeter({
  level,
  scale = LEARNING_LEVEL_SCALE,
  className,
}: SkillLevelMeterProps) {
  const cells = Number.isFinite(scale) && scale > 0 ? Math.trunc(scale) : LEARNING_LEVEL_SCALE
  const filled = Number.isFinite(level) ? Math.min(Math.max(Math.trunc(level), 0), cells) : 0

  return (
    <span aria-hidden="true" className={cn('flex items-center gap-0.5', className)}>
      {Array.from({ length: cells }, (_, index) => (
        <span
          key={index}
          className={cn(
            'size-2 rounded-[2px]',
            index < filled ? 'bg-chart-1' : 'bg-muted',
          )}
        />
      ))}
    </span>
  )
}

export interface SkillCardProps {
  skill: SkillRead
  /** Where this skill's own page lives. Omitted renders a read-only card. */
  href?: string | null
  /** The page's own row actions, rendered under the card body. */
  actions?: ReactNode
  titleLevel?: 'h3' | 'h4'
  className?: string
}

export function SkillCard({
  skill,
  href = null,
  actions,
  titleLevel = 'h3',
  className,
}: SkillCardProps) {
  const source = LEVEL_SOURCE_META[skill.level_source]
  const estimate = skill.level_source === 'system_estimate'

  const title = (
    <CardTitle level={titleLevel} className="line-clamp-2 text-base leading-snug">
      {skill.name}
    </CardTitle>
  )

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            {href ? (
              <Link
                to={href}
                className={cn(
                  'block min-w-0 rounded-sm font-semibold leading-snug tracking-tight',
                  'hover:underline focus-visible:outline-none focus-visible:ring-2',
                  'focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                )}
              >
                {title}
              </Link>
            ) : (
              title
            )}
          </div>
          <span className="flex shrink-0 items-center gap-1 truncate text-xs text-muted-foreground">
            <Tag aria-hidden="true" className="size-3" />
            {skill.category ? (
              <span className="truncate">{skill.category}</span>
            ) : (
              <span>No category</span>
            )}
          </span>
        </div>

        {skill.description && (
          <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">
            {skill.description}
          </p>
        )}
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-1.5" aria-labelledby={`skill-${skill.id}-level`}>
          <h4
            id={`skill-${skill.id}-level`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Current level
          </h4>
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <SkillLevelMeter level={skill.current_level} />
            <span className="font-medium text-foreground">
              {describeLevelClaim(skill.current_level, skill.level_source)}
            </span>
            <LevelSourceBadge source={skill.level_source} size="sm" />
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">{source.description}</p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`skill-${skill.id}-target`}>
          <h4
            id={`skill-${skill.id}-target`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Target level
          </h4>
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <SkillLevelMeter level={skill.target_level} />
            <span className="font-medium text-foreground">
              {formatNumber(skill.target_level)} of {formatNumber(LEARNING_LEVEL_SCALE)}
            </span>
            <span className="text-muted-foreground">— the target you set for this skill.</span>
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`skill-${skill.id}-evidence`}>
          <h4
            id={`skill-${skill.id}-evidence`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Evidence
          </h4>
          <p className="flex min-w-0 items-start gap-1.5 text-sm">
            <Layers aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 text-foreground">{describeEvidenceCount(skill.evidence_count)}</span>
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {describeLastActivity(skill.last_activity_at)}
          </p>
          {estimate ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              Estimate confidence {formatNumber(skill.confidence)}% — how much recorded evidence
              backs the derivation, not how good the skill is.
            </p>
          ) : (
            <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
              <ShieldQuestion aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
              <span>
                No estimate has been made for this skill. A level you set is recorded as your claim,
                and NEXUS attaches no confidence figure to it.
              </span>
            </p>
          )}
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
 * The skill card's silhouette.
 *
 * **No digits, and no filled meter cells.** The real card's level is a five-cell
 * meter beside a sentence naming who set the level, so a skeleton that drew
 * three filled squares would be publishing a level nobody has claimed. The
 * blocks here are all `bg-muted` for the same reason a placeholder `0` is
 * avoided everywhere else on this surface.
 */
export function SkillCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="space-y-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <Skeleton className="h-4 w-16 rounded-md" />
        </div>
        <Skeleton className="h-3 w-4/5" />
      </CardHeader>

      <CardContent className="space-y-4">
        {[0, 1, 2].map((section) => (
          <div key={section} className="space-y-2">
            <Skeleton className="h-2.5 w-24" />
            <div className="flex items-center gap-2">
              <div className="flex gap-0.5">
                {Array.from({ length: LEARNING_LEVEL_SCALE }, (_, index) => (
                  <div key={index} className="size-2 rounded-[2px] bg-muted" />
                ))}
              </div>
              <Skeleton className="h-3.5 w-2/3" />
            </div>
            <Skeleton className="h-3 w-full" />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

export interface SkillCardGridSkeletonProps {
  count?: number
  className?: string
}

/** The skill grid while its first read is in flight. */
export function SkillCardGridSkeleton({
  count = 6,
  className,
}: SkillCardGridSkeletonProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-3', className)}
    >
      <span className="sr-only">Loading skills</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <SkillCardSkeleton key={index} />
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------------- grid */

export interface SkillCardGridProps {
  skills: readonly SkillRead[]
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  buildHref?: (skill: SkillRead) => string | null
  renderActions?: (skill: SkillRead) => ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The skill grid, with its loading, empty, error and stale states.
 *
 * `min-w-0` on the grid and on every card: a skill name is free text, and
 * without it one long name pushes its column — and on a phone the page — into a
 * horizontal scroll.
 */
export function SkillCardGrid({
  skills,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  buildHref,
  renderActions,
  skeletonCount = 6,
  titleLevel = 'h3',
  className,
}: SkillCardGridProps) {
  if (isLoading) {
    return <SkillCardGridSkeleton count={skeletonCount} className={className} />
  }

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <LearningRegionError error={error} onRetry={onRetry} subject="the skill list" />
      </div>
    )
  }

  if (skills.length === 0) {
    return (
      <LearningEmptyState
        variant="skills"
        reason={emptyReason}
        action={emptyAction}
        className={cn('rounded-lg border border-border bg-card', 'min-h-[16rem]', className)}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <LearningStaleNotice isStale={isStale} subject="the skill list" />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {skills.map((skill) => (
          <SkillCard
            key={skill.id}
            skill={skill}
            href={buildHref ? buildHref(skill) : null}
            actions={renderActions?.(skill)}
            titleLevel={titleLevel}
          />
        ))}
      </div>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CircleDot aria-hidden="true" className="size-3" />
        {formatNumber(skills.length)} {skills.length === 1 ? 'skill' : 'skills'} shown.
      </p>
    </div>
  )
}

/**
 * The figure a card shows when a level could not be read at all.
 *
 * Exported because the gap list needs the same words for a skill whose levels
 * could not be compared: the sentence names the state and carries **no figure**,
 * because printing a dash's neighbour next to it would be a number the backend
 * declined to produce.
 */
export function SkillLevelUnavailableNote({ reason }: { reason?: string | null }) {
  return (
    <>
      <p className="text-sm font-semibold leading-snug text-muted-foreground">
        {NOT_ENOUGH_DATA_TITLE}
      </p>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {reason && reason.trim().length > 0
          ? reason
          : 'No level has been recorded for this skill, so no level is shown.'}
      </p>
    </>
  )
}

/**
 * A target-vs-current line, reused by the gap list and the development-areas
 * panel.
 *
 * **The source is part of the line, not a decoration beside it**, which is why
 * this is one function rather than two props: the moment the claim and the
 * attribution are rendered by different call sites, one of them stops.
 */
export function SkillLevelComparison({
  currentLevel,
  targetLevel,
  levelSource,
  className,
}: {
  currentLevel: number
  targetLevel: number
  levelSource: SkillRead['level_source']
  className?: string
}) {
  return (
    <p className={cn('flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm', className)}>
      <Flag aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="font-medium text-foreground">
        Current {describeLevelClaim(currentLevel, levelSource)}
      </span>
      <span aria-hidden="true" className="text-muted-foreground">·</span>
      <span className="tabular-nums text-muted-foreground">
        target {formatNumber(targetLevel)} / {formatNumber(LEARNING_LEVEL_SCALE)}, the level you set
      </span>
      <LevelSourceBadge source={levelSource} size="sm" />
    </p>
  )
}