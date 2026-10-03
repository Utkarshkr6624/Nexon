/**
 * Wire types for the Phase 8 developer intelligence surface.
 *
 * Mirrors `backend/app/schemas/developer.py`, which in turn projects
 * `backend/app/models/developer.py`.
 *
 * ## What this surface is allowed to say
 *
 * Git records *evidence*, and the vocabulary here is chosen so a client cannot
 * accidentally upgrade evidence into a verdict. There is no `hours_worked`, no
 * `productivity`, no `focus_time` and no `effort` field anywhere below, and
 * there is no field whose unit is minutes-per-person, because a commit timestamp
 * cannot prove how long anyone worked. What a commit *does* record is that work
 * happened at an instant, and the metrics are built from counts of those
 * instants — commits, active days, lines changed, distinct repositories. A UI
 * that wants to say "18 commits were recorded this week" has every field it
 * needs here; a UI that wants to say "you were busy for six hours" cannot
 * write it, because the number does not exist.
 *
 * ## The two rules this file obeys, and why they are written down twice
 *
 * 1. **The backend emits `null`, never an absent key.** Every nullable field is
 *    `T | null` and *required* — never `field?: T`. The distinction is not
 *    pedantry: `first_commit_at: string | null` means "this repository has no
 *    commits yet", whereas `first_commit_at?: string` also covers "this response
 *    shape did not bother", and a client that cannot tell those apart will
 *    eventually render the second as the first.
 * 2. **A figure that could not be computed is `null`, never `0`.** So
 *    `repository_age_days: number | null` — `null` for a repository with no
 *    commits, because `0` would assert "committed today" — and every metric
 *    carries `available` plus `reason_if_unavailable`, because
 *    `recent_momentum` divides by the previous seven days and a zero
 *    denominator is an *absence of measurement*, not a measurement of zero.
 *
 * Both rules push in the same direction: a zero-filled chart is a good chart, a
 * zero-*filled absence* is a lie. Genuine zeros (a day with no commits, a
 * repository with no branches) are plain `number` on purpose — those are
 * measurements.
 *
 * ## What was deliberately not built
 *
 * No model, no prediction, no score of the person. `DeveloperFeatureVectorRead`
 * is an *extractor*: named numbers with a schema version so a later phase knows
 * what each column meant. It trains nothing and infers nothing, and nothing in
 * this file may be joined with `features` and rendered as a forecast.
 */

import type { ISODateTimeString, UUIDString } from './api'
import type { PaginationParams } from './pagination'

// Re-exported so a consumer of the developer vocabulary needs one import, not three.
export type { ISODateTimeString, PaginationParams, UUIDString }

/* -------------------------------------------------------------- vocabulary */

/**
 * How a repository scan ended.
 *
 * Two members, and `pending` is deliberately absent: a scan is a synchronous
 * `POST`, so a `git_scan_runs` row is only ever written once the attempt has
 * already finished. There is no state in which a scan is known to be running.
 *
 * `error` is the load-bearing half. A broken repository must not break NEXUS, so
 * the failure is *data* — a row with `status: 'error'` and a human sentence in
 * `last_scan_error` — rather than an exception the page has to survive. The
 * union is closed so a client can render the failure as a state instead of
 * guessing.
 */
export const GIT_SCAN_STATUSES = ['ok', 'error'] as const
export type GitScanStatus = (typeof GIT_SCAN_STATUSES)[number]

/**
 * How the activity series is bucketed.
 *
 * Mirrors `developer_activity_granularity_default` on the server. The ordering
 * is increasing coarseness, which is the order a picker should offer them in:
 * day is the default and the finest grain a git timestamp supports honestly.
 */
export const ACTIVITY_GRANULARITIES = ['day', 'week', 'month'] as const
export type ActivityGranularity = (typeof ACTIVITY_GRANULARITIES)[number]

/**
 * What kind of thing a metric's number is.
 *
 * Carried as data rather than baked into formatting so a screen cannot render a
 * ratio as a count, or a line count as a count of commits, by forgetting which
 * metric it is holding. Note what is *not* here: `minutes`. Nothing on this
 * surface measures time spent.
 */
