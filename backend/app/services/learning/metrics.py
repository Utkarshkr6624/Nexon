"""The learning metrics, as pure functions from recorded activities to numbers.

Nothing here touches a database, an ORM model, a clock or a subprocess. That is
not restraint for its own sake — it is the property that makes these figures
auditable. A metric computed from ``(sessions=6, minutes=180, window=30)``
returns the same thing forever, on any machine, and can be asserted to the exact
value in a test that provisions no database. It is the same split
:mod:`app.services.developer.metrics` and :mod:`app.services.risk.scoring`
already make, and for the same reason: the service layer gathers rows, this module
does the arithmetic.

This module also owns the shape of a recorded activity
---------------------------------------------------------
:class:`ActivitySample` and :class:`GoalSample` live here rather than in
:mod:`app.services.learning.gaps` because they are the *reading* of the two
Phase 9 tables — reduced to the fields the metrics read, with no ORM type behind
them — and :mod:`~app.services.learning.gaps` consumes the very same samples. The
service builds each row once and hands it to both, so there is exactly one
definition of "a session" in the codebase rather than one per consumer.

Why the granularity enum is repeated rather than imported
--------------------------------------------------------
:mod:`app.services.developer.metrics` already defines an ``ActivityGranularity``
of ``day``/``week``/``month``, and this module defines a second one with the same
members. That duplication is deliberate. A learning chart and a commit chart are
different products with different windows, and importing one phase's chart
vocabulary into another would make it impossible to change either without a
migration of the other. Three members and four lines is a cheaper price than a
dependency between phases that the contracts keep deliberately separate.

A session is a recorded row, and its duration may be nothing
------------------------------------------------------------
``duration_minutes`` is nullable on ``learning_activities`` for a reason stated in
:mod:`app.models.learning`: an *event* ("I finished the chapter") is not a *span*
("I spent forty minutes on it"). So :func:`learning_minutes` sums only the
durations that were recorded and says how many rows carried one, and it never
converts a null into a zero. An account whose every activity is an event has a
recorded total of zero minutes, and the sentence says so with the count beside
it — ``"0 minutes across 3 of the 3 recorded activities, none of which carries a
duration"`` — rather than implying three measured zero-length sessions.

Levels are the user's, or visibly derived
----------------------------------------
No function in this module reads, infers, adjusts or scores a skill level. The
levels live in :mod:`app.services.learning.gaps`, and they appear on this screen
only through the evidence counts and session figures computed here. That is not a
division of labour picked for tidiness: a level that moved because a metric
changed would be NEXUS editing a claim the user made, and the brief's first rule
forbids exactly that. :data:`FORBIDDEN_CLAIM_WORDS` is checked on every
explanation so the *sentences* hold the same line, and a metric whose copy
reached for "mastery", "proficient" or "competence" fails where a reviewer reads
it rather than silently on a dashboard.

Absence of measurement is not zero
----------------------------------
This is the module's other load-bearing rule, stated in
:class:`LearningMetric`'s own field list: **a real measurement of zero is
``value=0, available=True``; an absence of measurement is ``available=False`` with
a reason.** They are different answers. A window with no recorded sessions is a
*measured* zero — "nothing was recorded in these 30 days" is a true sentence about
the record — whereas a completion rate with no goals to divide by has no answer
that is not invented, and is declined.

Which metrics can decline
-------------------------
Three can, and each for the same reason: a ratio with an empty denominator.

* :func:`goal_progress` — the mean of the user's own percentages, with no open
  goal there is no set to average.
* :func:`goal_deadline_distance_days` — the mean distance to a date, with no dated
  open goal there is no date to be near or late for.
* :func:`completion_rate` — ``0 / 0`` is a number and a mistake. A new account
  that has written no goals has not completed 0% of them; it has a question that
  was never asked.
* :func:`skill_activity_frequency` — an average per skill, with no skill named by
  any activity there is no denominator.

The other four are counts or day-totals over rows the caller passed in, and a
count of zero rows is a measurement. :func:`skill_activity_frequency` declines
only on an empty denominator rather than on an empty window, which is the
distinction the whole design turns on.

Every explanation carries its figures
------------------------------------
:class:`LearningMetric.__post_init__` rejects an explanation with no digit in it,
the same rule :class:`~app.services.risk.recommendation.RecommendationDraft`
enforces on a recommendation's reason and
:class:`~app.services.developer.metrics.DeveloperMetric` enforces on a developer
metric. "You have been learning consistently" is an adjective; "5 of the last 30
days carried a recorded activity" is a finding. Requiring the figure at
construction makes that failure loud in a test rather than silent on a screen.

Why this module reads no clock
------------------------------
Every windowed function takes its boundaries from the caller, including the
trailing seven-day window. ``window_end`` *is* "now" for everything here — the
service takes that instant from the database rather than from the application
machine, so two servers cannot disagree about which day a session landed on. A
pure module that called :func:`datetime.now` would be untestable at an exact value
and would disagree with itself across a midnight.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from app.models.enums import LearningGoalStatus

__all__ = [
    "ACTIVITY_GRANULARITIES",
    "FORBIDDEN_CLAIM_WORDS",
    "LEARNING_METRICS",
    "METRIC_UNITS",
    "NOT_ENOUGH_DATA",
    "OPEN_GOAL_STATUSES",
    "RECENT_MONTH_DAYS",
    "RECENT_WEEK_DAYS",
    "ActivityGranularity",
    "ActivitySample",
    "GoalSample",
    "LearningActivitySeries",
    "LearningBucket",
    "LearningMetric",
    "MetricKey",
    "activity_series",
    "bucket_for",
    "build_metrics",
    "completion_rate",
    "goal_deadline_distance_days",
    "goal_progress",
    "learning_consistency",
    "learning_minutes",
    "sessions_last_7d",
    "sessions_last_30d",
    "skill_activity_frequency",
]

#: What every metric says when it cannot judge. One string, not one per metric:
#: this is product copy, and product copy belongs in one place. The value and the
#: spelling match :data:`app.services.risk.scoring.NOT_ENOUGH_DATA` and
#: :data:`app.services.developer.metrics.NOT_ENOUGH_DATA` deliberately — a user
#: who reads "Not enough data" on three screens is reading one phrase, and three
#: phrasings would read as three different answers to the same question.
NOT_ENOUGH_DATA = "Not enough data to assess this yet"

#: Words no explanation here may contain. Not a style preference: Phase 9's first
#: rule is that NEXUS never claims to know how good anyone is at anything, and an
#: explanation that reaches for "mastery", "proficiency" or "competence" is making
#: a claim about a person that no row in ``learning_activities`` can support. They
#: are checked in :func:`_check_explanation` so the rule is a property of the code
#: rather than of whoever writes the next metric's copy.
FORBIDDEN_CLAIM_WORDS: frozenset[str] = frozenset(
    {
        "aptitude",
        "competence",
        "competent",
        "gifted",
        "master",
        "mastered",
        "mastering",
        "mastery",
        "proficien",
        "proficiency",
        "proficient",
        "talent",
    }
)

#: The status values that make a goal "open" for the purposes of the goal
#: metrics. ``COMPLETED`` and ``ARCHIVED`` are excluded and the exclusion is
#: argued in :mod:`app.models.enums`: an archived goal must not count as
#: incomplete work anywhere, so averaging its 0% into "goal progress" would make
#: discarding a goal look like a failure to finish it. ``PAUSED`` is included: a
#: goal the user intends to return to is still open, and merging it with
#: ``NOT_STARTED`` would make one of the two lie.
OPEN_GOAL_STATUSES: frozenset[LearningGoalStatus] = frozenset(
    {
        LearningGoalStatus.NOT_STARTED,
        LearningGoalStatus.IN_PROGRESS,
        LearningGoalStatus.PAUSED,
    }
)

#: The trailing window :func:`sessions_last_7d` reads. Seven days because that is
#: the shortest span over which "have I been at this lately" has an answer at all
#: — a single day reads as a mood rather than a pattern.
RECENT_WEEK_DAYS = 7

#: The trailing window :func:`sessions_last_30d` reads. Thirty because that is the
#: phase's default window (``learning_default_window_days``), and because it is
#: also the window the request-scoped metrics use, so on a default request every
#: figure on the page covers exactly the same range.
RECENT_MONTH_DAYS = 30


class MetricKey(StrEnum):
    """The eight learning metric identities Phase 9 promises to report.

    A :class:`~enum.StrEnum` rather than eight bare strings because the key is a
    wire value: a dictionary key in ``/learning/metrics``, a column name in the
    ``learning_features.v1`` export, and the identity of a test case. One
    vocabulary object means a typo is an import error rather than a metric that
    silently never appears on the dashboard.
    """

    SESSIONS_LAST_7D = "sessions_last_7d"
    SESSIONS_LAST_30D = "sessions_last_30d"
    LEARNING_MINUTES = "learning_minutes"
    GOAL_PROGRESS = "goal_progress"
    GOAL_DEADLINE_DISTANCE_DAYS = "goal_deadline_distance_days"
    COMPLETION_RATE = "completion_rate"
    LEARNING_CONSISTENCY = "learning_consistency"
    SKILL_ACTIVITY_FREQUENCY = "skill_activity_frequency"


#: The eight metric keys in the order ``/learning/metrics`` returns them, which is
#: the order ``learning_features.v1`` names its columns. A tuple and not a set:
#: the order is a reading order a human follows, and a set would hand the same
#: eight metrics back in whatever order the interpreter happened to hash them.
LEARNING_METRICS: tuple[MetricKey, ...] = tuple(MetricKey)

#: The unit each metric's value is expressed in. Named rather than inferred from
#: the magnitude: ``0.5`` is a ratio or a score depending on which metric it came
#: from, and a client that guesses picks the wrong formatter and renders
#: "0.5 minutes".
METRIC_UNITS: dict[MetricKey, str] = {
    MetricKey.SESSIONS_LAST_7D: "count",
    MetricKey.SESSIONS_LAST_30D: "count",
    MetricKey.LEARNING_MINUTES: "minutes",
    MetricKey.GOAL_PROGRESS: "percent",
    MetricKey.GOAL_DEADLINE_DISTANCE_DAYS: "days",
    MetricKey.COMPLETION_RATE: "ratio",
    MetricKey.LEARNING_CONSISTENCY: "ratio",
    MetricKey.SKILL_ACTIVITY_FREQUENCY: "ratio",
}


class ActivityGranularity(StrEnum):
    """How wide one point on the learning activity chart is.

    Deliberately a second, identical enum to
    :class:`app.services.developer.metrics.ActivityGranularity`; see the module
    docstring for why two phases deliberately do not share one.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


