"""Wire shapes for Phase 8 developer intelligence.

Every number here is a count of something git recorded: commits, UTC dates
carrying a commit, lines added, files touched. **There is no field on this
surface whose unit is a minute of a person's work, and adding one is the way this
phase's central rule would be broken.** A commit timestamp records when a commit
object was written; it cannot record how long anyone worked, so nothing here can
be rendered as "you were busy for six hours" because the number to render does
not exist.

The nullability rules, and why they are the two rules
------------------------------------------------------
**A figure that could not be computed is ``None``, never ``0``.** So
:attr:`RepositoryFeatureVectorRead.repository_age_days` and
:attr:`RepositoryFeatureVectorRead.inactivity_days` are null for a repository
with no commits: ``0`` would assert "committed today". Inside a feature matrix
this matters more than it does on a dashboard — a fabricated zero is
indistinguishable from an observed one once a later trainer has consumed it —
which is the same reasoning :mod:`app.services.analytics.service` applies to
``AnalyticsService.feature_snapshot``.

**A metric that could not be measured is ``available=False`` with a reason, not a
zero.** :class:`DeveloperMetricRead` carries both ``value`` (null when
unavailable) and the positive form of the same fact, ``available`` plus
``reason_if_unavailable``. ``recent_momentum`` divides commits in the last seven
days by commits in the seven before, and a zero denominator is an *absence of
measurement* — reporting ``0.0`` would be the "confident zero that reads as
reassurance about a question nothing was measured to answer" failure this
codebase is built to avoid.

**A genuine zero is a plain number.** ``additions = 0`` on a commit that only
moved a file, ``commits = 0`` in a day bucket, ``branch_count = 0`` on a
repository whose branches were all deleted: each is a measurement, and typing
them as optional would force every client to render a real fact as an unknown.

List shapes, and why they are flat
----------------------------------
:func:`RepositoryListRead`, :class:`CommitListRead` and :class:`BranchListRead`
carry ``items``/``total``/``limit``/``offset`` directly rather than inside a
``meta`` envelope, matching the Phase 7 list shapes. These totals are read next
to the rows on screen, and burying them under ``meta`` invites a header to be
written against a page slice and then quoted as though it described the whole
set. :class:`RepositoryListRead` additionally carries the active/inactive split,
because "12 repositories, 3 of them paused" is a question a list header asks and
cannot be answered from the rows it happens to hold.

Enum columns arrive as ``str``
------------------------------
:attr:`RepositoryRead.last_scan_status` and
:attr:`DeveloperActivityRead.granularity` are typed ``str`` even though the
backend holds a :class:`~app.models.enums.GitScanStatus` and an
:class:`~app.services.developer.metrics.ActivityGranularity`. The stored
vocabulary is a closed set and is documented on each field, but a response model
that enumerated it would make the wire a second place the vocabulary is
declared — and the one that gets out of step with the enum. The service returns
the enum's ``.value``; the client checks it against the same closed union it
already imports.

Nothing here is a model
-----------------------
:class:`DeveloperFeatureVectorRead` carries a ``schema_version`` and named
numbers, and that is all. Phase 8 extracts features; it does not train, load,
serve or register a model, and nothing in this file may be joined with
``features`` and rendered as a forecast.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BranchListRead",
    "BranchRead",
    "CommitListRead",
    "CommitRead",
    "DeveloperActivityBucketRead",
    "DeveloperActivityRead",
    "DeveloperFeatureValues",
    "DeveloperFeatureVectorRead",
    "DeveloperMetricRead",
    "DeveloperSummaryRead",
    "ProjectDeveloperRead",
    "RepositoryCreate",
    "RepositoryFeatureVectorRead",
    "RepositoryListRead",
    "RepositoryRead",
    "RepositoryUpdate",
    "ScanRunRead",
]

#: The version stamped on a feature vector. A single string rather than an enum
#: because this is the *contract* with whatever trains on it: a v2 must not be
#: able to typecheck against v1 column meanings, and the frontend carries the
#: closed union that makes that a compile error there.
FEATURE_SCHEMA_VERSION = "developer_features.v1"


class RepositoryRead(BaseModel):
    """One registered local repository, and what its last scan found.

    Mirrors ``git_repositories``. The ``last_scan_*`` group is the honest face of
    "a broken repository must never break NEXUS": a repository git could not read
    is still a row here, carrying ``last_scan_status: 'error'`` and a sentence in
    ``last_scan_error``, rather than an exception that took the page down. That
    sentence is sanitised server-side — the operator's home directory is not in
    it — so it may be rendered as written.

    :attr:`current_branch` is null on a detached HEAD, which is a normal state
    and not an error, and :attr:`default_branch` is null for a repository with no
    commits, because there is nothing yet for a default to point at.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the registered repository.")
    name: str = Field(
        description="The user's label for it, defaulting to the directory name when "
        "registering. Never machine-derived afterwards: renaming the directory on "
        "disk must not rename it here."
    )
    local_path: str = Field(
        description="The resolved absolute path of the work tree, normalized "
        "server-side when registering so a relative path cannot later resolve "
        "somewhere else. Immutable — a repository cannot be moved to another "
        "directory, because its recorded history would then describe a different "
        "one."
    )
    description: str | None = Field(
        default=None,
        description="A free-text note the user attached to it. Null is a real "
        "state, not an unrendered empty string.",
    )
    primary_language: str | None = Field(
        default=None,
        description="The most common tracked-file extension's language, from a "
        "static in-repo map. Null when nothing in the repository matches a known "
        "one — never 'Other', which would be a number nobody can act on.",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="The project this repository is linked to. Null is normal, and "
        "the recorded history outlives the project: the foreign key is "
        "`ON DELETE SET NULL`.",
    )
    is_active: bool = Field(
        default=True,
        description="False hides the repository from the dashboard without "
        "deleting its commits. It still occupies a registration slot.",
    )
    current_branch: str | None = Field(
        default=None,
        description="The branch HEAD pointed at when the last scan ran. Null on a "
        "detached HEAD, which git reports as the literal string `HEAD` — a name "
        "that names no branch.",
    )
    default_branch: str | None = Field(
        default=None,
        description="The repository's default branch as git resolved it — "
        "`refs/remotes/origin/HEAD` for a clone, `HEAD` otherwise. Null for a "
        "repository with no commits.",
    )
    branch_count: int = Field(
        default=0,
        ge=0,
        description="How many local branches the last scan saw under `refs/heads`. "
        "Counts what the last scan observed, not what has ever existed: a branch "
        "deleted on disk stops appearing here while its commits stay recorded.",
    )
    commit_count: int = Field(
        default=0,
        ge=0,
        description="How many commits the last scan observed, bounded by "
        "`developer_max_commits_per_scan`. Not the repository's whole history.",
    )
    first_commit_at: datetime | None = Field(
        default=None,
        description="The author date of the repository's earliest commit, or null "
        "when it has no commits at all. Null rather than 0, which would claim a "
        "zero-length history rather than the absence of one.",
    )
    latest_commit_at: datetime | None = Field(
        default=None,
        description="The author date of the repository's most recent commit, or "
        "null when it has none. This is the value an incremental re-scan passes "
        "as `--since`.",
    )
    working_tree_dirty: bool = Field(
        default=False,
        description="True when `git status --porcelain` reported at least one entry "
        "at scan time. A description of the working tree as it was, never a "
        "judgement about the uncommitted work.",
    )
    last_scanned_at: datetime | None = Field(
        default=None,
        description="When the last scan attempt finished. Null until the first "
        "scan, which is a fact the UI states rather than an absence.",
    )
    last_scan_status: str | None = Field(
        default=None,
        description="How the last attempt went: one of the "
        f"{('ok', 'error')} `GitScanStatus` values. Null only before the first "
        "scan.",
    )
    last_scan_error: str | None = Field(
        default=None,
        description="A human sentence describing why the last scan failed, or null "
        "after one that succeeded. Never a traceback and never an absolute path "
        "from inside the operator's home directory.",
    )
    created_at: datetime = Field(description="When the repository was registered.")
    updated_at: datetime = Field(
        description="When the row was last revised. A scan moves it; a metadata "
        "edit moves it; nothing else does."
    )


