import type { ReactNode } from 'react'
import {
  Activity,
  CircleDashed,
  Filter,
  GraduationCap,
  ListChecks,
  type LucideIcon,
  Target,
  TrendingUp,
} from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/learning/components/learning-vocabulary'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'

/**
 * The "nothing to show" states for the learning surface.
 *
 * **Every description says why the region is empty *and* what fills it.** A bare
 * "No goals" reads as a broken page; "goals appear when you write one down, and
 * NEXUS never creates one for you" reads as an answer. That is the rule
 * `EmptyState` documents and this module is where it is discharged — one variant
 * per region, each with its own sentence, because a missing goal list and a
 * missing activity trail are filled by completely different actions.
 *
 * **`EmptyAnalytics` is deliberately not reused, and the reason is the title.**
 * That component says "Not enough activity yet", which is the right claim for an
 * analytics panel where an empty chart means a measurement could not be made
 * *from work data*. This surface is built from records a person entered, so its
 * empty states are mostly about *nothing having been recorded yet* and the
 * contract-specified wording "Not enough data yet." is used instead. What is
 * reused is the layout: `EmptyState` is the same primitive underneath, so the
 * two surfaces cannot drift apart visually.
 *
 * Two variants are not about insufficiency at all and say so with their own
 * titles. `goals` is a cold-start state: nothing has been written down, and a
 * title reading "Not enough data yet" would imply the account was missing
 * something rather than having not started. `filtered` is the opposite of empty
 * — the records exist and the filter is what hides them — and conflating the two
 * is exactly how a filtered list comes to read as an empty account.
 *
 * The tone throughout is factual. Nothing here congratulates an empty state or
 * alarms at one: an account that has not recorded any learning yet is in
 * exactly the state it started in.
 */
export type LearningEmptyVariant =
  | 'goals'
  | 'skills'
  | 'gaps'
  | 'activities'
  | 'summary'
  | 'filtered'

interface LearningEmptyCopy {
  icon: LucideIcon
  title: string
  description: string
}

const COPY: Record<LearningEmptyVariant, LearningEmptyCopy> = {
  goals: {
    icon: Target,
    title: 'No learning goals yet',
    description:
      'A goal is something you write down yourself: a title, where you want the level to go, ' +
      'and optionally a target date. NEXUS never creates a goal on your behalf, and every ' +
      'progress figure on this page is the one you set.',
  },
  skills: {
    icon: GraduationCap,
    title: 'No skills tracked yet',
    description:
      'A skill is a name plus a level you claim for it. Add one and it becomes the unit ' +
      'everything else here is counted in — gaps, evidence and activity all attach to a skill.',
  },
  gaps: {
    icon: TrendingUp,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'A gap is the distance between a recorded level and the target you set for it, and it is ' +
      'computed on read from both. Track a skill with a target above its current level and the ' +
      'gap, its evidence count and the sentence explaining it appear here.',
  },
  activities: {
    icon: Activity,
    title: 'Nothing recorded yet',
    description:
      'Every entry here is an event that was recorded: a study session, a completed task, a ' +
      'note, a concept or a resource that was opened. Record one — or let NEXUS derive it from ' +
      'a task, note, project or repository — and the trail builds itself.',
  },
  summary: {
    icon: ListChecks,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'The counts above are read from recorded learning activities and goals. They stay at ' +
      'nothing until there is at least one of either, because a dashboard of zeroes is not a ' +
      'measurement.',
  },
  filtered: {
    icon: Filter,
    title: 'Nothing matches this filter',
    description:
      'These records exist; none of them match what is selected. Clearing the filter shows them ' +
      'again.',
  },
}

export interface LearningEmptyStateProps {
  variant: LearningEmptyVariant
  /** Replaces the built-in description — the backend's own reason, verbatim. */
  reason?: string | null
  action?: ReactNode
  compact?: boolean
  className?: string
}

/**
 * One "nothing here yet" state, with the reason it is nothing here.
 *
 * `reason` wins over the built-in copy whenever the backend supplied one, for
 * the same reason `EmptyAnalytics` renders it verbatim: it names the specific
 * thing that was missing, which is the part a generic sentence cannot carry.
 */
export function LearningEmptyState({
  variant,
  reason,
  action,
  compact = false,
  className,
}: LearningEmptyStateProps) {
  const copy = COPY[variant]
  const useReason = typeof reason === 'string' && reason.trim().length > 0

  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      description={useReason ? reason : copy.description}
      action={action}
      compact={compact}
      className={className}
    />
  )
}

export interface LearningRegionErrorProps {
  error: ApiError
  onRetry?: () => void
  /** What failed, in a sentence the page can complete: "the learning goals". */
  subject: string
  /** The tighter form, for a region inside a card that is already framed. */
  compact?: boolean
  className?: string
}

/**
 * The failed-read state for one region.
 *
 * `subject` is per-region on purpose: a failure to read the goal list is not a
 * failure to read the activity trail, and a reader told "the learning data
 * could not be loaded" learns nothing about what to retry. `ErrorState` already
 * renders the backend's user-safe message and the request id, and never a
 * stack trace — this only supplies the title.
 *
 * **It is a region, not the page.** One failed read must not empty the rest of
 * the dashboard, so the surrounding lists each decide for themselves whether a
 * failure means "nothing yet" or "nothing can be shown".
 */
export function LearningRegionError({
  error,
  onRetry,
  subject,
  compact = false,
  className,
}: LearningRegionErrorProps) {
  return (
    <ErrorState
      error={error}
      onRetry={onRetry}
      title={`${subject} could not be loaded`}
      compact={compact}
      className={className}
    />
  )
}

export interface LearningStaleNoticeProps {
  /** True while a background refetch is in flight behind figures already on screen. */
  isStale: boolean
  /** What is being refetched, e.g. "the goal list". */
  subject?: string
  className?: string
}

/**
 * The "these figures are from the previous read" line.
 *
 * **It renders nothing at all when `isStale` is false** — not a hidden element,
 * not an empty string — so a settled page has no residue from a state it has
 * already left. While a refetch is in flight the figures beneath it are real and
 * were real a moment ago; saying so is what stops a control from being read as
 * "these numbers are broken".
 *
 * `aria-live="polite"` so a screen reader hears the settling rather than being
 * interrupted mid-sentence by the change itself.
 */
export function LearningStaleNotice({
  isStale,
  subject = 'these figures',
  className,
}: LearningStaleNoticeProps) {
  if (!isStale) return null

  return (
    <p aria-live="polite" className={cn('flex items-center gap-1.5 text-xs text-muted-foreground', className)}>
      <CircleDashed aria-hidden="true" className="size-3 animate-pulse" />
      Refreshing {subject}. The values shown are from the last completed read.
    </p>
  )
}