export const DEVELOPER_METRIC_UNITS = ['count', 'lines', 'days', 'ratio', 'score'] as const
export type DeveloperMetricUnit = (typeof DEVELOPER_METRIC_UNITS)[number]

/**
 * The eight metrics `GET /developer/metrics` always returns.
 *
 * A closed vocabulary, in the contract's own order, and deliberately complete:
 * the endpoint promises all eight every time, marking the ones it could not
 * judge with `available: false` rather than omitting them. A metric that
 * vanishes from the list when the data is thin is a metric a client has to
 * guard for; a metric that is present and says "not enough data" is one a
 * client can render.
 *
 * Every one of them counts recorded facts. None of them claims working time,
 * focus or effort, and none is derived from the *duration* between two commits.
 */
export const DEVELOPER_METRIC_KEYS = [
  'commit_activity',
  'repository_activity',
  'change_volume',
  'active_days',
  'consistency',
  'repository_growth',
  'maintenance_activity',
  'recent_momentum',
] as const
export type DeveloperMetricKey = (typeof DEVELOPER_METRIC_KEYS)[number]

/**
 * The schema version stamped on a feature vector.
 *
 * A *closed* union on purpose, which is the opposite of the usual instinct for
 * a version field. The entire reason `DeveloperFeatureVectorRead` carries a
 * version is that a later trainer must be able to tell what each column meant;
 * widening this to `string` would make `developer_features.v2` typecheck
 * silently against v1 field meanings, which is exactly the drift the version
 * exists to prevent. When v2 ships this union grows by one member and the
 * compiler finds every consumer that has to think about it.
 */
export const DEVELOPER_FEATURE_SCHEMA_VERSIONS = ['developer_features.v1'] as const
export type DeveloperFeatureSchemaVersion = (typeof DEVELOPER_FEATURE_SCHEMA_VERSIONS)[number]

/**
 * The one sentence the backend uses when a figure could not be derived.
 *
 * Mirrors `NOT_ENOUGH_DATA` in `app/services/developer/metrics.py`. Exported so
 * a screen can recognise the case and render the analytics placeholder (`—`)
 * rather than treating it as a distinct message to invent per page. It is a
 * module-level constant on both sides precisely so the two cannot drift into
 * two different sentences for one condition.
 */
export const NOT_ENOUGH_DATA = 'Not enough data to assess this yet'

/** Mirrors `developer_default_window_days`. Sending nothing asks for this. */
export const DEVELOPER_DEFAULT_WINDOW_DAYS = 30

/** Mirrors `developer_max_window_days`. A wider window is a 422 server-side. */
export const MAX_DEVELOPER_WINDOW_DAYS = 366

/** Mirrors `developer_activity_granularity_default`; `day` is the honest default. */
export const DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY: ActivityGranularity = 'day'

/* --------------------------------------------------------------- list params */

/**
 * Query parameters for `GET /developer/repositories`.
 *
 * `is_active` is served from `ix_git_repositories_owner_active` and
 * `project_id` from `ix_git_repositories_project_id`, so both are filters the
 * backend can answer without a scan. Neither is defaulted here: omitting them
 * asks for every repository the account owns, which is what the list page
 * wants on first load.
 */
export interface RepositoryListParams extends PaginationParams {
  is_active?: boolean
  project_id?: UUIDString
}

/**
 * Query parameters for the commit timelines.
 *
 * Shared by `GET /developer/commits` and
 * `GET /developer/repositories/{repository_id}/commits`. The per-repository
 * route already implies `repository_id`, but the global timeline takes it as a
 * filter, and one params type keeps the two callers from drifting.
 */
export interface CommitListParams extends PaginationParams {
  repository_id?: UUIDString
  /** Narrows to commits the scan could attribute to one branch. Attributed
   *  best-effort, so a commit with no resolvable branch is simply absent from
   *  this filter rather than misfiled. */
  branch?: string
}