#: The accepted ``granularity`` values, for an error message that lists them.
ACTIVITY_GRANULARITIES: tuple[str, ...] = tuple(member.value for member in ActivityGranularity)


@dataclass(frozen=True, slots=True)
class LearningMetric:
    """One metric: its number, and everything needed to argue with the number.

    A metric is never returned as a bare float. The bare float is the part that
    goes stale — a card showing ``0.18`` with no definition, no window and no
    source is a number the user has to simply trust — and ``definition``,
    ``window_days``, ``source`` and ``explanation`` are required fields rather
    than optional context for exactly that reason.

    ``value`` is a ``float`` and not ``float | None`` so this carrier matches
    :class:`~app.services.developer.metrics.DeveloperMetric`: an unavailable metric
    carries a zero it does not mean, and ``available=False`` is what tells every
    reader to ignore it. The wire layer maps an unavailable metric's ``value`` to
    null, which is where a figure that could not be computed becomes ``null``
    rather than ``0``.

    Frozen with ``slots=True`` because a metric passes from a metric function to a
    service to a schema without anything in between having a reason to edit it.

    Attributes:
        key: The metric's stable identity, a :class:`MetricKey` value.
        label: Short human name for the card.
        value: The measured figure.
        unit: One of ``count``, ``minutes``, ``percent``, ``days``, ``ratio``.
        definition: One sentence saying how ``value`` is computed.
        window_days: The window the figure covers, or ``None`` for whole history.
        source: Which recorded facts the computation read.
        explanation: The sentence shown to the user, carrying its own figures.
        available: Whether there is a measurement at all.
        reason_if_unavailable: Why not, when there is not. ``None`` when there is.
    """

    key: str
    label: str
    value: float
    unit: str
    definition: str
    window_days: int | None
    source: str
    explanation: str
    available: bool = True
    reason_if_unavailable: str | None = None

    def __post_init__(self) -> None:
        """Reject a metric that could not explain itself.

        Raises:
            ValueError: If the explanation carries no digit, names a claim this
                phase is forbidden to make, or the metric is unavailable without
                saying why. The first is the brief's "a metric with no figure in
                it is a bug" — an adjective is not an explanation. The second
                keeps the copy from reaching for "mastery" or "proficiency", which
                no row in ``learning_activities`` can support. The third is what
                stops an unavailable metric rendering as an unexplained blank.
        """
        _check_explanation(self.key, self.explanation)
        if not self.available and not self.reason_if_unavailable:
            raise ValueError(
                f"Metric {self.key!r} is unavailable and must say why. An unavailable "
                "metric with no reason renders as an unexplained blank."
            )