class RepositoryListRead(BaseModel):
    """One page of repositories, with the active/inactive split beside it.

    Flat rather than wrapped in the shared page envelope, matching the Phase 7
    list shapes. The split is here rather than left to the client because a
    browser-side tally can only count the rows the current page happens to carry,
    so it would understate a band that continues onto page two and the pager
    would have to be withdrawn for want of a total.
    """

    items: list[RepositoryRead] = Field(
        default_factory=list,
        description="The repositories on this page, by name. Empty when the filters match nothing.",
    )
    total: int = Field(
        default=0,
        ge=0,
        description="How many repositories match the filters, not the length of this page.",
    )
    limit: int = Field(default=0, ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(default=0, ge=0, description="How many matching rows were skipped.")
    active_count: int = Field(
        default=0,
        ge=0,
        description="Repositories not marked inactive, across every matching row "
        "rather than this page. `active_count + inactive_count == total`.",
    )
    inactive_count: int = Field(
        default=0,
        ge=0,
        description="Repositories the user paused, across every matching row.",
    )


class RepositoryCreate(BaseModel):
    """Register one local repository.

    ``local_path`` is the only required field. The server resolves the path and
    proves it is a git work tree **before** storing anything, so a bad path is a
    422 carrying a sentence and no row is ever created that fails on every future
    scan. A bare ``git init`` with no commits is valid and registers fine.
    """

    model_config = ConfigDict(populate_by_name=True)

    local_path: str = Field(
        min_length=1,
        max_length=4096,
        description="The local directory holding the work tree. May be absolute, "
        "relative or `~`-prefixed; it is expanded and resolved server-side and "
        "must land inside `developer_path_allowlist` when one is configured.",
    )
    name: str | None = Field(
        default=None,
        max_length=200,
        description="A label for it. Defaults server-side to the directory name, "
        "and is never machine-derived afterwards.",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="An optional note about what the repository is for.",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="Optional project to link it to, so a project page can show "
        "the repositories behind it.",
    )
    is_active: bool = Field(
        default=True,
        description="Whether it appears on the dashboard immediately. False "
        "registers it without surfacing it.",
    )


class RepositoryUpdate(BaseModel):
    """Edit a repository's metadata.

    **There is no ``local_path`` here, and that omission is the design.** The
    path is the row's identity and the one field checked against the filesystem;
    letting a PATCH move a repository would mean re-validating a second work tree
    and would leave the recorded history silently describing a different
    directory. ``primary_language`` is absent for the mirror-image reason: it is
    measured by the scan, not typed by the user. To point a repository at another
    directory, register the new one and remove the old.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(
        default=None,
        max_length=200,
        description="A new label. Null leaves the current one alone.",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="A new note. An explicit null clears it.",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="A project to link it to. An explicit null clears the link "
        "without deleting the repository's recorded history.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Whether it appears on the dashboard. An explicit null leaves "
        "the current value alone.",
    )


class CommitRead(BaseModel):
    """One commit, with the facts the repository recorded about it.

    ``author_name`` and ``author_email`` may be null because git recorded them
    and where it did not NEXUS does not invent them. ``branch`` is explicitly
    best-effort and null when no branch could be resolved — which is a claim that
    the scan could not place the commit, and never a guess.

    ``message`` is the subject line only. The body is deliberately not stored:
    commit messages carry ticket ids, credentials pasted in haste and paragraphs
    of context, none of which is needed to answer "how much changed".
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the stored commit.")
    repository_id: uuid.UUID = Field(
        description="The repository this commit was read from. Null for nothing: "
        "a commit is an observation about one work tree."
    )
    commit_hash: str = Field(
        description="The full object name as git reported it. Display code should use `short_hash`."
    )
    short_hash: str = Field(
        description="The display abbreviation, chosen by git from the repository's "
        "own object count. Present so a commit list never re-derives it."
    )
    committed_at: datetime = Field(
        description="The author date git recorded, in UTC. A fact about a commit "
        "object, never a claim about how long anyone worked."
    )
    message: str = Field(description="The commit's subject line, never its body.")
    author_name: str | None = Field(
        default=None,
        description="The author name git recorded, or null where it recorded none.",
    )
    author_email: str | None = Field(
        default=None,
        description="The author address git recorded, or null. Read only by an "
        "owner-scoped query and never shown to another account.",
    )
    additions: int = Field(
        default=0,
        ge=0,
        description="Lines the commit added, counted by git from the diff. A real "
        "zero for a commit that only moved a file or touched one binary blob.",
    )
    deletions: int = Field(
        default=0,
        ge=0,
        description="Lines the commit deleted, counted the same way.",
    )
    files_changed: int = Field(
        default=0,
        ge=0,
        description="Files the commit touched. Counts to the same set of commits "
        "as `additions`, because a commit with no countable lines is stored as 0 "
        "rather than dropped.",
    )
    branch: str | None = Field(
        default=None,
        description="Best-effort attribution: the branch whose head currently "
        "reaches this commit. Null when no branch could be resolved, which "
        "includes every commit on a detached HEAD and every commit whose only ref "
        "was deleted.",
    )
    created_at: datetime = Field(
        description="When NEXUS first recorded this commit, which is not the same "
        "instant as `committed_at` — a repository registered months after its "
        "first commit has both.",
    )


class CommitListRead(BaseModel):
    """One page of commits — the account-wide timeline or one repository's history.

    Flat for the reason every list on this surface is flat: the total is read
    beside the rows, not inside a ``meta`` object whose other members are
    pagination bookkeeping.
    """

    items: list[CommitRead] = Field(
        default_factory=list,
        description="The commits on this page, newest first. Empty when the window "
        "matches nothing, which is a measurement and not an error.",
    )
    total: int = Field(
        default=0,
        ge=0,
        description="How many commits match the filters, not the length of this page.",
    )
    limit: int = Field(default=0, ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(default=0, ge=0, description="How many matching rows were skipped.")


class BranchRead(BaseModel):
    """One branch as the last scan observed it.

    ``is_current`` marks the branch HEAD points at and ``is_default`` the one git
    resolved as the repository's default. On a detached HEAD neither is true,
    which is a normal state rather than a gap in the data.

    ``last_committed_at`` is the **tip's** committer date, not the date the
    branch was created: git does not record branch creation, and deriving one
    from the tip would let a two-year-old branch read as brand new.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the stored branch.")
    repository_id: uuid.UUID = Field(description="The repository the branch belongs to.")
    name: str = Field(description="The branch's short name, as git printed it.")
    is_current: bool = Field(
        default=False,
        description="True when HEAD pointed at this branch at the last scan.",
    )
    is_default: bool = Field(
        default=False,
        description="True for the branch git resolved as the repository's default.",
    )
    head_commit_hash: str | None = Field(
        default=None,
        description="The object name the branch tip points at, or null when the "
        "scan could not read it. The branch is listed either way.",
    )
    last_committed_at: datetime | None = Field(
        default=None,
        description="The committer date git reported for the head commit. Null is "
        "honest; 0 would not be.",
    )
    created_at: datetime = Field(description="When NEXUS first recorded this branch.")
    updated_at: datetime = Field(
        description="When the row was last refreshed by a scan. A branch's head "
        "moves on every commit, so this one genuinely is revised."
    )


class BranchListRead(BaseModel):
    """One page of branches. Usually the whole set; ``limit`` bounds a large repo."""

    items: list[BranchRead] = Field(
        default_factory=list,
        description="The branches on this page, current first then alphabetical.",
    )
    total: int = Field(default=0, ge=0, description="How many branches the repository has.")
    limit: int = Field(default=0, ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(default=0, ge=0, description="How many branches were skipped.")


class ScanRunRead(BaseModel):
    """One attempt to read a repository from disk, recorded whatever the outcome.

    A failed scan is a row here with ``status: 'error'`` and a sentence in
    ``error``, returned as a 200. That row — not an exception — is the whole
    mechanism behind "a broken repository must never break NEXUS": there is no
    code path where a bad repository produces a 500.

    ``commits_discovered`` against ``commits_added`` is the interesting pair. They
    differ on every re-scan of an unchanged repository, and the gap is the
    idempotent upsert working rather than anything having gone wrong: on such a
    re-scan ``commits_added`` is 0, which is the figure that proves the scan was
    idempotent.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the scan run.")
    repository_id: uuid.UUID = Field(description="The repository the attempt read.")
    status: str = Field(
        description=f"How the attempt ended: one of the {('ok', 'error')} "
        "`GitScanStatus` values. Never null — a row exists only once the attempt "
        "had already finished, and a scan is a synchronous request."
    )
    commits_discovered: int = Field(
        default=0,
        ge=0,
        description="What git returned for this attempt.",
    )
    commits_added: int = Field(
        default=0,
        ge=0,
        description="How many of those were new to storage. Lower than "
        "`commits_discovered` on a re-scan, by design.",
    )
    branches_discovered: int = Field(
        default=0,
        ge=0,
        description="What `for-each-ref` reported under `refs/heads`.",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Milliseconds the git call took. The only signal that a "
        "repository has become expensive to read, and explicitly not a measure "
        "of anybody's work.",
    )
    error: str | None = Field(
        default=None,
        description="A human sentence describing why the attempt failed, or null on "
        "success. Never a traceback.",
    )
    scanned_at: datetime = Field(
        description="When the attempt finished. The instant the run belongs to, "
        "which differs from `created_at` on a slow scan by the write itself."
    )
    created_at: datetime = Field(description="When the row was written.")


class DeveloperSummaryRead(BaseModel):
    """The account-wide headline figures, for the dashboard.

    Counts only. There is no score here and no comparison to a previous week as a
    verdict: ``commits_in_window`` is "how many commits were recorded", and the
    range it covers is carried beside it so the sentence printed underneath it can
    be true.

    ``has_data`` is the cold-start flag. False means every count below is
    legitimately zero **and** the page must explain that no repository has been
    registered or scanned yet, rather than rendering a dashboard of zeroes as
    though that were a finding about the account.
    """

    repository_count: int = Field(
        default=0,
        ge=0,
        description="Repositories registered for this account, paused ones included.",
    )
    active_repository_count: int = Field(
        default=0,
        ge=0,
        description="Of those, the ones not marked inactive.",
    )
    commit_count: int = Field(
        default=0,
        ge=0,
        description="Commits recorded across every repository, whole history. A "
        "real zero when nothing has been recorded.",
    )
    commits_in_window: int = Field(
        default=0,
        ge=0,
        description="Commits recorded inside the window below.",
    )
    active_days: int = Field(
        default=0,
        ge=0,
        description="Distinct UTC calendar dates inside the window carrying at "
        "least one commit. A count of days a commit was recorded on — never a "
        "count of days anybody worked.",
    )
    change_volume: int = Field(
        default=0,
        ge=0,
        description="Additions plus deletions inside the window. A count of lines "
        "changed, not of effort.",
    )
    repositories_touched: int = Field(
        default=0,
        ge=0,
        description="How many distinct repositories carried a commit inside the "
        "window. Distinct repositories rather than commits, so breadth and "
        "volume stay separate figures.",
    )
    window_days: int = Field(
        default=0,
        ge=1,
        description="The length of the window the in-window figures cover. Carried "
        "so no sentence about them can omit the range it describes.",
    )
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    latest_commit_at: datetime | None = Field(
        default=None,
        description="The most recent commit recorded across the account, or null "
        "when none has been.",
    )
    last_scanned_at: datetime | None = Field(
        default=None,
        description="When any of this account's repositories was last read from "
        "disk, or null when none has ever been scanned.",
    )
    has_data: bool = Field(
        default=False,
        description="False when there is nothing recorded to summarise, so the "
        "counts read as an absence rather than as a finding.",
    )
    summary: str = Field(
        default="",
        description="One factual sentence describing the counts, composed "
        "server-side so the header has one owner rather than one per page. Never "
        "claims working time, productivity, focus or effort.",
    )


class DeveloperActivityBucketRead(BaseModel):
    """One bucket of the activity series: a period and the commits inside it.

    Buckets are **zero-filled**. A quiet Tuesday arrives with ``commits: 0`` rather
    than being skipped, because a series that omitted empty buckets silently
    compresses the timeline and makes a sparse fortnight read as dense as a busy
    one — a misreading of the data rather than a presentational choice.
    """

    bucket_start: datetime = Field(
        description="The bucket's first instant, floored to UTC midnight, to the "
        "Monday starting its week, or to the first of its month. Consecutive "
        "buckets are exactly adjacent."
    )
    bucket_end: datetime = Field(
        description="When this bucket ends: the next bucket's start, or the "
        "window's end for the final one, which is therefore possibly narrower "
        "than its siblings.",
    )
    commits: int = Field(
        default=0,
        ge=0,
        description="Commits recorded in this bucket. Merges are excluded upstream, "
        "so this counts commits that represent work rather than commits replaying "
        "another branch's work.",
    )
    additions: int = Field(default=0, ge=0, description="Lines added in this bucket.")
    deletions: int = Field(default=0, ge=0, description="Lines deleted in this bucket.")
    files_changed: int = Field(
        default=0,
        ge=0,
        description="Files touched by this bucket's commits, summed across them.",
    )
    repository_count: int = Field(
        default=0,
        ge=0,
        description="Distinct repositories with at least one commit in this bucket.",
    )


class DeveloperActivityRead(BaseModel):
    """The activity series, with the window that produced it.

    ``repository_id`` echoes what the series was narrowed to, or null for the
    whole account, so a chart can say what it is showing without re-reading the
    query that asked for it.
    """

    granularity: str = Field(
        description="How wide one bucket is: one of the "
        f"{('day', 'week', 'month')} `ActivityGranularity` values.",
    )
    window_days: int = Field(
        default=0, ge=1, description="The length of the window the series covers."
    )
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    repository_id: uuid.UUID | None = Field(
        default=None,
        description="The repository the series was narrowed to, or null when it "
        "covers every repository the account owns.",
    )
    buckets: list[DeveloperActivityBucketRead] = Field(
        default_factory=list,
        description="Dense and ascending, gaps included. Never empty for a "
        "non-empty range: a range with no commits is all zeroes.",
    )
    total_commits: int = Field(
        default=0,
        ge=0,
        description="Commits across every bucket. Carried so a chart's axis and "
        "its caption cannot quote different sums.",
    )


class DeveloperMetricRead(BaseModel):
    """One metric, fully explained.

    The shape of :class:`app.services.developer.metrics.DeveloperMetric`, carried
    through unchanged. Four fields do real work:

    * ``value`` is ``number | null``, and null means *not measured*.
      ``recent_momentum`` divides commits in the last seven days by commits in
      the seven before, and a zero denominator produces null rather than an
      invented zero.
    * ``available`` with ``reason_if_unavailable`` is the positive form of the
      same fact: "we looked, and there was nothing to look at". A zero with
      ``available: true`` is a *different* answer — the arithmetic came out at
      zero — and must never be rendered with the reason attached.
    * ``definition`` says how it is computed and ``explanation`` says it again
      with the figures in it. An explanation with no digit in it is a backend
      bug: the metric module refuses to build one.
    * ``unit`` is data rather than decoration, so a ratio cannot be formatted as
      a count.
    """

    key: str = Field(
        description="The metric's stable identity: `commit_activity`, "
        "`repository_activity`, `change_volume`, `active_days`, `consistency`, "
        "`repository_growth`, `maintenance_activity` or `recent_momentum`. A "
        "closed set of exactly eight, always all eight, so a client that indexes "
        "by key never meets a hole where a card belongs.",
    )
    label: str = Field(description="Short human name for the card.")
    value: float | None = Field(
        default=None,
        description="The measured figure, or null when the metric could not be "
        "computed. Never 0 for that reason: a real zero is a measurement and is "
        "carried with `available: true`.",
    )
    unit: str = Field(
        description=f"What kind of figure this is: one of the "
        f"{('count', 'lines', 'days', 'ratio', 'score')} units. Note what is not "
        "in that list — nothing on this surface measures time spent.",
    )
    definition: str = Field(
        description="One sentence naming the inputs and the arithmetic, so the "
        "method can be shown above the result.",
    )
    window_days: int | None = Field(
        default=None,
        description="The window this instance measured, or null for a whole-history figure.",
    )
    source: str = Field(
        description="Which recorded facts the computation read, named so a reader "
        "can find the rows behind the number — for example `git_commits`.",
    )
    explanation: str = Field(
        description="The sentence shown to the user, carrying the figures it was "
        "built from. Never claims working time, productivity, focus or effort: a "
        "commit timestamp cannot support any of them.",
    )
    available: bool = Field(
        default=True,
        description="Whether there is a measurement at all. False with a reason "
        "attached, never a zero wearing a value.",
    )
    reason_if_unavailable: str | None = Field(
        default=None,
        description="Why the metric could not be computed, usually the shared "
        "'Not enough data to assess this yet'. Null whenever `available` is true.",
    )


class DeveloperFeatureValues(BaseModel):
    """The feature names, and only the feature names.

    An **extractor**, not a model: named numbers with a schema version so a later
    phase knows what each column meant. Nothing here is a prediction, a
    probability or a fitted parameter, and ``commit_frequency`` is a rate of
    recorded commits per day — not a statement about a person.

    The two nullable figures are the contract's own example of the null-not-zero
    rule: ``repository_age_days`` and ``inactivity_days`` are null for a subject
    with no commits, because 0 would assert "committed today".
    """

    commits_last_7d: int = Field(
        default=0, ge=0, description="Commits recorded in the last 7 days."
    )
    commits_last_30d: int = Field(
        default=0, ge=0, description="Commits recorded in the last 30 days."
    )
    active_days_7d: int = Field(
        default=0,
        ge=0,
        description="Distinct UTC dates in the last 7 days carrying at least one commit.",
    )
    active_days_30d: int = Field(
        default=0,
        ge=0,
        description="Distinct UTC dates in the last 30 days carrying at least one commit.",
    )
    files_changed_7d: int = Field(
        default=0, ge=0, description="Files touched by the commits in the last 7 days."
    )
    additions_7d: int = Field(
        default=0, ge=0, description="Lines added by the commits in the last 7 days."
    )
    deletions_7d: int = Field(
        default=0, ge=0, description="Lines deleted by the commits in the last 7 days."
    )
    repository_age_days: int | None = Field(
        default=None,
        description="Days between the earliest recorded commit and now. Null for a "
        "subject with no commits — 0 would claim it was created today.",
    )
    inactivity_days: int | None = Field(
        default=None,
        description="Days since the most recent recorded commit. Null for a subject "
        "with no commits, for the same reason.",
    )
    commit_frequency: float = Field(
        default=0.0,
        ge=0,
        description="Recorded commits per day across the window. A rate of events, "
        "never a rate of work.",
    )
    project_association: bool = Field(
        default=False,
        description="True when the repository is linked to a project — for an "
        "account-level row, when at least one of its repositories is.",
    )


class RepositoryFeatureVectorRead(DeveloperFeatureValues):
    """The same feature names, attributed to one repository.

    Carries a ``repository_id`` and deliberately no name: the feature vector is a
    machine surface, and a name belongs in the repository list a client already
    fetches. Joining on ``repository_id`` keeps the two from disagreeing about
    what a repository is called.
    """

    repository_id: uuid.UUID = Field(description="The repository these figures describe.")


class DeveloperFeatureVectorRead(BaseModel):
    """``GET /developer/features``: one row per account and one per repository.

    ``schema_version`` is what makes the vector usable later: a trainer that sees
    ``developer_features.v1`` knows the column meanings without having to trust
    that the client did not reorder them. There is no model, no inference and no
    registry behind this shape — Phase 10 does that.
    """

    schema_version: str = Field(
        default=FEATURE_SCHEMA_VERSION,
        description="The version of the column meanings below. A v2 must not "
        "typecheck against v1, which is why the frontend carries this as a closed "
        "union rather than a bare string.",
    )
    generated_at: datetime = Field(
        description="When the vector was extracted, from the database clock rather "
        "than the host's, so it belongs on the same timeline as the rows it reads."
    )
    window_days: int = Field(
        default=0,
        ge=1,
        description="The window ``commit_frequency`` was computed over.",
    )
    features: DeveloperFeatureValues = Field(
        description="The account-level row: the same features aggregated across "
        "every repository the account owns.",
    )
    repositories: list[RepositoryFeatureVectorRead] = Field(
        default_factory=list,
        description="One row per registered repository, empty on an account with none registered.",
    )


class ProjectDeveloperRead(BaseModel):
    """The developer view of one project.

    Reads the same facts as :class:`DeveloperSummaryRead` but narrowed to the
    repositories linked to this project. The repositories themselves are carried
    alongside the counts, so a project page can list them without a second request
    and cannot show a total that disagrees with the rows beneath it.
    """

    project_id: uuid.UUID = Field(description="The project these figures describe.")
    project_name: str = Field(description="The project's current name.")
    repositories: list[RepositoryRead] = Field(
        default_factory=list,
        description="The repositories linked to this project. The foreign key is "
        "`ON DELETE SET NULL`, so a repository outlives the project it was "
        "attached to — but a repository linked here was linked while it existed.",
    )
    repository_count: int = Field(
        default=0, ge=0, description="How many repositories are linked to the project."
    )
    commit_count: int = Field(
        default=0,
        ge=0,
        description="Commits recorded across this project's repositories, whole history.",
    )
    commits_in_window: int = Field(
        default=0, ge=0, description="Commits recorded inside the window below."
    )
    active_days: int = Field(
        default=0,
        ge=0,
        description="Distinct UTC dates inside the window carrying at least one "
        "commit from this project's repositories. Counted across the project "
        "rather than summed per repository, because one day with commits in three "
        "repositories is one active day.",
    )
    change_volume: int = Field(
        default=0,
        ge=0,
        description="Additions plus deletions inside the window, across this "
        "project's repositories.",
    )
    window_days: int = Field(
        default=0, ge=1, description="The length of the window the figures cover."
    )
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    latest_commit_at: datetime | None = Field(
        default=None,
        description="The most recent commit recorded for this project, or null "
        "while none of its repositories has one.",
    )
    has_data: bool = Field(
        default=False,
        description="False when none of this project's repositories has recorded "
        "a commit yet, so the counts read as an absence rather than a finding.",
    )
    summary: str = Field(
        default="",
        description="One factual sentence describing the counts, in the same "
        "register as the account summary: commits and lines, never effort.",
    )
