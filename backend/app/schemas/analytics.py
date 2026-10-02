"""Wire shapes for the Phase 6 analytics engine.

One convention runs through the whole module, and it is the phase's rule made into
a type: **a number that could not be computed is ``None``, never ``0``, and every
score carries ``available`` plus ``reason_if_unavailable``.** A client can therefore
render "Not enough activity yet" without knowing which field to check, because
every score says so itself.

The other convention: ``None`` is a *measurement*, not a hole. ``score=None`` with
``available=False`` is a positive claim — "we looked, and there was nothing to
measure" — and it is strictly more informative than a 0.

Two spellings for some facts
----------------------------
A few models carry the same fact under both a long name and a short one —
``rate``/``adherence_rate``, ``content``/``csv``, ``mean_absolute_error``/
``absolute_error``. They exist because two contracts were drafted for this phase
and consumers are written against both; whichever spelling a caller uses, a
model validator copies it to the other so the response always agrees with itself.
They are the one piece of redundancy in this module and they are deliberate.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "DEFAULT_ANALYTICS_WINDOW_DAYS",
    "MAX_ANALYTICS_RANGE_DAYS",
    "PRODUCTIVITY_SCORE_LABEL",
    "SCORE_DISCLAIMER",
    "AnalyticsCsvDataset",
    "AnalyticsGranularity",
    "AnalyticsTrendMetric",
    "ComparisonPoint",
    "ConsistencyRead",
    "CsvExportManifestRead",
    "CsvExportRead",
    "DailyMetricRead",
    "DeadlineAdherenceRead",
    "EstimationAccuracyRead",
    "FocusRead",
    "KnowledgeAnalyticsRead",
    "LearningAnalyticsRead",
    "MetricRange",
    "OverdueTaskRead",
    "OverviewRead",
    "ProductivityRead",
    "ProjectAnalyticsRead",
    "RebuildRead",
    "ScoreComponent",
    "ScoreComponentRead",
    "TagCount",
    "TagCountRead",
    "TaskAnalyticsRead",
    "TimeBucketRead",
    "TimeDistributionRead",
    "TimeSlice",
    "TrendPoint",
    "VelocityRead",
    "WorkloadRead",
    "absolute_change",
    "build_comparison",
    "median",
    "percent_change",
    "safe_ratio",
]

#: Granularities every time-series read accepts. Deliberately three: the
#: ``daily_metrics`` table stores exactly one row per day, so a week or a month is
#: a *bucket* of those rows rather than a second table with its own figures — see
#: the module docstring of migration ``0006``.
AnalyticsGranularity = Literal["day", "week", "month"]

#: Metrics ``GET /analytics/trends`` will plot. An allowlist, not a convenience:
#: the name is resolved against
#: :data:`app.repositories.analytics.METRIC_COLUMNS` and an unknown one is a 422,
#: because a name interpolated into ``SELECT`` is SQL injection behind a query
#: parameter.
AnalyticsTrendMetric = Literal[
    "tasks_created",
    "tasks_completed",
    "tasks_overdue",
    "tasks_cancelled",
    "tasks_blocked",
    "tasks_rescheduled",
    "planned_minutes",
    "actual_minutes",
    "work_sessions",
    "calendar_events",
    "knowledge_events",
    "projects_touched",
]

#: The three export datasets. A fixed vocabulary rather than "whatever table you
#: name": a CSV export is a file the user keeps, so its columns are a contract,
#: not a reflection of whatever exists this week.
AnalyticsCsvDataset = Literal["daily_metrics", "task_performance", "work_sessions"]

#: What every analytics window defaults to when the caller sends no dates. Seven
#: days is the shortest window in which a completion *rate* is not dominated by a
#: single task.
DEFAULT_ANALYTICS_WINDOW_DAYS = 7

#: Longest window a request may ask for. Bounded because each read fans out over
#: the window's days; an unbounded ``start_date=1970-01-01`` would be a scan of
#: every row the user has ever created.
MAX_ANALYTICS_RANGE_DAYS = 366

#: Every score carries this as its ``label``. The spec is explicit that these
#: numbers are NEXUS-derived and carry no scientific validity, so the label is a
#: field rather than a sentence in a docstring no user reads.
PRODUCTIVITY_SCORE_LABEL = "NEXUS Productivity Score"

#: Shown verbatim next to every score. Short enough to sit under a number without a
#: tooltip.
SCORE_DISCLAIMER = (
    "A NEXUS-derived metric computed from your own recorded activity. "
    "It is not a scientific or clinical measure."
)


def percent_change(current: float | None, previous: float | None) -> float | None:
    """Relative change from ``previous`` to ``current``, as a percentage.

    ``None`` — not ``Infinity``, not ``0`` — whenever the answer does not exist: a
    missing operand, or a previous value of zero. Dividing by zero is the exact
    failure the spec forbids ("never display NaN, Infinity or undefined%"), and
    returning ``0`` would be the more insidious one: "you went from nothing to
    something" is not "you changed by no amount", and a flat line through it draws
    a claim the data does not make.

    The result is **not** rounded. A caller displaying it can round for a human; a
    caller asserting on it can compare against the exact IEEE value.
    """
    if current is None or previous is None:
        return None
    if previous == 0:
        return None
    if math.isinf(current) or math.isnan(current):
        return None
    return (current - previous) / abs(previous) * 100.0


def absolute_change(current: float | None, previous: float | None) -> float | None:
    """``current - previous``, or ``None`` if either operand is absent."""
    if current is None or previous is None:
        return None
    return current - previous


def safe_ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    """``numerator / denominator`` as a percentage, or ``None``.

    The single choke point for "there is nothing here yet". Every rate in this module
    goes through it, so no endpoint can divide by zero and answer ``0``.
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator) * 100.0