@dataclass(frozen=True, slots=True)
class ActivitySample:
    """One recorded learning activity, reduced to the fields the metrics read.

    Deliberately not an ORM model: the metrics must be computable from a
    hand-built list in a test with no database in it. A sample is what the service
    layer builds from ``learning_activities`` rows.

    ``duration_minutes`` is nullable and stays nullable all the way to the
    arithmetic — see the module docstring. ``skill_id`` is nullable for the same
    reason the column is: a study session may name no skill yet, and only
    :func:`skill_activity_frequency` cares, which declines rather than counting
    those rows as evidence for nothing.

    Attributes:
        occurred_at: When the activity happened.
        activity_id: Its identity, so a caller can join two passes back together.
        activity_type: A :class:`~app.models.enums.LearningActivityType` value.
        skill_id: The skill it is evidence for, or ``None``.
        goal_id: The goal it was recorded towards, or ``None``.
        duration_minutes: The recorded span, or ``None`` for an event.
    """

    occurred_at: datetime
    activity_id: uuid.UUID | None = None
    activity_type: str = "study_session"
    skill_id: uuid.UUID | None = None
    goal_id: uuid.UUID | None = None
    duration_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class GoalSample:
    """One learning goal, reduced to the fields the goal metrics read.

    ``progress`` is the user's own percentage and is never recomputed from
    activities: a study session and a goal are different units, and deriving one
    from the other is the first step towards NEXUS claiming to know whether
    somebody learned something.

    Attributes:
        goal_id: The goal's identity.
        status: A :class:`~app.models.enums.LearningGoalStatus` value.
        progress: The user's own 0-100 progress figure.
        target_date: The user's own deadline, or ``None``.
    """

    goal_id: uuid.UUID | None = None
    status: LearningGoalStatus = LearningGoalStatus.NOT_STARTED
    progress: int = 0
    target_date: date | None = None


