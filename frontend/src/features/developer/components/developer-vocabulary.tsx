/**
 * The developer surface's vocabulary: what each scan status, activity level,
 * granularity and metric unit is called, which icon carries it, which tone it
 * wears and one sentence explaining it.
 *
 * **This is a `.tsx` file with no JSX in it, and that is deliberate.** The
 * project runs `react-refresh/only-export-components`, which reports any file
 * exporting a component *and* a non-component; `allowConstantExport` is on but
 * the plugin's implementation only exempts literals, so an object of metadata
 * cannot sit beside the badge components that consume it. The rule's own error
 * message gives the remedy: "use a new file to share constants".
 *
 * ## Nothing here measures a person
 *
 * Every label in this file describes **what a repository recorded**, and the
 * wording was chosen to keep it that way. There is no "productivity", no
 * "output", no "busy" and no "effort" anywhere, because a commit timestamp
 * records *that* something was committed and *when* — it records nothing at all
 * about how long anyone was at it. `ACTIVITY_LEVELS` is the sharpest case: it
 * would have been easy to name it after effort, and instead every entry names a
 * **count of commits** and says so in the sentence that renders beside it. The
 * levels deliberately carry no `success` tone, since a tone ladder from "few" to
 * "many" would imply that more commits is better work, which is a judgement this
 * surface is not allowed to make.
 *
 * The bands in `SCAN_STATUS_META` describe **what the scan attempt did**, not
 * the repository's quality. `ok` means git read the directory; it does not mean
 * the code is good.
 */