class MetricRange(BaseModel):
    """The window a figure was computed over, and how it was bucketed.

    Carried on every aggregate response so a client never has to guess which days
    a number covers or re-derive the answer it just asked for — the same reason
    the planner views carry their ``window``.

    The range itself is validated here rather than in the service so an inverted
    or unbounded window is a 422 at the edge of the request, not an error surfacing
    somewhere inside an aggregation.
    """

    start_date: date
    end_date: date
    granularity: AnalyticsGranularity = Field(
        default="day",
        description="`day`, `week` or `month`. Bucketing is applied to the daily "
        "aggregates; it never changes what is counted.",
    )

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date.")
        if (self.end_date - self.start_date).days + 1 > MAX_ANALYTICS_RANGE_DAYS:
            raise ValueError(
                f"The analytics window may span at most {MAX_ANALYTICS_RANGE_DAYS} days."
            )
        return self

    @property
    def days(self) -> int:
        """Inclusive day count of the window."""
        return (self.end_date - self.start_date).days + 1


class DailyMetricRead(BaseModel):
    """One ``daily_metrics`` row, verbatim. Zeroes are real measurements."""

    metric_date: date
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_overdue: int = 0
    tasks_cancelled: int = 0
    tasks_blocked: int = 0
    tasks_rescheduled: int = 0
    planned_minutes: int = 0
    actual_minutes: int = 0
    work_sessions: int = 0
    calendar_events: int = 0
    knowledge_events: int = 0
    projects_touched: int = 0
    updated_at: datetime | None = Field(
        default=None,
        description="When the aggregate was last recomputed; how a client shows "
        "'updated 5 minutes ago' instead of a silently stale number.",
    )


class ScoreComponentRead(BaseModel):
    """One line of a score's breakdown.

    ``points`` and ``max_points`` are carried separately from the total so the UI
    can render "Completion +24 of 30" and a reader can check that the parts sum to
    the score without trusting the headline.
    """

    name: str
    points: float = Field(description="Points earned by this component.")
    max_points: float = Field(description="Points this component could have earned.")
    explanation: str = Field(description="Why it earned what it earned, in one sentence.")


#: The same model under the other name the phase's contracts use.
ScoreComponent = ScoreComponentRead


class ComparisonPoint(BaseModel):
    """One headline figure against the same-length period immediately before it.

    ``absolute_change`` is a difference and so is defined even from zero.
    ``percent_change`` is ``None`` when the previous period was zero or absent,
    because a percentage change from nothing has no honest value; see
    :func:`percent_change`.
    """

    label: str
    current: float = 0
    previous: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None