@dataclass(frozen=True, slots=True)
class LearningBucket:
    """One point on the learning activity chart: a period and what fell inside it.

    ``start`` is the bucket's first instant, floored to UTC midnight (or to the
    Monday starting its week, or to the first of its month) so consecutive buckets
    are exactly adjacent and a session at 00:00 belongs to exactly one of them.

    ``activity_count`` and ``session_minutes`` are kept separately because "6
    sessions" and "180 minutes" answer different questions, and a client forced to
    re-derive one from the other has no way to know which the tooltip meant.
    ``skill_ids`` and ``goal_ids`` exclude nulls by construction: they are frozensets
    of what the activities in this bucket actually named, which is what a
    "what did I work on" tooltip wants.
    """

    start: datetime
    label: str
    activity_count: int
    session_minutes: int
    skill_ids: frozenset[uuid.UUID] = frozenset()
    goal_ids: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class LearningActivitySeries:
    """A complete, gap-free run of buckets across a range.

    ``buckets`` is dense by construction — one entry per day, week or month from
    the first to the last, quiet periods included as zeros. That is the property
    the series builder exists for: a chart emitting only the days with sessions
    would silently drop the quiet Tuesday, and a reader counting the bars would see
    five active days and read them as consecutive.
    """

    granularity: str
    start: datetime
    end: datetime
    buckets: tuple[LearningBucket, ...]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    """Read an instant as UTC, normalising an aware one to that zone.

    The same rule :mod:`app.services.developer.metrics` applies before bucketing:
    an instant with no offset would otherwise be counted against the machine's
    local midnight, so the same session could land on two different days depending
    on which server served the request.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _check_explanation(key: str, explanation: str) -> None:
    """Enforce the two rules every explanation sentence must satisfy.

    Split out of :meth:`LearningMetric.__post_init__` so the *building* helpers
    below can check a sentence before it is attached to a metric, and so the rule
    has one implementation rather than two that could drift.

    Args:
        key: The metric key, named in the error so the failure points at a caller.
        explanation: The sentence to check.

    Raises:
        ValueError: If it carries no digit, or uses forbidden claim vocabulary.
    """
    if not any(character.isdigit() for character in explanation):
        raise ValueError(
            f"Metric {key!r} has an explanation with no figure in it: {explanation!r}. "
            "An explanation must state the numbers it was built from, or it is an "
            "adjective rather than a reason."
        )
    lowered = explanation.lower()
    forbidden = sorted(word for word in FORBIDDEN_CLAIM_WORDS if word in lowered)
    if forbidden:
        raise ValueError(
            f"Metric {key!r} explains itself with {', '.join(forbidden)!r}. No recorded "
            "learning activity can show what someone mastered or is proficient at, so "
            "no explanation here may claim them."
        )


def _explain(key: MetricKey, template: str, **figures: object) -> str:
    """Render an explanation sentence and check it against the two rules.

    Args:
        key: The metric key, named in the error.
        template: A format string with one placeholder per figure.
        **figures: The values to substitute.

    Returns:
        The rendered sentence, already checked.

    Raises:
        ValueError: If the rendered sentence breaks either rule. Checked here as
            well as in the dataclass so the error names the sentence rather than
            arriving as a constructor failure three frames away.
    """
    rendered = template.format(**figures)
    _check_explanation(key.value, rendered)
    return rendered


def _span_days(window_start: datetime, window_end: datetime) -> int:
    """Whole days in ``[window_start, window_end)``, at least 1.

    One rather than zero for a zero-length window, because several metrics here
    divide by or describe this number. Clamping up to 1 keeps every explanation
    true and keeps the ratios computable.
    """
    delta = _as_utc(window_end) - _as_utc(window_start)
    return max(1, int(delta.total_seconds() // 86400))


def _in_window(sample: ActivitySample, window_start: datetime, window_end: datetime) -> bool:
    """Whether a sample falls in ``[window_start, window_end)``.

    Half-open at the top, because a boundary that belonged to two buckets would
    double-count it: a session at exactly midnight would be counted by the day that
    ended and the day that began.
    """
    instant = _as_utc(sample.occurred_at)
    return _as_utc(window_start) <= instant < _as_utc(window_end)


def _selected(
    activities: Sequence[ActivitySample], window_start: datetime, window_end: datetime
) -> list[ActivitySample]:
    """The activities inside the window, in the order they were given.

    Not sorted: every metric here is order-independent and sorting would imply an
    ordering guarantee the callers do not get. What the window does guarantee is
    that the *same* rows are selected for every metric in one response, which is
    what makes the eight numbers comparable to each other.
    """
    return [activity for activity in activities if _in_window(activity, window_start, window_end)]


def _metric(
    key: MetricKey,
    *,
    label: str,
    value: float,
    definition: str,
    explanation: str,
    window_days: int,
    source: str,
) -> LearningMetric:
    """Assemble one measured metric from the shared constants.

    Every measured metric goes through here rather than constructing its own
    :class:`LearningMetric`, so the unit comes from :data:`METRIC_UNITS` and a
    metric cannot invent a unit the frontend has no formatter for. The declined
    case is separate, below, because its ``value`` carries no meaning.
    """
    return LearningMetric(
        key=key.value,
        label=label,
        value=value,
        unit=METRIC_UNITS[key],
        definition=definition,
        window_days=window_days,
        source=source,
        explanation=explanation,
        available=True,
    )


def _declined(
    key: MetricKey,
    *,
    label: str,
    definition: str,
    window_days: int,
    source: str,
    reason: str = NOT_ENOUGH_DATA,
) -> LearningMetric:
    """The one shape every "I cannot measure this" answer takes.

    ``value`` is ``0.0`` together with ``available=False``, and the pairing is
    deliberate rather than lazy. :class:`LearningMetric` requires a float, so an
    unavailable metric carries a zero it does not mean, and ``available=False`` is
    what tells every reader — and the wire layer — to drop it.

    The explanation is still figure-bearing on purpose. "Not enough data" alone
    would leave the reader unable to tell which figure was missing, so the sentence
    names what was read and the emptiness of it alongside the reason — and it
    names the **0** that the reading found rather than the ``None`` window, which
    is the whole difference between declining and claiming.

    Args:
        key: The metric key.
        label: Human name for the card.
        definition: One sentence saying how it *would* be computed.
        window_days: The window the attempt covered, or ``None`` for whole history.
        source: Which facts it would have read.
        reason: Why it could not be computed.

    Returns:
        An unavailable metric.
    """
    scope = f"the {window_days}-day window" if window_days is not None else "this account's goals"
    return LearningMetric(
        key=key.value,
        label=label,
        value=0.0,
        unit=METRIC_UNITS[key],
        definition=definition,
        window_days=window_days,
        source=source,
        explanation=_explain(
            key,
            "{reason} The {label} figure is not computed: 0 records in {scope} supply "
            "the numbers it would be divided over.",
            reason=reason,
            label=label,
            scope=scope,
        ),
        available=False,
        reason_if_unavailable=reason,
    )


# ---------------------------------------------------------------------------
# The eight metrics
# ---------------------------------------------------------------------------


def sessions_last_7d(
    activities: Sequence[ActivitySample], *, window_end: datetime
) -> LearningMetric:
    """How many learning sessions were recorded in the last seven days.

    **A session is a recorded row**, not a row with a duration. "I completed the
    chapter" and "I read for forty minutes" are both evidence that something
    happened here, and counting only the second would quietly drop every task and
    note the user recorded.

    The window is anchored on ``window_end`` rather than on the request's window,
    for the same reason :func:`app.services.developer.metrics.recent_momentum` is:
    "have I been at this lately" is a trailing question and should not change
    answer because the caller asked for a different range. ``window_end`` *is*
    "now" — this module reads no clock, so the service takes that instant from the
    database.

    Always available: no sessions in a week is a *measured* zero.

    Args:
        activities: Every recorded activity the caller has.
        window_end: The instant the trailing week ends, exclusive.

    Returns:
        The metric.
    """
    end = _as_utc(window_end)
    start = end - timedelta(days=RECENT_WEEK_DAYS)
    count = len(_selected(activities, start, end))
    return _metric(
        MetricKey.SESSIONS_LAST_7D,
        label="Sessions (7 days)",
        value=float(count),
        definition=f"Recorded learning activities in the {RECENT_WEEK_DAYS} days to today.",
        explanation=_explain(
            MetricKey.SESSIONS_LAST_7D,
            "{count} learning session(s) were recorded in the last {span} days.",
            count=count,
            span=RECENT_WEEK_DAYS,
        ),
        window_days=RECENT_WEEK_DAYS,
        source="learning_activities.occurred_at",
    )


def sessions_last_30d(
    activities: Sequence[ActivitySample], *, window_end: datetime
) -> LearningMetric:
    """How many learning sessions were recorded in the last thirty days.

    The same count as :func:`sessions_last_7d` over a longer window, and named for
    a length rather than for "the request's window" because the feature export
    fixes the column name. On a default request — a thirty-day window ending at
    ``window_end`` — this and :func:`learning_minutes`,
    :func:`learning_consistency` and :func:`skill_activity_frequency` all cover
    exactly the same range, and each metric reports its own ``window_days`` so a
    caller that asks for something else can say so on the card.

    Always available.

    Args:
        activities: Every recorded activity the caller has.
        window_end: The instant the trailing month ends, exclusive.

    Returns:
        The metric.
    """
    end = _as_utc(window_end)
    start = end - timedelta(days=RECENT_MONTH_DAYS)
    count = len(_selected(activities, start, end))
    return _metric(
        MetricKey.SESSIONS_LAST_30D,
        label="Sessions (30 days)",
        value=float(count),
        definition=f"Recorded learning activities in the {RECENT_MONTH_DAYS} days to today.",
        explanation=_explain(
            MetricKey.SESSIONS_LAST_30D,
            "{count} learning session(s) were recorded in the last {span} days.",
            count=count,
            span=RECENT_MONTH_DAYS,
        ),
        window_days=RECENT_MONTH_DAYS,
        source="learning_activities.occurred_at",
    )


def learning_minutes(
    activities: Sequence[ActivitySample], *, window_start: datetime, window_end: datetime
) -> LearningMetric:
    """Total minutes recorded against learning activities in the window.

    Sums only the durations that were recorded, and reports how many rows carried
    one. ``duration_minutes`` is nullable because an *event* is not a *span*, and
    an event contributes nothing here without being counted as a measured
    zero-length session — which is the whole point of carrying the ``timed`` count
    in the sentence.

    **What this is not.** It is minutes the user chose to record. It is not a
    measure of how long anyone studied: someone who forgets to start the timer, or
    works in a tab that was never given a duration, has a smaller number, and the
    explanation names the sample it was taken from so the number can be read at its
    actual size.

    Always available: no recorded durations is a measured zero, reported as one.

    Args:
        activities: Every recorded activity the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(activities, window_start, window_end)
    span = _span_days(window_start, window_end)
    durations = [
        activity.duration_minutes
        for activity in windowed
        if activity.duration_minutes is not None and activity.duration_minutes > 0
    ]
    minutes = sum(durations)
    return _metric(
        MetricKey.LEARNING_MINUTES,
        label="Learning minutes",
        value=float(minutes),
        definition=(
            "Sum of the durations recorded on learning activities in the window. An "
            "activity with no recorded duration contributes nothing and is not treated "
            "as a zero-length session."
        ),
        explanation=_explain(
            MetricKey.LEARNING_MINUTES,
            "{minutes} minute(s) were recorded, summed from {timed} of the {total} "
            "learning activit(y/ies) in the {span}-day window that carry a duration.",
            minutes=minutes,
            timed=len(durations),
            total=len(windowed),
            span=span,
        ),
        window_days=span,
        source="learning_activities.duration_minutes",
    )


