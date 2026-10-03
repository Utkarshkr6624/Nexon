import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CalendarClock, CheckCircle2, FolderKanban, Gauge, NotebookPen, Sparkles, Timer } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { GoalPriorityBadge, GoalStatusBadge } from '@/features/learning/components/learning-badges'
import {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from '@/features/learning/components/learning-empty-state'
import {
  describeProgress,
  describeTargetDate,
  formatLearningInstant,
} from '@/features/learning/components/learning-format'
import { formatMinutes, formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { LearningGoalRead, UUIDString } from '@/types/learning'

/**
 * One learning goal and the state the user put it in.
 *
 * ## What this card is allowed to say
 *
 * **Every number on it is the user's.** `progress` is a percentage the person
 * set or moved; NEXUS does not compute it from activity, because a percentage
 * derived from the *absence* of a record would be a claim about commitment
 * rather than about progress, and the sentence beside the bar says so on every
 * card. `estimated_effort_minutes` is the same: their own estimate, absent when
 * they gave none, and the card says "No estimate recorded" rather than
 * supplying a plausible-looking one.
 *
 * ## The nulls here are ordinary states, not failures
 *
 * - `target_date: null` means **no deadline was set**, which is a legitimate
 *   choice and reads as one. It is never rendered as "0 days remaining".
 * - `target_skill_id` and `target_topic` are two forms of the same idea and
 *   either may be null — a goal can name a topic before the skill exists, and
 *   the foreign key is `ON DELETE SET NULL`, so it can also be set aside later.
 *   "Neither named" is a real state and says what fills it.
 * - `project_id` and `note_id` are links. Their absence says something about
 *   linkage and nothing about the goal's value.
 * - `completed_at` is set by the complete route and is null for every other
 *   status, so it is only rendered when the status agrees with it.
 *
 * ## `min-w-0` because the title is unbounded
 *
 * A goal title is free text with a 200-character limit and no width constraint,
 * so the card, the header and the title all carry `min-w-0` and `truncate`.
 * Without it a single long title pushes its grid column — and on a phone the
 * page itself — into a horizontal scroll.
 */
export interface LearningGoalCardProps {
  goal: LearningGoalRead
  /** Where this goal's own page lives. Omitted renders a read-only card. */
  href?: string | null
  /** Resolved name of the linked skill, when the caller already has it. */
  skillName?: string | null
  skillHref?: string | null
  projectName?: string | null
  projectHref?: string | null
  noteHref?: string | null
  /** The page's own row actions, rendered under the card body. */
  actions?: ReactNode
  titleLevel?: 'h3' | 'h4'
  className?: string
}

export function LearningGoalCard({
  goal,
  href = null,
  skillName = null,
  skillHref = null,
  projectName = null,
  projectHref = null,
  noteHref = null,
  actions,
  titleLevel = 'h3',
  className,
}: LearningGoalCardProps) {
  const subject = goal.target_skill_id
    ? (skillName ?? 'A skill that is no longer tracked')
    : (goal.target_topic ?? null)
  const progressValue = Number.isFinite(goal.progress) ? Math.min(Math.max(goal.progress, 0), 100) : 0

  const title = (
    <CardTitle level={titleLevel} className="line-clamp-2 text-base leading-snug">
      {goal.title}
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
          <div className="flex shrink-0 items-center gap-1.5">
            <GoalStatusBadge status={goal.status} size="sm" />
            <GoalPriorityBadge priority={goal.priority} size="sm" />
          </div>
        </div>

        {goal.description && (
          <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">
            {goal.description}
          </p>
        )}
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-2" aria-labelledby={`goal-${goal.id}-progress`}>
          <h4
            id={`goal-${goal.id}-progress`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Progress
          </h4>
          <Progress value={progressValue} aria-label={`${goal.title} progress`} />
          <p className="text-xs leading-relaxed text-muted-foreground">{describeProgress(goal)}</p>
          <p className="flex items-center gap-1.5 text-sm">
            <Gauge aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium tabular-nums text-foreground">
              {formatNumber(progressValue)}%
            </span>
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`goal-${goal.id}-target`}>
          <h4
            id={`goal-${goal.id}-target`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Target
          </h4>
          <p className="flex min-w-0 items-start gap-1.5 text-sm">
            <CalendarClock aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 text-foreground">{describeTargetDate(goal.target_date)}</span>
          </p>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Timer aria-hidden="true" className="size-3 shrink-0" />
            {goal.estimated_effort_minutes === null ? (
              <span>No effort estimate recorded — the estimate on a goal is yours, and none was given.</span>
            ) : (
              <span>
                {formatMinutes(goal.estimated_effort_minutes)} of estimated effort, your own estimate.
              </span>
            )}
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`goal-${goal.id}-subject`}>
          <h4
            id={`goal-${goal.id}-subject`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Learning
          </h4>
          {subject ? (
            skillHref ? (
              <Link
                to={skillHref}
                className="inline-flex min-w-0 items-center gap-1.5 truncate text-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Sparkles aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">
                  {skillName ? `Skill: ${skillName}` : `Topic: ${subject}`}
                </span>
              </Link>
            ) : (
              <p className="flex min-w-0 items-center gap-1.5 text-sm">
                <Sparkles aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate font-medium text-foreground">
                  {skillName ? `Skill: ${skillName}` : `Topic: ${subject}`}
                </span>
              </p>
            )
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              No skill or topic named yet. A goal can name a tracked skill or describe its subject
              in words, and neither is required.
            </p>
          )}
        </section>

        <section className="space-y-1.5" aria-labelledby={`goal-${goal.id}-linked`}>
          <h4
            id={`goal-${goal.id}-linked`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Linked to
          </h4>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li className="flex min-w-0 items-center gap-1.5">
              <FolderKanban aria-hidden="true" className="size-3 shrink-0" />
              {projectName ? (
                projectHref ? (
                  <Link
                    to={projectHref}
                    className="min-w-0 truncate font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Project: {projectName}
                  </Link>
                ) : (
                  <span className="min-w-0 truncate font-medium text-foreground">
                    Project: {projectName}
                  </span>
                )
              ) : (
                <span>Not linked to a project. The goal outlives the project it was attached to.</span>
              )}
            </li>
            <li className="flex min-w-0 items-center gap-1.5">
              <NotebookPen aria-hidden="true" className="size-3 shrink-0" />
              {goal.note_id && noteHref ? (
                <Link
                  to={noteHref}
                  className="min-w-0 truncate font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Linked note
                </Link>
              ) : (
                <span>Not linked to a note.</span>
              )}
            </li>
            {goal.completed_at && (
              <li className="flex min-w-0 items-center gap-1.5">
                <CheckCircle2 aria-hidden="true" className="size-3 shrink-0 text-success" />
                <span>
                  Completed on{' '}
                  <time dateTime={goal.completed_at}>{formatLearningInstant(goal.completed_at)}</time>.
                </span>
              </li>
            )}
          </ul>
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
 * The goal card's silhouette.
 *
 * **It reserves the blocks the real card occupies** — header, two badges, three
 * labelled sections and a progress bar — so the grid does not reflow when the
 * data lands. **Nothing here reads as a value**: the skeletons are grey blocks
 * with no digits and no half-filled progress bar, because a pulse in the shape
 * of "40%" is a number to anyone glancing at it, and on this surface a figure
 * that is not there is never a zero.
 */
export function LearningGoalCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="space-y-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-3/5" />
            <Skeleton className="h-3 w-full" />
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Skeleton className="h-4 w-20 rounded-md" />
            <Skeleton className="h-4 w-16 rounded-md" />
          </div>
        </div>
        <Skeleton className="h-3 w-4/5" />
      </CardHeader>

      <CardContent className="space-y-4">
        {[0, 1, 2].map((section) => (
          <div key={section} className="space-y-2">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="h-2 w-full rounded-full" />
            <Skeleton className="h-3.5 w-2/3" />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

export interface LearningGoalCardGridSkeletonProps {
  /** How many card silhouettes to draw. */
  count?: number
  className?: string
}

/** The grid while its first read is in flight: the real cards' outlines, empty. */
export function LearningGoalCardGridSkeleton({
  count = 4,
  className,
}: LearningGoalCardGridSkeletonProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-3', className)}
    >
      <span className="sr-only">Loading learning goals</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <LearningGoalCardSkeleton key={index} />
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------------- grid */

export interface LearningGoalCardGridProps {
  goals: readonly LearningGoalRead[]
  isLoading?: boolean
  /** A refetch is in flight behind rows already on screen. */
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  /** Usually the page's "New goal" button. */
  emptyAction?: ReactNode
  /** Builds each card's link. Omit for a read-only grid. */
  buildHref?: (goal: LearningGoalRead) => string | null
  skillNames?: Readonly<Record<UUIDString, string>>
  skillHrefs?: Readonly<Record<UUIDString, string>>
  projectNames?: Readonly<Record<UUIDString, string>>
  projectHrefs?: Readonly<Record<UUIDString, string>>
  /** Per-card actions, when the caller has any. */
  renderActions?: (goal: LearningGoalRead) => ReactNode
  /** Silhouettes to draw while loading. */
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The goal grid, with its loading, empty, error and stale states.
 *
 * **The grid is the loading state.** Returning `null` while the first read is in
 * flight would collapse the page to its header and then push everything back
 * down as the cards arrived; drawing the real grid's silhouettes keeps the column
 * count visible so the layout does not move when the rows land.
 *
 * `min-w-0` on the grid *and* on every card in it: a goal title is the one
 * string here with no length limit.
 *
 * The name maps are read with `?.` rather than `??`, so a goal whose skill the
 * caller has not resolved falls through to the card's own "no longer tracked"
 * sentence instead of being drawn as a skill with no name.
 */
export function LearningGoalCardGrid({
  goals,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  buildHref,
  skillNames,
  skillHrefs,
  projectNames,
  projectHrefs,
  renderActions,
  skeletonCount = 4,
  titleLevel = 'h3',
  className,
}: LearningGoalCardGridProps) {
  if (isLoading) {
    return <LearningGoalCardGridSkeleton count={skeletonCount} className={className} />
  }

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <LearningRegionError error={error} onRetry={onRetry} subject="the learning goals" />
      </div>
    )
  }

  if (goals.length === 0) {
    return (
      <LearningEmptyState
        variant="goals"
        reason={emptyReason}
        action={emptyAction}
        className={cn(
          'rounded-lg border border-border bg-card',
          'min-h-[16rem]',
          className,
        )}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <LearningStaleNotice isStale={isStale} subject="the goal list" />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {goals.map((goal) => (
          <LearningGoalCard
            key={goal.id}
            goal={goal}
            href={buildHref ? buildHref(goal) : null}
            skillName={goal.target_skill_id ? (skillNames?.[goal.target_skill_id] ?? null) : null}
            skillHref={goal.target_skill_id ? (skillHrefs?.[goal.target_skill_id] ?? null) : null}
            projectName={goal.project_id ? (projectNames?.[goal.project_id] ?? null) : null}
            projectHref={goal.project_id ? (projectHrefs?.[goal.project_id] ?? null) : null}
            actions={renderActions?.(goal)}
            titleLevel={titleLevel}
          />
        ))}
      </div>

      <p className="text-xs text-muted-foreground">
        {formatNumber(goals.length)} {goals.length === 1 ? 'goal' : 'goals'} shown. Archived goals
        are listed here but are not counted among the goals still open.
      </p>
    </div>
  )
}