class ProductivityRead(BaseModel):
    """The NEXUS Productivity Score with the arithmetic that produced it.

    ``score`` is ``None`` whenever ``available`` is ``False``. That pairing is the
    contract: a client must never render ``score=0`` with ``available=False``,
    because 0 is a claim ("you did nothing") and the honest claim is "there is not
    enough data to say".
    """

    score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="0-100, or null when no component could be measured. "
        "NEXUS-derived, not a validated measure of human performance.",
    )
    available: bool
    reason_if_unavailable: str | None = None
    components: list[ScoreComponent] = Field(default_factory=list)
    formula: str = Field(description="The formula, in words, so the number is arguable.")
    label: str = PRODUCTIVITY_SCORE_LABEL
    disclaimer: str = SCORE_DISCLAIMER
    range: MetricRange | None = None
    weight_total: float = Field(
        default=100.0,
        description="Sum of the configured weights. Always 100: the settings validator "
        "refuses to start otherwise, so a score is never silently rescaled.",
    )


class ConsistencyRead(BaseModel):
    """Active days over the window."""

    score: int | None = Field(default=None, ge=0, le=100)
    available: bool
    reason_if_unavailable: str | None = None
    active_days: int
    window_days: int
    work_sessions: int = 0
    session_count: int = 0
    active_day_ratio: float | None = Field(
        default=None,
        description="active_days / window_days as a percent, or None when window_days is 0.",
    )
    longest_streak: int = 0
    current_streak: int = 0
    formula: str = ""
    label: str = "NEXUS Consistency Score"
    disclaimer: str = SCORE_DISCLAIMER
    range: MetricRange | None = None
    components: list[ScoreComponent] = Field(default_factory=list)


class FocusRead(BaseModel):
    """The NEXUS Focus Score, derived from recorded work-session behaviour.

    Nothing in this shape claims to measure human attention. ``interruptions``
    counts sessions in the window that never reached ``completed``.
    """

    score: int | None = Field(default=None, ge=0, le=100)
    available: bool
    reason_if_unavailable: str | None = None
    avg_session_minutes: float | None = None
    completed_planned_sessions: int = 0
    interruptions: int = 0
    reschedules: int = 0
    focused_minutes: int = 0
    total_minutes: int = 0
    formula: str = ""
    label: str = "NEXUS Focus Score"
    disclaimer: str = SCORE_DISCLAIMER
    range: MetricRange | None = None
    components: list[ScoreComponent] = Field(default_factory=list)