def goal_progress(goals: Sequence[GoalSample]) -> LearningMetric:
    """The mean of the user's own progress figures across their open goals.

    A mean of a set the user filled in, and nothing more. It is emphatically **not**
    a measure of how much someone has learned: the numbers being averaged are the
    percentages the user themselves set, and an account that sets them generously
    and one that sets them conservatively produce different numbers for identical
    histories. The definition says "user-set" for that reason.

    Open goals only — ``COMPLETED`` and ``ARCHIVED`` are excluded, the first
    because a finished goal's 100% is not a data point about the ones still going,
    the second because an archived goal must not count as incomplete work anywhere
    (see :data:`OPEN_GOAL_STATUSES`).

    Declines when there are no open goals: the mean of an empty set has no answer
    that is not invented, and "0% progress" for someone who has written no goals is
    a claim about nobody.

    Args:
        goals: Every goal the caller has.

    Returns:
        The metric, or an unavailable one when no goal is open.
    """
    open_goals = [goal for goal in goals if goal.status in OPEN_GOAL_STATUSES]
    if not open_goals:
        return _declined(
            MetricKey.GOAL_PROGRESS,
            label="Goal progress",
            definition="Mean of the user-set progress percentage across open goals.",
            window_days=None,
            source="learning_goals.progress, learning_goals.status",
        )

    mean = sum(goal.progress for goal in open_goals) / len(open_goals)
    return _metric(
        MetricKey.GOAL_PROGRESS,
        label="Goal progress",
        value=round(mean, 4),
        definition="Mean of the user-set progress percentage across open goals.",
        explanation=_explain(
            MetricKey.GOAL_PROGRESS,
            "{count} open goal(s) carry a user-set progress figure, averaging "
            "{percentage}% of the way to their targets.",
            count=len(open_goals),
            percentage=round(mean),
        ),
        window_days=None,
        source="learning_goals.progress, learning_goals.status",
    )


