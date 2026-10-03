/**
 * The developer surface's component library.
 *
 * Presentational only: every component here takes data as props and none of them
 * fetches. Query state, windowing and granularity live in `features/developer/hooks.ts`,
 * which another layer owns, and the pages decide what to pass — so a card can be
 * rendered in a test with a literal `RepositoryRead` and no provider around it.
 *
 * Charts are re-exported rather than reimplemented. `ChartShell`, `ChartTooltip`,
 * `TrendChart`, `AnalyticsBarChart`, `Heatmap`, `MetricCard`, `ScoreCard`,
 * `EmptyAnalytics` and `LazyChart` all come from `features/analytics`, and the
 * activity and language panels here are thin wrappers that decide *which* of them
 * to draw and what the surrounding sentences say.
 */

export {
  BranchBadge,
  LanguageBadge,
  ScanStatusBadge,
} from './developer-badges'

export {
  DeveloperEmptyState,
  DeveloperStaleNotice,
} from './developer-empty-state'
export type {
  DeveloperEmptyStateProps,
  DeveloperEmptyVariant,
  DeveloperStaleNoticeProps,
} from './developer-empty-state'

export {
  ACTIVITY_LEVEL_PIPS,
  GRANULARITY_META,
  METRIC_ICONS,
  METRIC_UNIT_META,
  NEVER_SCANNED_META,
  NEVER_SCANNED_TITLE,
  NOT_ENOUGH_DATA_TITLE,
  SCAN_STATUS_META,
  activityLevelFor,
} from './developer-vocabulary'
export type { ActivityLevel } from './developer-vocabulary'

export {
  DEFAULT_STALE_HOURS,
  commitFilesPhrase,
  commitLineDelta,
  commitShortHash,
  describeCommitCount,
  describeCurrentBranch,
  formatActivityBucketLabel,
  formatDeveloperClock,
  formatDeveloperInstant,
  formatDeveloperMetricValue,
  formatDeveloperMetricValueWithUnit,
  formatScanAge,
  isScanStale,
  languageCountsFromRepositories,
  languageUnitNoun,
  repositoryDirectoryName,
} from './developer-format'
export type { LanguageCount } from './developer-format'

export {
  ActivityGranularityControl,
  DeveloperActivitySection,
} from './activity-chart-section'
export type {
  ActivityGranularityControlProps,
  DeveloperActivitySectionProps,
} from './activity-chart-section'

export {
  BranchList,
  BranchListSkeleton,
  BranchRow,
} from './branch-list'
export type { BranchListProps, BranchRowProps } from './branch-list'

export {
  CommitTimeline,
  CommitTimelineItem,
  CommitTimelineSkeleton,
  TimelineScopeNote,
} from './commit-timeline'
export type { CommitTimelineItemProps, CommitTimelineProps } from './commit-timeline'

export {
  DeveloperMetricCard,
  DeveloperMetricCardSkeleton,
  DeveloperMetricList,
  DeveloperMetricListSkeleton,
} from './developer-metric-card'
export type {
  DeveloperMetricCardProps,
  DeveloperMetricListProps,
  DeveloperMetricListSkeletonProps,
} from './developer-metric-card'

export {
  DeveloperSummaryTiles,
  DeveloperSummaryTilesSkeleton,
} from './developer-summary-tiles'
export type {
  DeveloperSummaryTilesProps,
  SummaryTile,
} from './developer-summary-tiles'

export { LanguageBreakdown } from './language-breakdown'
export type { LanguageBreakdownProps } from './language-breakdown'

export {
  NeverScannedHint,
  ScanRunList,
  ScanRunListSkeleton,
  ScanStatusPanel,
} from './scan-status-panel'
export type { ScanRunListProps, ScanStatusPanelProps } from './scan-status-panel'

export {
  RepositoryCard,
  RepositoryCardGrid,
  RepositoryCardGridSkeleton,
  RepositoryCardSkeleton,
} from './repository-card'
export type {
  RepositoryCardGridProps,
  RepositoryCardGridSkeletonProps,
  RepositoryCardProps,
} from './repository-card'