/** Query parameters for `GET /developer/activity`. */
export interface ActivityParams {
  /** Left unset, the backend applies `developer_default_window_days`. */
  window_days?: number
  granularity?: ActivityGranularity
  /** Narrow the series to one repository; omitted means all of them. */
  repository_id?: UUIDString
}

/**
 * The window-only parameter set, shared by the summary, metrics, features and
 * project reads — the four endpoints that reason over a range and nothing else.
 *
 * Optional because the backend owns the default. A client that invented its own
 * fallback would show a 30-day chart on a page the server answered for a
 * different range, and the disagreement would be invisible.
 */
export interface DeveloperWindowParams {
  window_days?: number
}

/* -------------------------------------------------------------------- shapes */

/**
 * One registered local repository, and the state of its last scan.
 *
 * Mirrors `git_repositories` field for field. The `last_scan_*` group is the
 * honest face of rule 3 in the brief: a repository that git could not read is
 * still a row on the page, carrying `last_scan_status: 'error'` and a sentence
 * in `last_scan_error`, rather than an exception that took the screen down.
 * `last_scan_error` is a human message and never a traceback — sanitized
 * server-side of the user's home directory — so it may be rendered as written.
 *
 * `current_branch` is null on a detached HEAD, which is a normal state and not
 * an error; `default_branch` is null for a repository with no commits, because
 * there is nothing for a default to point at yet.
 */