def goal_deadline_distance_days(
    goals: Sequence[GoalSample], *, window_end: datetime
) -> LearningMetric:
    """How far the nearest open goal's deadline sits from today, averaged.

    Signed, and the sign is the whole point: a negative figure is a goal whose own
    date has passed, which is a fact the user can see in their own row and a
    different answer from "no date set". Averaging the open goals that carry a
    date puts "4 days away" and "9 days overdue" on the same card as ``-2.5``,
    which reads correctly once the sentence says a negative figure means the date
    has passed.

    ``window_end`` *is* "today" — this module reads no clock, so the service takes
    the instant from the database rather than from the application machine.

    Declines when no open goal carries a date: distance to nothing is not short.

    Args:
        goals: Every goal the caller has.
        window_end: The instant treated as today.

    Returns:
        The metric, or an unavailable one when no open goal carries a date.
    """
    open_goals = [goal for goal in goals if goal.status in OPEN_GOAL_STATUSES]
    dated = [goal for goal in open_goals if goal.target_date is not None]
    if not dated:
        return _declined(
            MetricKey.GOAL_DEADLINE_DISTANCE_DAYS,
            label="Deadline distance",
            definition=(
                "Mean days from today to the target date of open goals that carry one; "
                "negative when the date has passed."
            ),
            window_days=None,
            source="learning_goals.target_date, learning_goals.status",
        )

    today = _as_utc(window_end).date()
    distances = [(goal.target_date - today).days for goal in dated]
    mean = sum(distances) / len(distances)
    return _metric(
        MetricKey.GOAL_DEADLINE_DISTANCE_DAYS,
        label="Deadline distance",
        value=round(mean, 4),
        definition=(
            "Mean days from today to the target date of open goals that carry one; "
            "negative when the date has passed."
        ),
        explanation=_explain(
            MetricKey.GOAL_DEADLINE_DISTANCE_DAYS,
            "{count} open goal(s) carry a target date, {days} day(s) from today on "
            "average; a negative figure means the date has passed.",
            count=len(dated),
            days=round(mean, 1),
        ),
        window_days=None,
        source="learning_goals.target_date, learning_goals.status",
    )


def completion_rate(goals: Sequence[GoalSample]) -> LearningMetric:
    """Completed goals as a share of the goals that were not archived.

    A ratio rather than a percentage because the value is used in arithmetic by a
    later phase, and a metric whose unit changed with its caller is a metric that
    cannot be summed.

    Archived goals are excluded from **both** sides of the fraction, and that is
    the only interesting decision here. Counting an archived goal as incomplete
    would make discarding a goal look like failing to finish it — which is exactly
    what :class:`~app.models.enums.LearningGoalStatus` separates the two states in
    order to prevent.

    Declines when there is nothing to divide: ``0 / 0`` is a number and a mistake.
    A new account that has written no goals has not completed 0% of them; it has a
    question that was never asked.

    Args:
        goals: Every goal the caller has.

    Returns:
        The metric, or an unavailable one when there are no non-archived goals.
    """
    counted = [goal for goal in goals if goal.status is not LearningGoalStatus.ARCHIVED]
    if not counted:
        return _declined(
            MetricKey.COMPLETION_RATE,
            label="Completion rate",
            definition="Completed goals as a share of the goals that were not archived.",
            window_days=None,
            source="learning_goals.status",
        )

    completed = sum(1 for goal in counted if goal.status is LearningGoalStatus.COMPLETED)
    ratio = completed / len(counted)
    return _metric(
        MetricKey.COMPLETION_RATE,
        label="Completion rate",
        value=round(ratio, 4),
        definition="Completed goals as a share of the goals that were not archived.",
        explanation=_explain(
            MetricKey.COMPLETION_RATE,
            "{completed} of the {total} non-archived goal(s) are recorded as completed, "
            "which is {percentage}% of them.",
            completed=completed,
            total=len(counted),
            percentage=round(ratio * 100),
        ),
        window_days=None,
        source="learning_goals.status",
    )


