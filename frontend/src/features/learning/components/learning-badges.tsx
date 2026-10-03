import { CircleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import {
  ACTIVITY_TYPE_META,
  GOAL_PRIORITY_META,
  GOAL_STATUS_META,
  LEVEL_SOURCE_META,
} from '@/features/learning/components/learning-vocabulary'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import type {
  LearningActivityType,
  LearningGoalStatus,
  SkillLevelSource,
} from '@/types/learning'
import type { ProjectPriority, StatusMeta } from '@/types/work'

/**
 * The small vocabulary chips the learning surface renders.
 *
 * **Every chip carries an icon *and* the word**, which is the same contract
 * `features/risk` and `features/developer` discharge and for the same reason: a
 * small coloured pill is invisible to a screen-reader user, to a reader with a
 * colour vision deficiency, to a dark-mode reader where the tint shifts, and to
 * a printed page. The icons differ in *shape*, not only in hue, and the word is
 * the authority.
 *
 * `TONE_BADGE_VARIANT` is reused rather than re-derived, so a tone never names a
 * `Badge` variant in two places and the mapping cannot drift.
 *
 * The icons are `aria-hidden`: the word beside them already names the state, so
 * announcing "green check, Completed" would duplicate the announcement rather
 * than add to it.
 *
 * **The level-source chip is the one that must never be dropped.** It is not
 * decoration attached to a level — it is what makes the number sayable at all.
 * A level with no chip beside it is a bare judgement, which is the single thing
 * this surface is built to refuse.
 */

export interface LearningBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

/**
 * Renders one `StatusMeta` as an icon plus its label.
 *
 * **A value outside the vocabulary still renders.** A payload this client has
 * not been taught about — a status added by a newer backend — must read as the
 * word the server sent rather than as a chip with no text at all, which a
 * screen reader would announce as nothing. This mirrors the developer surface's
 * badge helper, written for the same reason.
 */
function VocabularyBadge({
  meta,
  raw,
  size = 'default',
  className,
}: LearningBadgeProps & { meta: StatusMeta | undefined; raw: string }) {
  const resolved = meta ?? { label: raw, icon: CircleAlert, tone: 'neutral' as const }
  const Icon = resolved.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[resolved.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta?.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {resolved.label}
    </Badge>
  )
}

/**
 * Where a learning goal sits in its own life.
 *
 * `archived` gets its own chip rather than being folded into "Completed" — an
 * archived goal is not outstanding work, and a badge that merged the two would
 * let a header say "3 goals remaining" on an account that filed them away.
 */
export function GoalStatusBadge({
  status,
  size,
  className,
}: LearningBadgeProps & { status: LearningGoalStatus }) {
  return (
    <VocabularyBadge
      meta={GOAL_STATUS_META[status]}
      raw={String(status)}
      size={size}
      className={className}
    />
  )
}

/** The ordering the user gave a goal. Says nothing about the goal's worth. */
export function GoalPriorityBadge({
  priority,
  size,
  className,
}: LearningBadgeProps & { priority: ProjectPriority }) {
  return (
    <VocabularyBadge
      meta={GOAL_PRIORITY_META[priority]}
      raw={String(priority)}
      size={size}
      className={className}
    />
  )
}

/**
 * What kind of event a recorded activity is.
 *
 * The chip's `title` is the type's own sentence, which is what stops a reader
 * taking `Resource viewed` for evidence of understanding: the type carries the
 * qualifier "a page was opened" right where they hover it.
 */
export function ActivityTypeBadge({
  activityType,
  size,
  className,
}: LearningBadgeProps & { activityType: LearningActivityType }) {
  const meta = ACTIVITY_TYPE_META[activityType]
  const Icon = meta.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant="secondary"
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {meta.label}
    </Badge>
  )
}

/**
 * Who said a skill level.
 *
 * Rendered beside **every** level on this surface: the skill card, the gap row
 * and the development-areas panel all put it there, and none of them has a prop
 * to switch it off. That is deliberate — the chip is the attribution, and an
 * optional attribution is one that eventually gets left out.
 */
export function LevelSourceBadge({
  source,
  size,
  className,
}: LearningBadgeProps & { source: SkillLevelSource }) {
  const meta = LEVEL_SOURCE_META[source]
  const Icon = meta.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {meta.label}
    </Badge>
  )
}