import {
  BookOpenCheck,
  CalendarRange,
  CircleCheck,
  CircleDot,
  GitBranch,
  GitCommitVertical,
  GitCompareArrows,
  Layers,
  Minus,
  Package,
  Scale,
  TrendingUp,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'

import type {
  ActivityGranularity,
  DeveloperMetricUnit,
  GitScanStatus,
} from '@/types/developer'
import type { StatusMeta } from '@/types/work'

/* --------------------------------------------------------------- scan status */

/**
 * What one attempt to read a repository from disk ended as.
 *
 * **`error` is a state, not a failure of the page.** A broken repository must
 * never break NEXUS, so the failure arrives as a 200 carrying
 * `status: 'error'` and a human sentence; this chip is where that sentence is
 * announced, with the word beside the icon rather than the colour alone.
 */
export const SCAN_STATUS_META: Record<GitScanStatus, StatusMeta> = {
  ok: {
    label: 'Scanned',
    icon: CircleCheck,
    tone: 'success',
    description: 'Git read the repository on the last scan attempt, and the run is recorded.',
  },
  error: {
    label: 'Scan failed',
    icon: TriangleAlert,
    tone: 'danger',
    description:
      'The last attempt could not read the repository. The reason is recorded with it, ' +
      'in words rather than a traceback, and the repository itself is untouched.',
  },
}

/** Shown where `last_scan_status` is null — a repository registered but never scanned. */
export const NEVER_SCANNED_META: StatusMeta = {
  label: 'Never scanned',
  icon: CircleDot,
  tone: 'neutral',
  description:
    'Registration records the path; it does not read it. The first scan is what brings ' +
    'commits and branches in.',
}

/* ------------------------------------------------------------- activity level */

/**
 * How much was recorded in a window, as a named count.
 *
 * The thresholds are fixed here rather than computed from the account's own
 * history, so the same figure reads the same way on every screen and a reader
 * comparing two repositories is comparing two counts rather than two percentiles
 * of themselves. There is deliberately **no** `success` tone on any level: the
 * ladder says "more commits were recorded", and colouring it as if it were a
 * grade would make the surface say something the data does not contain.
 */
export interface ActivityLevel {
  key: 'none' | 'few' | 'some' | 'many'
  /** The short noun phrase rendered on the card. */
  label: string
  /** Filled pips out of `ACTIVITY_LEVEL_PIPS`, for the decorative meter. */
  pips: number
  tone: 'neutral' | 'info'
  /** One sentence saying what the level counts. */
  description: string
}

/** The meter's width. Decorative — the sentence beside it is the accessible version. */
export const ACTIVITY_LEVEL_PIPS = 4

/**
 * Buckets a commit count into a named level.
 *
 * A **count**, never a rate and never a duration: the input is commits recorded
 * inside a window, and nothing here looks at the gap between two commits. Doing
 * that is how a timestamp turns into a claim about hours, which this surface is
 * built to avoid.
 */
export function activityLevelFor(commits: number): ActivityLevel {
  const rounded = Number.isFinite(commits) ? Math.max(0, Math.trunc(commits)) : 0

  if (rounded === 0) {
    return {
      key: 'none',
      label: 'No commits recorded',
      pips: 0,
      tone: 'neutral',
      description:
        'No commit landed in this window. That is a count of recorded events, not a ' +
        'statement about how time was spent.',
    }
  }
  if (rounded < 5) {
    return {
      key: 'few',
      label: 'A few commits recorded',
      pips: 1,
      tone: 'info',
      description: 'Between 1 and 4 commits were recorded in this window.',
    }
  }
  if (rounded < 20) {
    return {
      key: 'some',
      label: 'Some commits recorded',
      pips: 2,
      tone: 'info',
      description: 'Between 5 and 19 commits were recorded in this window.',
    }
  }
  return {
    key: 'many',
    label: 'Many commits recorded',
    pips: 3,
    tone: 'info',
    description: '20 or more commits were recorded in this window.',
  }
}

/* ---------------------------------------------------------------- granularity */

/**
 * Day, week or month.
 *
 * The ordering is increasing coarseness, which is the order the control offers
 * them in, and `day` leads because a git timestamp is exact to the second and a
 * daily bucket is the finest grain the data honestly supports.
 */
export const GRANULARITY_META: Record<
  ActivityGranularity,
  { label: string; shortLabel: string; icon: LucideIcon; description: string }
> = {
  day: {
    label: 'Daily',
    shortLabel: 'Day',
    icon: GitCommitVertical,
    description: 'One point per UTC day.',
  },
  week: {
    label: 'Weekly',
    shortLabel: 'Week',
    icon: CalendarRange,
    description: 'One point per week, anchored on the start of the week in UTC.',
  },
  month: {
    label: 'Monthly',
    shortLabel: 'Month',
    icon: TrendingUp,
    description: 'One point per calendar month in UTC.',
  },
}

/* ---------------------------------------------------------------- metric unit */

/**
 * What a metric's number counts.
 *
 * Carried as data so a screen cannot format a ratio as a count, or a line count
 * as a count of commits. Two members exist for the union's completeness rather
 * than for anything Phase 8 emits: `score` is declared by the wire types but
 * **no Phase 8 metric produces one**, because there is no score of a person on
 * this surface, and the copy says so rather than leaving a reader wondering what
 * a developer score would mean.
 */
export const METRIC_UNIT_META: Record<
  DeveloperMetricUnit,
  { label: string; icon: LucideIcon; description: string; digits: number }
> = {
  count: {
    label: 'Count',
    icon: Package,
    description: 'A count of things the repository recorded.',
    digits: 0,
  },
  lines: {
    label: 'Lines',
    icon: GitCompareArrows,
    description: 'A count of lines added or removed by commits. Not an effort measure.',
    digits: 0,
  },
  days: {
    label: 'Days',
    icon: CalendarRange,
    description:
      'A count of distinct calendar days carrying at least one commit. It counts days ' +
      'that had a commit, never the hours spent.',
    digits: 0,
  },
  ratio: {
    label: 'Ratio',
    icon: Scale,
    description: 'A number between 0 and 1, shown as the ratio rather than as a percentage.',
    digits: 2,
  },
  score: {
    label: 'Score',
    icon: Minus,
    description:
      'No Phase 8 metric produces a score. Nothing on this surface rates a person.',
    digits: 0,
  },
}

/* --------------------------------------------------------------------- icons */

/** Leading icon for the metric sections, chosen by what the metric counts. */
export const METRIC_ICONS = {
  commit_activity: GitCommitVertical,
  repository_activity: GitBranch,
  change_volume: GitCompareArrows,
  active_days: CalendarRange,
  consistency: Layers,
  repository_growth: GitBranch,
  maintenance_activity: BookOpenCheck,
  recent_momentum: TrendingUp,
} as const satisfies Record<string, LucideIcon>

/* --------------------------------------------------------------------- words */

/**
 * The title every insufficient-data state on this surface reads.
 *
 * One sentence, one meaning: the measurement could not be made. It is **not** a
 * zero, and a card carrying it must not also print a figure — the backend's own
 * `reason_if_unavailable` goes underneath it, verbatim, because it names the
 * specific ingredient that was missing and a generic sentence cannot.
 */
export const NOT_ENOUGH_DATA_TITLE = 'Not enough data yet.'

/** Shown for a repository that has never been read, next to the never-scanned chip. */
export const NEVER_SCANNED_TITLE = 'Never scanned'