def learning_consistency(
    activities: Sequence[ActivitySample], *, window_start: datetime, window_end: datetime
) -> LearningMetric:
    """What share of the window's days carried a recorded learning activity.

    ``active days / window days``, clamped to 0-1, counted over distinct UTC
    dates. One session on a day counts once however many.

    **What this is not.** It is a count of days with a recorded row over a count of
    days in the window. It is not a claim about habit, discipline, reliability or
    effort — nothing in ``learning_activities`` records whether the person was
    focused, only whether they recorded something — and the explanation says
    exactly that much: an empty window reads "0 of the 30 days", which is a fact
    about the record and nothing about the person.

    Always available.

    Args:
        activities: Every recorded activity the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(activities, window_start, window_end)
    span = _span_days(window_start, window_end)
    count = len({_as_utc(activity.occurred_at).date() for activity in windowed})
    ratio = min(1.0, max(0.0, count / span))
    return _metric(
        MetricKey.LEARNING_CONSISTENCY,
        label="Learning consistency",
        value=round(ratio, 4),
        definition=(
            "Distinct UTC calendar dates carrying at least one recorded activity in the "
            "window, divided by the days in the window, as a ratio between 0 and 1."
        ),
        explanation=_explain(
            MetricKey.LEARNING_CONSISTENCY,
            "{count} of the {span} days in the window carried a recorded learning "
            "activity, which is {percentage}% of them.",
            count=count,
            span=span,
            percentage=round(ratio * 100),
        ),
        window_days=span,
        source="learning_activities.occurred_at",
    )


def skill_activity_frequency(
    activities: Sequence[ActivitySample], *, window_start: datetime, window_end: datetime
) -> LearningMetric:
    """Recorded activities per named skill, over the skills that had any.

    Both sides count only activities that name a skill. An activity with no
    ``skill_id`` — a study session recorded before the skill existed — is real
    evidence of learning and is counted by every other metric here, but it belongs
    to no skill's frequency, and dividing by a per-skill denominator it does not
    appear in would inflate every skill that does have one.

    Declines when no activity in the window names a skill, because the average is
    then per-nothing. It does **not** decline on an empty window: zero activities
    across zero skills is measured, but there is nothing to divide, so the two
    cases are handled by the same branch and only the second is reported, with the
    count in the sentence saying which it was.

    Args:
        activities: Every recorded activity the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric, or an unavailable one when no activity names a skill.
    """
    windowed = _selected(activities, window_start, window_end)
    span = _span_days(window_start, window_end)
    tagged = [activity for activity in windowed if activity.skill_id is not None]
    skills = {activity.skill_id for activity in tagged}
    if not skills:
        return _declined(
            MetricKey.SKILL_ACTIVITY_FREQUENCY,
            label="Skill activity frequency",
            definition=(
                "Recorded activities naming a skill, divided by the distinct skills they "
                "named, inside the window."
            ),
            window_days=span,
            source="learning_activities.skill_id",
        )

    average = len(tagged) / len(skills)
    return _metric(
        MetricKey.SKILL_ACTIVITY_FREQUENCY,
        label="Skill activity frequency",
        value=round(average, 4),
        definition=(
            "Recorded activities naming a skill, divided by the distinct skills they "
            "named, inside the window."
        ),
        explanation=_explain(
            MetricKey.SKILL_ACTIVITY_FREQUENCY,
            "{count} skill-tagged activit(y/ies) were recorded across {skills} skill(s) "
            "in the {span}-day window, an average of {average} per skill.",
            count=len(tagged),
            skills=len(skills),
            span=span,
            average=round(average, 2),
        ),
        window_days=span,
        source="learning_activities.skill_id",
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_metrics(
    activities: Sequence[ActivitySample],
    goals: Sequence[GoalSample],
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[LearningMetric, ...]:
    """Compute all eight metrics over one window, in contract order.

    One function so a caller cannot assemble a response from a mixture of two
    different windows, which would produce cards whose figures disagree with each
    other and nothing on the page to say why. Every metric receives the same
    ``activities``, the same ``goals`` and the same boundaries.

    ``window_end`` doubles as "now": the trailing seven- and thirty-day windows are
    measured back from it, and :func:`goal_deadline_distance_days` treats it as
    today. The service takes that instant from the database, so two application
    servers cannot disagree about which day a session landed on.

    Args:
        activities: Every recorded activity the caller has.
        goals: Every goal the caller has.
        window_start: Inclusive start of the request's window.
        window_end: Exclusive end of the request's window, and "now".

    Returns:
        Eight metrics, in :data:`LEARNING_METRICS` order. Exactly eight always — a
        metric that cannot be computed comes back unavailable rather than omitted,
        because a client that indexes by key would otherwise render a hole where a
        card belongs.
    """
    return (
        sessions_last_7d(activities, window_end=window_end),
        sessions_last_30d(activities, window_end=window_end),
        learning_minutes(activities, window_start=window_start, window_end=window_end),
        goal_progress(goals),
        goal_deadline_distance_days(goals, window_end=window_end),
        completion_rate(goals),
        learning_consistency(activities, window_start=window_start, window_end=window_end),
        skill_activity_frequency(activities, window_start=window_start, window_end=window_end),
    )


# ---------------------------------------------------------------------------
# Activity series
# ---------------------------------------------------------------------------


def _floor(value: datetime, granularity: ActivityGranularity) -> datetime:
    """The UTC start of the bucket ``value`` falls in.

    Weeks start on Monday, because that is the ISO convention and because a week
    starting on Sunday makes "last week" mean two different things depending on who
    is reading it. Months floor to the first of the month. The result is always
    midnight UTC, so two buckets are exactly adjacent and a session at 00:00:00
    belongs to exactly one of them.
    """
    moment = _as_utc(value)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity is ActivityGranularity.DAY:
        return midnight
    if granularity is ActivityGranularity.WEEK:
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


def _advance(value: datetime, granularity: ActivityGranularity) -> datetime:
    """The start of the bucket after the one starting at ``value``.

    Months step by adding 32 days and re-flooring rather than by arithmetic on the
    month number: a month with 31 days would make ``day + 1`` overflow, and a naive
    "same day next month" would skip February entirely.
    """
    if granularity is ActivityGranularity.DAY:
        return value + timedelta(days=1)
    if granularity is ActivityGranularity.WEEK:
        return value + timedelta(days=7)
    return _floor(value + timedelta(days=32), ActivityGranularity.MONTH)


def _bucket_label(value: datetime, granularity: ActivityGranularity) -> str:
    """A stable, sortable label for one bucket.

    ISO dates throughout (``2026-07-01`` for a day and for the Monday starting a
    week, ``2026-07`` for a month). Formatted rather than derived from a locale,
    because a label a client re-parses is a label that breaks when the server's
    locale does.
    """
    if granularity is ActivityGranularity.MONTH:
        return value.strftime("%Y-%m")
    return value.strftime("%Y-%m-%d")


def _parse_granularity(granularity: str) -> ActivityGranularity:
    """Coerce a caller's granularity string, refusing anything unknown.

    Args:
        granularity: The requested bucket size.

    Returns:
        The matching member.

    Raises:
        ValueError: If it is not one of the three known values. An unknown
            granularity is a request the caller can be told is wrong; guessing one
            would silently return a chart nobody asked for.
    """
    try:
        return ActivityGranularity(granularity)
    except ValueError as exc:
        raise ValueError(
            f"{granularity!r} is not a known activity granularity. Use one of "
            f"{', '.join(ACTIVITY_GRANULARITIES)}."
        ) from exc


def activity_series(
    activities: Sequence[ActivitySample],
    *,
    window_start: datetime,
    window_end: datetime,
    granularity: str = ActivityGranularity.DAY.value,
    skill_id: uuid.UUID | None = None,
    goal_id: uuid.UUID | None = None,
) -> LearningActivitySeries:
    """Bucket learning activities across a range, zero-filling every gap.

    **The zero-fill is the point of this function.** A chart built only from the
    buckets that have sessions skips the quiet ones, and a reader counting the bars
    sees five consecutive active days when the truth is five active days with gaps
    between them. So every bucket from the first to the last is emitted, including
    the empty ones, and the caller cannot accidentally omit them.

    Buckets are aligned to the calendar, not to the window's start. A window
    opening mid-week gets that week's Monday as its first bucket rather than a
    partial day that would sit beside a full one on the same chart and read as a
    real dip.

    Args:
        activities: Every recorded activity the caller has.
        window_start: Inclusive start of the range.
        window_end: Exclusive end of the range.
        granularity: ``day``, ``week`` or ``month``.
        skill_id: Restrict to one skill, or ``None`` for all of them.
        goal_id: Restrict to one goal, or ``None`` for all of them.

    Returns:
        The dense series.

    Raises:
        ValueError: If ``granularity`` is not one of the three known values, or the
            window does not end after it starts. Both are facts about the request
            rather than about the data, and both are better caught here than
            rendered as an empty chart.
    """
    step = _parse_granularity(granularity)
    start = _as_utc(window_start)
    end = _as_utc(window_end)
    if end <= start:
        raise ValueError(
            "The activity window must end after it starts; got "
            f"{start.isoformat()} to {end.isoformat()}."
        )

    grouped: dict[datetime, list[ActivitySample]] = defaultdict(list)
    for activity in activities:
        if skill_id is not None and activity.skill_id != skill_id:
            continue
        if goal_id is not None and activity.goal_id != goal_id:
            continue
        if _in_window(activity, start, end):
            grouped[_floor(activity.occurred_at, step)].append(activity)

    buckets: list[LearningBucket] = []
    cursor = _floor(start, step)
    while cursor < end:
        inside = grouped.get(cursor, [])
        buckets.append(
            LearningBucket(
                start=cursor,
                label=_bucket_label(cursor, step),
                activity_count=len(inside),
                session_minutes=sum(
                    activity.duration_minutes
                    for activity in inside
                    if activity.duration_minutes is not None and activity.duration_minutes > 0
                ),
                skill_ids=frozenset(
                    activity.skill_id for activity in inside if activity.skill_id is not None
                ),
                goal_ids=frozenset(
                    activity.goal_id for activity in inside if activity.goal_id is not None
                ),
            )
        )
        cursor = _advance(cursor, step)

    return LearningActivitySeries(
        granularity=step.value,
        start=_floor(start, step),
        end=end,
        buckets=tuple(buckets),
    )


def bucket_for(value: datetime, granularity: str = ActivityGranularity.DAY.value) -> str:
    """The label of the bucket ``value`` falls in.

    Exposed on its own so the service can bucket a *query* the same way the series
    buckets its rows. A caller grouping its SQL by a different definition of "a
    day" would produce a heatmap that disagrees with the line chart above it, and
    nothing on the page would say which one is right.

    Args:
        value: The instant to bucket.
        granularity: ``day``, ``week`` or ``month``.

    Returns:
        The bucket's label, in the format :func:`activity_series` emits.

    Raises:
        ValueError: If ``granularity`` is not a known value.
    """
    step = _parse_granularity(granularity)
    return _bucket_label(_floor(value, step), step)