export interface RepositoryRead {
  id: UUIDString
  /** User-facing label. Defaults to the directory name when registering. */
  name: string
  /** The resolved absolute path, normalized server-side so a relative path
   *  cannot later resolve somewhere else. */
  local_path: string
  description: string | null
  /** The most common tracked extension's language, or null when nothing in the
   *  repository matches a known one. Never "Other". */
  primary_language: string | null
  /** The project this repository belongs to. Null is normal, and the trail
   *  outlives the project — the FK is `ON DELETE SET NULL`. */
  project_id: UUIDString | null
  is_active: boolean
  /** Null on a detached HEAD. */
  current_branch: string | null
  /** Null for a repository with no commits yet. */
  default_branch: string | null
  branch_count: number
  commit_count: number
  /** Null for an empty repository — the first of the two null-not-zero cases. */
  first_commit_at: ISODateTimeString | null
  latest_commit_at: ISODateTimeString | null
  /** True when `git status --porcelain` reported at least one entry. A
   *  description of the working tree at scan time, not a judgement about it. */
  working_tree_dirty: boolean
  /** Null until the first scan. */
  last_scanned_at: ISODateTimeString | null
  last_scan_status: GitScanStatus | null
  /** A human sentence describing why the last scan failed, or null after one
   *  that succeeded. Safe to render verbatim; never a traceback. */
  last_scan_error: string | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/**
 * One page of repositories.
 *
 * Flat `items`/`total`/`limit`/`offset` rather than the shared `Paginated<T>`
 * envelope, matching the Phase 7 list shapes: this surface's totals are read
 * next to the rows on screen, and burying them under `meta` invites a header to
 * be written against a page slice and quoted as the whole.
 */
export interface RepositoryListRead {
  items: RepositoryRead[]
  /** Repositories matching the filters, not the length of this page. */
  total: number
  limit: number
  offset: number
}

/**
 * One observed commit.
 *
 * Mirrors `git_commits`. Every commit here was read out of a repository by a
 * scan, which is why `author_name` and `author_email` may be null: git recorded
 * them, and where it did not NEXUS does not invent them.
 *
 * `branch` is explicitly best-effort and null when no branch could be resolved
 * for the commit. It is never guessed, and a null is not a claim that the work
 * happened nowhere in particular — it is a claim that the scan could not place
 * it, which is the truth.
 *
 * `additions`, `deletions` and `files_changed` are plain `number` and may be
 * genuinely `0`: a commit that only moved a file, or one git reported no
 * numstat for, is stored as `0` rather than dropped, because "recorded as zero"
 * and "never counted" are different facts.
 */
export interface CommitRead {
  id: UUIDString
  repository_id: UUIDString
  /** The full SHA. Display code should use {@link CommitRead.short_hash}. */
  commit_hash: string
  short_hash: string
  committed_at: ISODateTimeString
  /** The subject line only, never the full body. */
  message: string
  author_name: string | null
  author_email: string | null
  additions: number
  deletions: number
  files_changed: number
  branch: string | null
  created_at: ISODateTimeString
}

/** One page of commits — the global timeline or one repository's history. */
export interface CommitListRead {
  items: CommitRead[]
  total: number
  limit: number
  offset: number
}

/**
 * One branch as the last scan observed it.
 *
 * `is_current` marks the branch HEAD points at and `is_default` the one git
 * resolved as the repository's default. On a detached HEAD exactly one of them
 * is false-or-absent by definition, which is why `current_branch` on the parent
 * repository can be null while this list is perfectly well-formed.
 */
export interface BranchRead {
  id: UUIDString
  repository_id: UUIDString
  name: string
  is_current: boolean
  is_default: boolean
  head_commit_hash: string | null
  /** The committer date of the head commit, or null for a branch whose head
   *  git could not date. */
  last_committed_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/** One page of branches. Usually the whole set; `limit` bounds a large repo. */
export interface BranchListRead {
  items: BranchRead[]
  total: number
  limit: number
  offset: number
}

/**
 * One attempt to read a repository from disk, recorded whatever the outcome.
 *
 * Mirrors `git_scan_runs`, which is append-only and has no `updated_at`: a scan
 * run is a fact about a moment, so there is no second version of it to amend.
 *
 * A failed scan is a row here with `status: 'error'` and a sentence in `error`.
 * That is the whole mechanism behind "a broken repository must never break
 * NEXUS" — there is no code path where a bad repository produces an exception
 * instead of one of these.
 *
 * `commits_discovered` against `commits_added` is the interesting pair: they
 * differ on every re-scan of an unchanged repository, and the gap is the
 * idempotent upsert doing its job rather than anything having gone wrong.
 */
export interface ScanRunRead {
  id: UUIDString
  repository_id: UUIDString
  /** Never null: a row exists only once the attempt finished. */
  status: GitScanStatus
  commits_discovered: number
  /** New rows written. Lower than `commits_discovered` on a re-scan, by design. */
  commits_added: number
  branches_discovered: number
  duration_ms: number
  /** A human sentence, or null on success. Never a traceback. */
  error: string | null
  scanned_at: ISODateTimeString
  created_at: ISODateTimeString
}

/**
 * The account-wide headline figures, for the developer dashboard.
 *
 * Counts only. There is no score here and no comparison to a previous week as a
 * verdict — `commits_in_window` is "how many commits were recorded", and the
 * range it covers is carried alongside so the sentence beside it can be true.
 *
 * `summary` is one factual sentence composed server-side, for the same reason
 * `RiskListRead.summary` is: header wording should have one owner rather than
 * one per page. `has_data` is the cold-start flag — false means every count
 * below is legitimately zero *and* the page must explain that no repository has
 * been registered or scanned yet, rather than showing a dashboard of zeroes as
 * though it were a finding.
 */
export interface DeveloperSummaryRead {
  /** Repositories registered for this account. */
  repository_count: number
  /** Of those, the ones not marked inactive. */
  active_repository_count: number
  /** Commits recorded across every repository, whole history. */
  commit_count: number
  /** Commits recorded inside the window below. A real zero when nothing was. */
  commits_in_window: number
  /** Distinct UTC dates inside the window carrying at least one commit. */
  active_days: number
  /** Additions plus deletions inside the window. A count of lines, not effort. */
  change_volume: number
  /** How many distinct repositories saw a commit inside the window. */
  repositories_touched: number
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** Null for an account whose repositories have no commits yet. */
  latest_commit_at: ISODateTimeString | null
  /** Null until some repository has been scanned at least once. */
  last_scanned_at: ISODateTimeString | null
  /** False when there is nothing to summarise, so zeros read as absence. */
  has_data: boolean
  /** One factual sentence describing the counts. */
  summary: string
}

/**
 * One bucket of the activity series.
 *
 * Buckets are **zero-filled**: a quiet Tuesday is present with `commits: 0`, not
 * skipped. A chart that omits an empty bucket silently compresses the timeline
 * and makes a sparse fortnight look as dense as a busy one, which is a
 * misreading of the data rather than a presentational choice.
 *
 * Timestamps rather than date-only strings, because week and month buckets are
 * anchored on an instant (start-of-week in UTC) and rendering them as bare
 * `YYYY-MM-DD` would invite a client to re-anchor them in local time.
 */
export interface DeveloperActivityBucketRead {
  bucket_start: ISODateTimeString
  bucket_end: ISODateTimeString
  commits: number
  additions: number
  deletions: number
  files_changed: number
  /** Distinct repositories with at least one commit in this bucket. */
  repository_count: number
}

/**
 * The activity series, with the window that produced it.
 *
 * `repository_id` echoes what the series was narrowed to, or null for the whole
 * account — so a chart can say what it is showing without re-reading the query
 * that asked for it.
 */
export interface DeveloperActivityRead {
  granularity: ActivityGranularity
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** Null when the series covers every repository the account owns. */
  repository_id: UUIDString | null
  /** Dense and in ascending order, gaps included. Never empty for a non-empty
   *  range: a range with no commits is all zeroes. */
  buckets: DeveloperActivityBucketRead[]
  /** The total across `buckets`, carried so a chart's axis and its caption
   *  cannot quote different sums. */
  total_commits: number
}

/**
 * One metric, fully explained.
 *
 * The shape of `app.services.developer.metrics.DeveloperMetric`, carried through
 * unchanged. Four fields do real work:
 *
 * - `value` is `number | null`, and null means *not measured*. `consistency`
 *   and `recent_momentum` divide, and a zero denominator produces null rather
 *   than an invented zero.
 * - `available` with `reason_if_unavailable` is the positive form of the same
 *   fact: "we looked, and there was nothing to look at". A zero with
 *   `available: true` is a different answer — the arithmetic came out at zero,
 *   and it must never be rendered with the reason attached.
 * - `definition` says how it is computed in one sentence, and `explanation`
 *   says it again with the figures in it. The explanation always contains the
 *   numbers it was built from; one without a digit in it is a backend bug, and
 *   carrying both is what lets a screen show the method above the result.
 * - `unit` is data, not decoration, so a ratio cannot be formatted as a count.
 *
 * `window_days` is null for a whole-history metric, which is why it is nullable
 * rather than defaulted to the request's window.
 */
export interface DeveloperMetricRead {
  key: DeveloperMetricKey
  label: string
  /** Null when the metric could not be computed. Never 0 for that reason. */
  value: number | null
  unit: DeveloperMetricUnit
  /** One sentence naming the inputs and the arithmetic. */
  definition: string
  /** The window this instance measured, or null for whole-history metrics. */
  window_days: number | null
  /** Which recorded facts it read, e.g. `git_commits`. */
  source: string
  /** The sentence shown to the user, with the figures it is built from. */
  explanation: string
  available: boolean
  /** Why it is unavailable, usually {@link NOT_ENOUGH_DATA}; null when
   *  `available` is true. */
  reason_if_unavailable: string | null
}

/**
 * The feature names, and only the feature names.
 *
 * An extractor, not a model: named numbers with a schema version so a later
 * phase knows what each column meant. Nothing here is a prediction, a
 * probability, or a fitted parameter, and `commit_frequency` is a rate of
 * recorded commits per day — not a statement about a person.
 *
 * The two nullable figures are the contract's own example of rule 2:
 * `repository_age_days` and `inactivity_days` are null for a repository with no
 * commits, because `0` would assert "committed today".
 */
export interface DeveloperFeatureValues {
  commits_last_7d: number
  commits_last_30d: number
  active_days_7d: number
  active_days_30d: number
  files_changed_7d: number
  additions_7d: number
  deletions_7d: number
  /** Null when the repository has no commits. */
  repository_age_days: number | null
  /** Null when the repository has no commits. */
  inactivity_days: number | null
  /** Recorded commits per day across the window. */
  commit_frequency: number
  /** True when the repository is linked to a project. */
  project_association: boolean
}

/**
 * The same feature names, attributed to one repository.
 *
 * Deliberately carries a `repository_id` and no name: the feature vector is a
 * machine surface, and a name belongs in the repository list a client already
 * fetches. Joining on `repository_id` keeps the two from disagreeing about what
 * a repository is called.
 */
export interface RepositoryFeatureVectorRead extends DeveloperFeatureValues {
  repository_id: UUIDString
}

/**
 * `GET /developer/features`: one row per account and one per repository.
 *
 * `schema_version` is a closed union (see
 * {@link DeveloperFeatureSchemaVersion}) and is what makes this vector usable
 * later: a trainer that sees `developer_features.v1` knows the column meanings
 * without trusting that the client did not reorder them.
 */
export interface DeveloperFeatureVectorRead {
  schema_version: DeveloperFeatureSchemaVersion
  generated_at: ISODateTimeString
  window_days: number
  /** The account-level row: the features aggregated across every repository. */
  features: DeveloperFeatureValues
  /** One row per repository, empty on an account with none registered. */
  repositories: RepositoryFeatureVectorRead[]
}

/**
 * The developer view of one project.
 *
 * Reads the same facts as {@link DeveloperSummaryRead} but narrowed to the
 * repositories linked to this project, which is what a project detail page
 * needs. `repositories` is the linked set itself, so the page can list them
 * without a second request and cannot show a count that disagrees with the
 * rows beneath it.
 *
 * `summary` is the same kind of one-line factual sentence, written to describe
 * commits and lines — never effort.
 */
export interface ProjectDeveloperRead {
  project_id: UUIDString
  project_name: string
  /** The repositories linked to this project; the FK is `ON DELETE SET NULL`,
   *  so a repository outlives the project it was attached to. */
  repositories: RepositoryRead[]
  repository_count: number
  /** Commits recorded across this project's repositories, whole history. */
  commit_count: number
  commits_in_window: number
  active_days: number
  change_volume: number
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** Null while none of this project's repositories has a commit yet. */
  latest_commit_at: ISODateTimeString | null
  has_data: boolean
  summary: string
}

/* ------------------------------------------------------------------ payloads */

/**
 * Registers a local repository.
 *
 * `local_path` is the only required field, and the backend resolves it and
 * proves it is a git work tree *before* storing anything — so a bad path is a
 * 422 with a sentence, not a row that fails on every future scan. A bare
 * `git init` with no commits is valid and registers fine.
 *
 * `name` defaults to the directory name server-side. `description`, `project_id`
 * and `is_active` are optional conveniences; the scan fills in everything the
 * repository itself knows.
 */
export interface RepositoryCreatePayload {
  local_path: string
  name?: string
  description?: string | null
  project_id?: UUIDString | null
  is_active?: boolean
}

/**
 * Edits repository metadata.
 *
 * **There is no `local_path` here, and that omission is the design.** The path
 * is the identity of the row and the one field the backend validates against
 * the filesystem; letting a PATCH move a repository would mean re-validating a
 * second work tree under a uniqueness constraint, and would let a row's history
 * silently change which directory it describes. To point a repository at
 * another directory, register the new one and delete the old.
 *
 * `primary_language` is absent for the mirror-image reason: it is measured by
 * the scan, not typed by the user. What you may edit is what you typed.
 */
export interface RepositoryUpdatePayload {
  name?: string
  description?: string | null
  project_id?: UUIDString | null
  is_active?: boolean
}

/**
 * Query parameters for the rescan.
 *
 * A scan is synchronous and takes no body; `full` exists only to override the
 * incremental default. Omitted, the backend passes `--since` the repository's
 * `latest_commit_at`, so a re-scan transfers only commits it has not seen.
 */
export interface RepositoryScanParams {
  /** Re-read the whole history rather than only what is new. */
  full?: boolean
}
