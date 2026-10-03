/**
 * The career surface's component library.
 *
 * Presentational only: every component here takes data as props and none of them
 * fetches. Query state and the derived joins live in `features/career/hooks.ts`,
 * which another layer owns, and the pages decide what to pass — so a card can be
 * rendered in a test with a literal `CareerProfileRead` and no provider around it.
 *
 * Charts and stat tiles are reused rather than reimplemented: `MetricCard`,
 * `ScoreCard`, `TrendChart`, `Heatmap`, `ChartShell`, `ChartTooltip`,
 * `AnalyticsBarChart`, `EmptyAnalytics` and `LazyChart` all come from
 * `features/analytics`, and nothing in this barrel draws one of its own.
 *
 * Four rules run through every component here, and they are why the vocabulary,
 * the formatters and the empty states live in files of their own:
 *
 * 1. **Nothing was written by NEXUS.** Every field on a profile, a record and a
 *    manually added piece of evidence is something the user typed, and the empty
 *    states say *what the user does* rather than promising that NEXUS will find
 *    or draft something.
 * 2. **A level is never a bare number.** `describeCareerLevel` takes the
 *    `level_source` and names who set it, beside a `LevelOriginBadge` that
 *    cannot be switched off.
 * 3. **A missing key is not a zero.** A sparse `by_type` record renders as "not
 *    in this response" rather than as `0`, and no figure is substituted with
 *    `?? 0` anywhere.
 * 4. **Every region has four states** — loading, empty, error and stale — and
 *    each says what fills it.
 */

export {
  CareerEvidenceTypeBadge,
  CareerRecordKindBadge,
  EvidenceSourceBadge,
  LevelOriginBadge,
} from './career-badges'
export type { CareerBadgeProps } from './career-badges'

export {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from './career-empty-state'
export type {
  CareerEmptyStateProps,
  CareerEmptyVariant,
  CareerRegionErrorProps,
  CareerStaleNoticeProps,
} from './career-empty-state'

export {
  CAREER_EVIDENCE_TYPE_META,
  CAREER_EVIDENCE_TYPE_ORDER,
  CAREER_LEVEL_SCALE,
  CAREER_LEVEL_SOURCE_META,
  CAREER_RECORD_KIND_META,
  MANUAL_EVIDENCE_SOURCE,
  NOT_ENOUGH_DATA_TITLE,
  evidenceSourceMeta,
} from './career-vocabulary'
export type {
  CareerEvidenceTypeMeta,
  CareerRecordKindMeta,
  EvidenceSourceMeta,
} from './career-vocabulary'

export {
  countFromRecord,
  DEFAULT_DEVELOPMENT_EVIDENCE_THRESHOLD,
  describeCareerDaysSince,
  describeCareerEvidence,
  describeCareerLastActivity,
  describeCareerLevel,
  describeDevelopmentSentence,
  describeEvidenceSource,
  describeLinkLabel,
  describeRecordPeriod,
  developmentAreasFromGaps,
  formatCareerDate,
  formatCareerInstant,
  formatCareerLevel,
  formatRecordMonth,
  isNavigableLink,
} from './career-format'

export {
  CareerProfileCard,
  CareerProfileCardSkeleton,
  CareerProfileRegion,
} from './career-profile-card'
export type {
  CareerProfileCardProps,
  CareerProfileRegionProps,
} from './career-profile-card'

export {
  PortfolioEvidenceRow,
  PortfolioEvidenceScopeNote,
  PortfolioEvidenceTimeline,
  PortfolioEvidenceTimelineSkeleton,
} from './portfolio-evidence-timeline'
export type {
  PortfolioEvidenceRowProps,
  PortfolioEvidenceTimelineProps,
} from './portfolio-evidence-timeline'

export {
  CareerRecordList,
  CareerRecordListSkeleton,
  CareerRecordRow,
} from './career-record-list'
export type {
  CareerRecordListProps,
  CareerRecordRowProps,
} from './career-record-list'

export {
  CareerSummaryTiles,
  CareerSummaryTilesSkeleton,
} from './career-summary-tiles'
export type { CareerSummaryTilesProps } from './career-summary-tiles'

export {
  DevelopmentAreaRow,
  DevelopmentAreasPanel,
  DevelopmentAreasSkeleton,
} from './development-areas-panel'
export type {
  DevelopmentAreaRowProps,
  DevelopmentAreasPanelProps,
} from './development-areas-panel'

export {
  CareerLevelMeter,
  SkillOverviewGrid,
  SkillOverviewGridSkeleton,
  SkillOverviewTile,
  SkillOverviewTileSkeleton,
} from './skill-overview-grid'
export type {
  CareerLevelMeterProps,
  SkillOverviewGridProps,
  SkillOverviewGridSkeletonProps,
  SkillOverviewTileProps,
} from './skill-overview-grid'