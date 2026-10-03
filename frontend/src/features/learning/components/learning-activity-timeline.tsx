import type { ReactNode } from 'react'
import { CircleDot, Link2, Target } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ActivityTypeBadge } from '@/features/learning/components/learning-badges'
import {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from '@/features/learning/components/learning-empty-state'
import {
  describeActivitySource,
  describeDuration,
  formatLearningClock,
  formatLearningInstant,
} from '@/features/learning/components/learning-format'
import { ACTIVITY_TYPE_META } from '@/features/learning/components/learning-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { LearningActivityRead, UUIDString } from '@/types/learning'

/**
 * The recorded learning trail, newest first.
 *
 * ## A row is a record, and it says which one
 *
 * Every row names its **activity type** and **where it came from**, and neither
 * is folded into the other. `activity_type` says what kind of event was recorded
 * — `task_completed` is the *name of an event type*, and a row that rendered it
 * as "you completed a task" would be claiming an outcome the record does not
 * carry. `source_type`/`source_id` say whether the person typed the activity in
 * or whether NEXUS derived it from a task, note, project or repository scan;
 * **derived is never presented as typed**, because that would claim a provenance
 * the row does not have.
 *
 * ## A null duration means the activity was an event, not a zero
 *
 * `duration_minutes` is null for a resource that was opened or a concept that
 * was recorded, and those things genuinely have no length. The row says so in
 * words ("Recorded as an event — no duration was attached to it") rather than
 * printing a dash beside a column header that promises minutes, and it never
 * prints `0m`.
 *
 * ## The trail outlives its links
 *
 * `skill_id` and `goal_id` are `ON DELETE SET NULL`, so a row can outlive both. A
 * row whose skill has gone reads "Skill no longer tracked" rather than losing its
 * identity, and a row whose goal has gone says the same about the goal.
 */
export interface LearningActivityRowProps {
  activity: LearningActivityRead
  /** Resolved skill name, when the caller already has it. */
  skillName?: string | null
  goalName?: string | null
  className?: string
}

export function LearningActivityRow({
  activity,
  skillName = null,
  goalName = null,
  className,
}: LearningActivityRowProps) {
  const meta = ACTIVITY_TYPE_META[activity.activity_type]

  return (
    <li className={cn('min-w-0 space-y-2 py-3 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 text-sm font-medium leading-snug text-foreground">
          {activity.title}
        </p>
        <p className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <time
            dateTime={activity.occurred_at}
            title={formatLearningInstant(activity.occurred_at)}
          >
            {formatLearningInstant(activity.occurred_at)} {formatLearningClock(activity.occurred_at)}
          </time>
        </p>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <ActivityTypeBadge activityType={activity.activity_type} size="sm" />
        <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
          <CircleDot aria-hidden="true" className="size-3 shrink-0" />
          <span className="truncate">{describeDuration(activity.duration_minutes)}</span>
        </span>
      </div>

      {activity.description && (
        <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">
          {activity.description}
        </p>
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="flex min-w-0 items-center gap-1.5">
          <Link2 aria-hidden="true" className="size-3 shrink-0" />
          <span className="truncate">{describeActivitySource(activity.source_type)}</span>
        </span>
        <span className="flex min-w-0 items-center gap-1.5">
          <CircleDot aria-hidden="true" className="size-3 shrink-0" />
          <span className="truncate">
            {activity.skill_id
              ? (skillName ?? 'Skill no longer tracked')
              : 'No skill named'}
          </span>
        </span>
        {activity.goal_id && (
          <span className="flex min-w-0 items-center gap-1.5">
            <Target aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">{goalName ?? 'Goal no longer tracked'}</span>
          </span>
        )}
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">{meta.description}</p>
    </li>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The trail's loading silhouette.
 *
 * A title line, a date line, a badge and two metadata lines per row, and **no
 * digits anywhere** — a grey "0m" beside a duration icon would be a duration
 * nobody recorded. Announced once for the whole list rather than per row.
 */
export function LearningActivityTimelineSkeleton({
  count = 6,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading the learning activity timeline</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <div className="h-3.5 w-3/5 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-24 shrink-0 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex items-center gap-3">
            <div className="h-4 w-24 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-40 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex gap-3">
            <div className="h-3 w-28 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-20 animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      ))}
    </div>
  )
}

export interface LearningActivityTimelineProps {
  activities: readonly LearningActivityRead[]
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  emptyAction?: ReactNode
  /** Resolves a skill id to a display name. Omit when no skill is named. */
  skillName?: (skillId: UUIDString) => string | null
  /** Resolves a goal id to a display name. Omit when no goal is linked. */
  goalName?: (goalId: UUIDString) => string | null
  /** The backend's total, so a page of rows cannot be read as the whole trail. */
  total?: number | null
  title?: string
  /** The sentence under the title; states what the trail covers. */
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The trail card: a title, a sentence about scope, and the rows.
 *
 * The scope sentence is the caller's, because only the caller knows the window:
 * a global trail and one goal's trail say different things above the same rows,
 * and a shared card that guessed would be wrong one of the two times.
 */
export function LearningActivityTimeline({
  activities,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  skillName,
  goalName,
  total = null,
  title = 'Learning activity',
  subtitle,
  skeletonCount = 6,
  titleLevel = 'h3',
  className,
}: LearningActivityTimelineProps) {
  const nameForSkill = skillName ?? (() => null)
  const nameForGoal = goalName ?? (() => null)

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <LearningStaleNotice isStale={isStale} subject="the activity timeline" />

        {isLoading ? (
          <LearningActivityTimelineSkeleton count={skeletonCount} />
        ) : error ? (
          <LearningRegionError
            error={error}
            onRetry={onRetry}
            subject="the activity timeline"
            compact
          />
        ) : activities.length === 0 ? (
          <LearningEmptyState
            variant="activities"
            reason={emptyReason}
            action={emptyAction}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(activities.length)}{' '}
              {activities.length === 1 ? 'activity' : 'activities'} shown, newest first.
            </p>
            <ul className="divide-y divide-border">
              {activities.map((activity) => (
                <LearningActivityRow
                  key={activity.id}
                  activity={activity}
                  skillName={activity.skill_id ? nameForSkill(activity.skill_id) : null}
                  goalName={activity.goal_id ? nameForGoal(activity.goal_id) : null}
                />
              ))}
            </ul>
            <LearningTimelineScopeNote total={total} shown={activities.length} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Shown in a trail with no pagination control, so a reader knows it is partial.
 *
 * `total` is the backend's figure and `shown` is the length of the page that
 * arrived; the two disagree on every paginated read, and a trail that said
 * nothing about it would let a reader mistake page one for the whole record.
 */
export function LearningTimelineScopeNote({
  total,
  shown,
}: {
  total: number | null
  shown: number
}) {
  if (total === null || total <= shown) {
    return (
      <p className="text-xs text-muted-foreground">
        Every activity that has been recorded is shown here.
      </p>
    )
  }

  return (
    <p className="text-xs text-muted-foreground">
      Showing {formatNumber(shown)} of {formatNumber(total)} recorded activities. This is a
      position in the list, not the whole trail.
    </p>
  )
}