class DeadlineAdherenceRead(BaseModel):
    """Completed on time, completed late, and still open past due."""

    available: bool
    reason_if_unavailable: str | None = None
    on_time: int
    late: int
    still_overdue: int
    adherence_rate: float | None = Field(
        description="on_time / (on_time + late), as a percentage. Null when nothing "
        "has been finished — not 0%."
    )
    rate: float | None = Field(
        default=None,
        description="Alias of `adherence_rate`, kept so either spelling resolves.",
    )
    overdue_open: int = 0
    total_considered: int = 0
    range: MetricRange | None = None
    components: list[ScoreComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_rate(self) -> Self:
        self.rate = self.adherence_rate if self.adherence_rate is not None else self.rate
        self.adherence_rate = self.adherence_rate if self.adherence_rate is not None else self.rate
        return self


class EstimationAccuracyRead(BaseModel):
    """How close the estimates have been to the recorded actual durations.

    ``bias`` is the mean **signed** error, and the sign convention is
    ``estimated - actual``: a negative bias means the estimates habitually ran
    below the time actually taken — an under-estimate — and always sits beside a
    high ``under_estimation_rate``. The two figures can never tell opposite stories
    about the same tasks, which is the only reason for the sign; the opposite
    convention would print a *positive* bias next to a 100% under-estimation rate
    for the worked ``(60, 80)``/``(90, 150)`` pairs.

    An empty comparison is ``available=False``, never a zero bias.
    """

    available: bool
    reason_if_unavailable: str | None = None
    sample_count: int
    pairs_compared: int = 0
    absolute_error: float | None = None
    mean_absolute_error: float | None = None
    percentage_error: float | None = None
    mean_percentage_error: float | None = None
    bias: float | None = Field(
        description="Mean signed error, in minutes, as estimated - actual. "
        "Negative means the estimates ran below the time actually taken "
        "(under-estimated); it always agrees with under_estimation_rate."
    )
    median_error: float | None = Field(
        description="Minutes. Null over an empty comparison, never 0."
    )
    under_estimation_rate: float | None = None
    over_estimation_rate: float | None = None
    underestimation_rate: float | None = None
    overestimation_rate: float | None = None
    range: MetricRange | None = None

    @model_validator(mode="after")
    def _sync_aliases(self) -> Self:
        self.mean_absolute_error = self.absolute_error or self.mean_absolute_error
        self.absolute_error = self.absolute_error or self.mean_absolute_error
        self.mean_percentage_error = self.percentage_error or self.mean_percentage_error
        self.percentage_error = self.percentage_error or self.mean_percentage_error
        self.pairs_compared = self.sample_count or self.pairs_compared
        self.sample_count = self.sample_count or self.pairs_compared
        self.underestimation_rate = self.under_estimation_rate or self.underestimation_rate
        self.under_estimation_rate = self.under_estimation_rate or self.underestimation_rate
        self.overestimation_rate = self.over_estimation_rate or self.overestimation_rate
        self.over_estimation_rate = self.overestimation_rate or self.overestimation_rate
        return self


class WorkloadRead(BaseModel):
    """What is open now, and how much of the available time it has claimed.

    ``workload_ratio`` is ``None`` — not 0 — when no availability rules exist,
    because without a declared capacity the honest answer is "unknown", not "no
    load".
    """

    open_tasks: int
    high_priority_open: int
    overdue_open: int
    scheduled_minutes: int
    available_minutes: int | None = Field(
        description="Minutes the user declared available over the window. Null when "
        "no availability rules exist — that is unconfigured, not zero."
    )
    workload_ratio: float | None = Field(
        description="scheduled / available as a percentage; null when available is null."
    )
    average_daily_scheduled_minutes: float | None = None
    high_priority_tasks: int = 0
    overdue_tasks: int = 0
    actual_minutes: int = 0
    available: bool = True
    reason_if_unavailable: str | None = None
    comparison: list[ComparisonPoint] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    priority_counts: dict[str, int] = Field(default_factory=dict)
    range: MetricRange | None = None


class TimeBucketRead(BaseModel):
    """One slice of the time-distribution breakdown."""

    key: str
    label: str
    minutes: int
    share: float | None = Field(
        description="Share of the total as a percentage; null when the total is zero."
    )


class TimeSlice(BaseModel):
    """One project's share of the tracked time in a window."""

    project_id: str
    project_name: str
    minutes: int = 0
    share: float | None = Field(
        description="Percent of the window's total, or None when the total is 0."
    )


class TimeDistributionRead(BaseModel):
    """Where the recorded work time went.

    Sessions with no project are reported separately as unassigned rather than
    folded into an arbitrary "other" project: a session is either attributable or
    it is not, and inventing a bucket for it would overstate every real project.
    """

    total_minutes: int
    available: bool
    reason_if_unavailable: str | None = None
    unassigned_minutes: int = 0
    project_id: str | None = None
    by_project: list[TimeBucketRead] = Field(default_factory=list)
    by_task: list[TimeBucketRead] = Field(default_factory=list)
    slices: list[TimeSlice] = Field(default_factory=list)
    range: MetricRange | None = None


class VelocityRead(BaseModel):
    """NEXUS project velocity — tasks completed per week. Not Agile, and says so."""

    tasks_per_week: float | None
    estimated_minutes_per_week: float | None
    weeks_measured: float
    definition: str = (
        "NEXUS defines velocity as tasks completed per calendar week, from the "
        "recorded completion timestamps. It is not an Agile story-point velocity."
    )


class ProjectAnalyticsRead(BaseModel):
    """One project's figures over the window."""

    project_id: uuid.UUID
    name: str
    status: str
    total_tasks: int
    completed_tasks: int
    remaining_tasks: int
    overdue_tasks: int
    completion_rate: float | None = Field(
        description="completed / total as a percentage; null when the project has no tasks."
    )
    total_work_minutes: int
    avg_task_actual_minutes: float | None = None
    estimation: EstimationAccuracyRead | None = None
    velocity: VelocityRead | None = None
    velocity_tasks_per_week: float | None = Field(
        default=None,
        description="NEXUS definition: tasks completed per calendar week in the window. "
        "Not an Agile velocity.",
    )
    weekly_completed: list[int] = Field(default_factory=list)
    work_minutes: int = 0
    estimated_minutes: int = 0
    actual_minutes: int = 0
    avg_task_minutes: float | None = None
    activity_events: int = 0
    available: bool = True
    reason_if_unavailable: str | None = None
    range: MetricRange | None = None


class OverdueTaskRead(BaseModel):
    """A task past its due date, for the drill-down list."""

    task_id: uuid.UUID
    title: str
    due_date: date | None = None
    days_overdue: int | None = Field(
        description="Null when the task has no due date — it is on this list because "
        "it is unfinished, not because it is late."
    )
    priority: str


class TaskAnalyticsRead(BaseModel):
    """Task-shaped figures over the window, plus estimation accuracy.

    ``by_status`` and ``by_priority`` always carry every member of their enum, set
    to zero when empty, so the response shape does not change as the last task of a
    status is deleted and no client needs a ``.get()`` default.
    """

    total_tasks: int
    completed_tasks: int
    open_tasks: int
    overdue_tasks: int
    cancelled_tasks: int
    blocked_tasks: int
    completion_rate: float | None = Field(
        description="completed / total as a percentage; null when there are no tasks, not 0%."
    )
    overdue_rate: float | None = Field(
        description="overdue / total as a percentage; null when there are no tasks."
    )
    avg_completion_days: float | None = Field(
        description="Mean days from creation to completion across completed tasks; "
        "null when nothing has been completed."
    )
    avg_cycle_minutes: float | None = None
    avg_estimate_error_minutes: float | None = None
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_cancelled: int = 0
    tasks_blocked: int = 0
    tasks_overdue: int = 0
    tasks_rescheduled: int = 0
    available: bool = True
    reason_if_unavailable: str | None = None
    estimation: EstimationAccuracyRead | None = None
    top_overdue: list[OverdueTaskRead] = Field(default_factory=list)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    range: MetricRange | None = None


class LearningAnalyticsRead(BaseModel):
    """Recorded study and knowledge-adjacent activity.

    NEXUS records **no explicit "learning" flag** on a project, task or session —
    Phases 1-5 have no such column. These figures are therefore built from two
    things that *are* recorded, and the ``definition`` field says which, so the
    number is not read as an inference about the user.
    """

    available: bool
    reason_if_unavailable: str | None = None
    study_events: int = Field(description="Calendar events typed `study` in the window.")
    study_minutes: int
    knowledge_linked_tasks: int | None = Field(
        default=None,
        description="Tasks completed in projects the user also wrote knowledge about "
        "in the same window. Always null today: Phase 5 knowledge events carry no "
        "task_id and no project_id, so that correlation is not computable from the "
        "rows that exist. Null rather than zero, because zero would claim the "
        "measurement was made and came back empty.",
    )
    knowledge_interactions: int
    notes_created: int = 0
    notes_updated: int = 0
    projects_touched: int = 0
    basis: str = (
        "Derived from calendar_events with event_type='study' and from recorded "
        "knowledge activity events. No inference is made about mastery."
    )
    definition: str = (
        "Learning activity is measured from calendar events typed `study` and from "
        "recorded knowledge events. NEXUS stores no explicit learning flag, so "
        "nothing is inferred beyond those recorded facts."
    )
    range: MetricRange | None = None


class TagCountRead(BaseModel):
    """A named thing and how many times it was used."""

    key: str
    label: str
    count: int


class TagCount(BaseModel):
    """One tag and the number of notes carrying it."""

    tag_id: str
    name: str
    note_count: int


class KnowledgeAnalyticsRead(BaseModel):
    """Recorded Phase 5 knowledge activity. No inference of mastery.

    ``interactions`` counts **writes only**: Phases 1-5 record no note *views* —
    :class:`~app.models.enums.ActivityEvent` has no ``note_viewed`` member — so
    "knowledge viewed" is not computable from what is stored and no proxy is
    invented for it.
    """

    available: bool
    reason_if_unavailable: str | None = None
    notes_created: int
    notes_updated: int
    concepts_created: int
    resources_added: int
    bookmarks_added: int
    links_created: int
    notes_published: int = 0
    documents_added: int = 0
    interactions: int = 0
    most_used_tags: list[TagCountRead] = Field(default_factory=list)
    most_active_concepts: list[TagCountRead] = Field(default_factory=list)
    top_tags: list[TagCount] = Field(default_factory=list)
    notes_by_status: dict[str, int] = Field(default_factory=dict)
    range: MetricRange | None = None


class TrendPoint(BaseModel):
    """One bucket of a time series, against the same bucket of the previous period.

    ``value`` is the bucket's total, never a running total and never interpolated
    across a gap: a day with no rows is a day with no activity, and the series
    shows that rather than a value nobody recorded.
    """

    bucket: date | None = None
    label: str
    value: float
    previous: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def _sync_period(self) -> Self:
        self.period_start = self.period_start or self.bucket
        self.bucket = self.bucket or self.period_start
        return self


class OverviewRead(BaseModel):
    """The dashboard payload: totals, comparisons, the score, and deadline adherence.

    ``productivity`` and ``deadlines`` are folded in rather than served by their own
    routes. Both are single computations over the same window this response already
    reads, and the brief asks for fewer endpoints where one is cleaner.
    """

    range: MetricRange
    previous_range: MetricRange | None = None
    #: True when no aggregate has been written for part of the window. The brief
    #: forbids silently showing stale numbers, so the client can say so.
    stale: bool = False
    is_stale: bool = False
    aggregates_through: date | None = None
    data_as_of: date | None = None
    totals: list[ComparisonPoint] = Field(default_factory=list)
    productivity: ProductivityRead
    deadlines: DeadlineAdherenceRead
    consistency: ConsistencyRead | None = None
    focus: FocusRead | None = None
    estimation: EstimationAccuracyRead | None = None
    workload: WorkloadRead | None = None
    daily: list[DailyMetricRead] = Field(default_factory=list)
    reason_if_empty: str | None = None

    @model_validator(mode="after")
    def _sync_staleness(self) -> Self:
        self.data_as_of = self.data_as_of or self.aggregates_through
        self.aggregates_through = self.aggregates_through or self.data_as_of
        self.is_stale = self.is_stale or self.stale
        self.stale = self.stale or self.is_stale
        return self


class RebuildRead(BaseModel):
    """What a rebuild wrote. The 202 body."""

    rows_written: int
    start_date: date
    end_date: date


class CsvExportRead(BaseModel):
    """A rendered CSV export.

    Returned as a JSON envelope rather than streamed so the row cap is enforceable
    and the client can show "exported N rows" before it writes a file. ``csv`` is
    RFC 4180 text with a header row first; ``content`` is the same value under the
    other name the phase's contracts use, and a validator keeps them identical.
    """

    dataset: str
    filename: str
    content_type: str = "text/csv; charset=utf-8"
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    truncated: bool = Field(
        default=False, description="True when the row cap cut the export short."
    )
    csv: str = ""
    content: str = ""

    @model_validator(mode="after")
    def _sync_body(self) -> Self:
        self.csv = self.csv or self.content
        self.content = self.content or self.csv
        return self


class CsvExportManifestRead(BaseModel):
    """The export manifest: which datasets exist and what columns they carry.

    Lets a client build an importer — and an inspector for the Phase 10 training
    set — without hardcoding a column list in TypeScript. The column list is a
    contract: changing it is a breaking change for importers.
    """

    datasets: list[str] = Field(default_factory=list)
    columns: dict[str, list[str]] = Field(default_factory=dict)
    content_type: str = "text/csv"
    note: str = (
        "Every dataset is scoped to the authenticated user. The column list is the "
        "contract: a change to it is a breaking change for importers."
    )


def build_comparison(label: str, current: float | None, previous: float | None) -> ComparisonPoint:
    """Build a :class:`ComparisonPoint` from the two raw figures.

    Both change fields are routed through the null-returning helpers.

    Exists so no caller hand-rolls ``(current - previous) / previous`` and walks
    past the guard that lives in :func:`percent_change`.
    """
    current_value = float(current) if current is not None else 0.0
    previous_value = float(previous) if previous is not None else None
    return ComparisonPoint(
        label=label,
        current=current_value,
        previous=previous_value,
        absolute_change=absolute_change(current_value, previous_value),
        percent_change=percent_change(current_value, previous_value),
    )


def median(values: Sequence[float]) -> float | None:
    """The median of ``values``, or ``None`` for an empty sequence.

    ``None`` rather than ``0``: "the median of nothing" is an absence, and
    reporting it as zero would say the errors were all zero minutes.
    """
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[midpoint])
    return (float(ordered[midpoint - 1]) + float(ordered[midpoint])) / 2.0


#: ``TaskAnalyticsRead`` and ``KnowledgeAnalyticsRead`` name forward references;
#: resolving them here rather than by reordering keeps the module reading in the
#: order the API returns it.
TaskAnalyticsRead.model_rebuild()
KnowledgeAnalyticsRead.model_rebuild()
ProjectAnalyticsRead.model_rebuild()
OverviewRead.model_rebuild()
CsvExportRead.model_rebuild()
