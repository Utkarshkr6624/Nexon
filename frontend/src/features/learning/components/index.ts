/**
 * The learning surface's component library.
 *
 * Presentational only: every component here takes data as props and none of them
 * fetches. Query state, windowing and the derived joins live in
 * `features/learning/hooks.ts`, which another layer owns, and the pages decide
 * what to pass — so a card can be rendered in a test with a literal
 * `LearningGoalRead` and no provider around it.
 *
 * Charts are re-exported rather than reimplemented: `ChartShell`, `ChartTooltip`,
 * `TrendChart`, `AnalyticsBarChart`, `Heatmap`, `MetricCard`, `ScoreCard`,
 * `EmptyAnalytics` and `LazyChart` all come from `features/analytics`, and the
 * summary here is a thin wrapper that decides *which* of them to draw and what
 * the surrounding sentences say.
 *
 * Three rules run through every component in this barrel, and they are why the
 * vocabulary, the formatters and the empty states live in files of their own
 * rather than inside the cards:
 *
 * 1. **A level is never a bare number.** Every level on this surface is rendered
 *    through `describeLevelClaim`, which takes the `level_source` and names who
 *    set it, beside a `LevelSourceBadge` that cannot be switched off.
 * 2. **A figure that could not be computed is never a zero.** `available: false`
 *    plus a verbatim reason replaces the figure; `formatNumber` returns a dash
 *    for a null; no call site uses `?? 0`.
 * 3. **Every region has four states** — loading, empty, error and stale — and
 *    each says what fills it.
 */

export {
  ActivityTypeBadge,
  GoalPriorityBadge,
  GoalStatusBadge,
  LevelSourceBadge,
} from './learning-badges'
export type { LearningBadgeProps } from './learning-badges'

export {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from './learning-empty-state'
export type {
  LearningEmptyStateProps,
  LearningEmptyVariant,
  LearningRegionErrorProps,
  LearningStaleNoticeProps,
} from './learning-empty-state'

export {
  ACTIVITY_TYPE_META,
  ACTIVITY_TYPE_ORDER,
  GOAL_PRIORITY_META,
  GOAL_STATUS_META,
  LEARNING_LEVEL_SCALE,
  LEVEL_SOURCE_META,
  NOT_ENOUGH_DATA_TITLE,
  STREAK_BAND_DAYS,
  levelSourcePhrase,
} from './learning-vocabulary'
export type {
  ActivityTypeMeta,
  LevelSourceMeta,
} from './learning-vocabulary'

export {
  describeActivitySource,
  describeDaysSince,
  describeDuration,
  describeEvidenceCount,
  describeGap,
  describeLastActivity,
  describeLevelClaim,
  describeProgress,
  describeStreak,
  describeTargetDate,
  describeWindow,
  formatBucketLabel,
  formatLevelOfScale,
  formatLearningClock,
  formatLearningDate,
  formatLearningInstant,
  streakPhrase,
} from './learning-format'
export type { LearningStreak } from './learning-format'

export {
  LearningActivitySummary,
  LearningSummarySkeleton,
} from './learning-activity-summary'
export type { LearningActivitySummaryProps } from './learning-activity-summary'

export {
  LearningActivityRow,
  LearningActivityTimeline,
  LearningActivityTimelineSkeleton,
  LearningTimelineScopeNote,
} from './learning-activity-timeline'
export type {
  LearningActivityRowProps,
  LearningActivityTimelineProps,
} from './learning-activity-timeline'

export {
  LearningGoalCard,
  LearningGoalCardGrid,
  LearningGoalCardGridSkeleton,
  LearningGoalCardSkeleton,
} from './learning-goal-card'
export type {
  LearningGoalCardGridProps,
  LearningGoalCardGridSkeletonProps,
  LearningGoalCardProps,
} from './learning-goal-card'

export {
  SkillCard,
  SkillCardGrid,
  SkillCardGridSkeleton,
  SkillCardSkeleton,
  SkillLevelComparison,
  SkillLevelMeter,
  SkillLevelUnavailableNote,
} from './skill-card'
export type {
  SkillCardGridProps,
  SkillCardGridSkeletonProps,
  SkillCardProps,
  SkillLevelMeterProps,
} from './skill-card'

export { SkillGapList, SkillGapListSkeleton, SkillGapRow } from './skill-gap-list'
export type { SkillGapListProps, SkillGapRowProps } from './skill-gap-list'