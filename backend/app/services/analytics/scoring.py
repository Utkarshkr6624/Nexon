"""The scoring functions. Pure: no database, no clock, no I/O, no FastAPI.

Every function here takes plain numbers and returns a :class:`ScoreResult` (or,
for estimation, an :class:`EstimationResult`) carrying the score *and the
arithmetic that produced it*. That is the whole design of this module, and the
brief's rule is why:

    "Not enough activity yet"     <- correct
    "0% productivity"             <- wrong

A score is either computable from real inputs, or it is unavailable with a
stated reason. It is never ``0`` for want of data, because a user with two
completed tasks who is told their deadline adherence is 0% has been told a lie
about themselves by a system that had nothing to measure.

The three ideas
---------------

**Unavailability is a first-class answer.** :attr:`ScoreResult.available` is
``False`` whenever the inputs that would carry the signal are missing, and
``reason_if_unavailable`` says which. When only *some* components are missing,
the score is still available — a user with a completion history and no clock
history has a real completion rate — but the missing component contributes
**zero points and an explanation saying why**, so the breakdown can never be
mistaken for a measurement.

**Nothing is invented.** A percentage change from a previous value of zero is
``None`` (:func:`percent_change`), not infinity; a median of nothing is
``None`` (:func:`median`), not zero. The caller renders "no previous period to
compare with", which is what is true.

**Weights are injected, not embedded.** :func:`productivity_score` takes a
``weights`` mapping defaulting to :data:`DEFAULT_WEIGHTS`, and
``AnalyticsService`` passes ``settings.analytics_productivity_weights`` — the
configured numbers. A weight therefore lives in exactly one place, and
``Settings`` refuses to construct at all if the four do not total 100.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median as _statistics_median

__all__ = [
    "DEFAULT_WEIGHTS",
    "NOT_ENOUGH_ACTIVITY",
    "EstimationResult",
    "ScoreComponent",
    "ScoreResult",
    "absolute_change",
    "clamp_percentage",
    "consistency_score",
    "deadline_adherence",
    "estimation_accuracy",
    "focus_score",
    "median",
    "percent_change",
    "productivity_score",
    "rate",
]

#: The productivity weights used when a caller does not supply them.
#:
#: These mirror ``Settings.analytics_productivity_weight_*`` and exist only so
#: that :func:`productivity_score` is callable with nothing but its inputs — a
#: unit test, a script. The application always passes the configured mapping;
#: nothing in the service layer reads this constant.
DEFAULT_WEIGHTS: dict[str, float] = {
    "completion": 30.0,
    "deadline": 25.0,
    "consistency": 20.0,
    "focus": 25.0,
}

#: What every unavailable score says. One string, not one per function: the
#: user-facing phrase is a product decision and belongs in one place.
NOT_ENOUGH_ACTIVITY = "Not enough activity yet"

#: A session at least this long is treated as "uninterrupted" for the focus
#: score. 45 minutes is the shortest block that is usually a unit of work rather
#: than a context switch, and it is stated here rather than buried in a formula.
FOCUS_TARGET_MINUTES = 45.0

#: Share of the focus score earned by sustained session length, the remainder by
#: following a plan through to completion. Stated as named constants because a
#: focus score with an unexplained split is the exact thing the brief forbids.
FOCUS_LENGTH_SHARE = 0.6
FOCUS_FOLLOWTHROUGH_SHARE = 0.4


# -- Result carriers --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One row of a score's breakdown: what it earned, out of what, and why."""

    name: str
    points: float
    max_points: float
    explanation: str


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """A score, its breakdown, and whether there was enough data to score.

    ``score`` is ``0`` only when a real measurement earned it. When
    ``available`` is ``False`` the score is ``None`` — there is no number to
    show, and the API says so rather than showing a zero.
    """

    score: int | None
    components: list[ScoreComponent] = field(default_factory=list)
    available: bool = True
    reason_if_unavailable: str | None = None


@dataclass(frozen=True, slots=True)
class EstimationResult:
    """How close ``estimated_minutes`` has been to ``actual_minutes``.

    Every field is a measurement of the pairs supplied. ``available`` is
    ``False`` — with ``reason_if_unavailable`` set — when there were no usable
    pairs at all, which is the case for a user who has never estimated anything.
    """

    available: bool = True
    reason_if_unavailable: str | None = None
    #: The signed-error breakdown, same shape as :attr:`ScoreResult.components`,
    #: so an estimation figure is explained the same way a score is. Empty when
    #: there were no pairs to break down.
    components: list[ScoreComponent] = field(default_factory=list)
    sample_count: int = 0
    #: Mean of ``abs(actual - estimated)``, in minutes.
    absolute_error: float | None = None
    #: Mean of ``abs(actual - estimated) / estimated * 100``, over the pairs
    #: whose estimate is greater than zero. ``None`` rather than zero when there
    #: is nothing to divide.
    percentage_error: float | None = None
    #: Mean of the **signed** error, ``estimated - actual``. Negative means the
    #: estimates habitually ran *below* the time actually taken — you
    #: under-estimate. Same sign as :attr:`under_estimation_rate`; see the
    #: sign note on :func:`estimation_accuracy`.
    bias: float | None = None
    #: Median of ``abs(actual - estimated)``. The median is beside the mean
    #: because one 40-minute miss on a 5-minute task should not describe the
    #: other forty-nine pairs.
    median_error: float | None = None
    #: Share of pairs where the work took *more* time than estimated.
    under_estimation_rate: float | None = None
    #: Share of pairs where the work took *less* time than estimated.
    over_estimation_rate: float | None = None


# -- Small numeric helpers --------------------------------------------------


def clamp_percentage(value: float | int | None) -> float | None:
    """Clamp to ``0.0-1.0``, preserving ``None``.

    ``None`` is a real input here: "no rate exists" must survive clamping, or
    the clamp would invent a 0% for it.
    """
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def absolute_change(current: float | int, previous: float | int) -> float:
    """Signed difference between two figures. Always defined, including at zero."""
    return round(float(current) - float(previous), 4)


def percent_change(current: float | int, previous: float | int) -> float | None:
    """Percentage change from ``previous`` to ``current``, or ``None``.

    **``None`` when ``previous`` is zero**, and that is the entire reason this
    function exists. ``(current - previous) / previous`` at ``previous == 0`` is
    ``Infinity`` for a positive current and ``NaN`` for a negative one, both of
    which serialise into a JSON body as ``null`` or ``NaN`` and into a rendered
    page as the literal text ``Infinity%``. The brief forbids all three. There is
    no honest percentage change from nothing to something, so the answer is that
    there isn't one.

    A negative ``previous`` also yields ``None``: the percentage change from a
    negative base has no readable meaning, and pretending otherwise produces a
    sign-flipped number that is worse than no number.
    """
    base = float(previous)
    if base == 0 or base < 0:
        return None
    return round((float(current) - base) / base * 100.0, 4)


def median(values: Sequence[float | int]) -> float | None:
    """The median of ``values``, or ``None`` when it is empty.

    ``None`` rather than ``0``: the median of nothing is unknown, and 0 is a
    claim that the middle value of an empty set is zero.
    """
    if not values:
        return None
    return round(float(_statistics_median(float(value) for value in values)), 4)


def rate(numerator: float | int, denominator: float | int) -> float | None:
    """``numerator / denominator`` as a percentage, or ``None`` when undefined.

    The only place a division happens, so the "empty denominator is unknown"
    rule is applied once rather than at a dozen call sites that each have to
    remember it.
    """
    if not denominator:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 4)


# -- Scores -----------------------------------------------------------------


def _as_rate(value: ScoreResult | float | int | None) -> float | None:
    """Coerce a component input to a 0-1 rate.

    Accepts a nested :class:`ScoreResult` (its own score, rescaled to 0-1),
    a bare rate, or ``None``. The nested form is what lets the four sub-scores
    be combined without each having to re-divide by 100 at the call site.
    """
    if value is None:
        return None
    if isinstance(value, ScoreResult):
        if not value.available or value.score is None:
            return None
        return clamp_percentage(value.score / 100.0)
    return clamp_percentage(value)


def _describe(name: str, value: ScoreResult | float | int | None) -> str:
    """The sentence a component shows when it earned nothing."""
    if isinstance(value, ScoreResult):
        return value.reason_if_unavailable or NOT_ENOUGH_ACTIVITY
    return f"No {name} data recorded for this period."


def productivity_score(
    *,
    completion_rate: ScoreResult | float | int | None,
    deadline_adherence: ScoreResult | float | int | None,
    consistency: ScoreResult | float | int | None,
    focus: ScoreResult | float | int | None,
    weights: dict[str, float] | None = None,
) -> ScoreResult:
    """The NEXUS Productivity Score: a weighted blend of four 0-1 rates.

    Formula, in full::

        score = round( sum over components of ( weight[component] * rate[component] ) )
        clamped to 0..100

    with ``rate[component]`` in ``0..1`` and the four weights summing to 100 —
    which ``Settings`` enforces at construction, so the score's denominator is
    always the 100 the user is shown.

    Each component reports ``weight * rate`` points out of ``weight``, which is
    what makes the breakdown add up to the score by construction rather than by
    coincidence.

    **This is a NEXUS-derived metric and nothing else.** It is not a validated
    measure of human performance, and the API labels it as such. It is a
    transparent, reproducible combination of four things the system recorded.

    **Availability.** ``available`` is ``False`` only when *none* of the four
    components could be measured — there is then no score at all and
    ``reason_if_unavailable`` names what is missing. When some components are
    missing the score is still produced: the measured ones count for their full
    weight and the unmeasured ones contribute zero points, with an explanation
    on each so the breakdown cannot be read as "you scored 0 at deadlines".
    """
    resolved = dict(DEFAULT_WEIGHTS if weights is None else weights)
    inputs = {
        "completion": completion_rate,
        "deadline": deadline_adherence,
        "consistency": consistency,
        "focus": focus,
    }

    components: list[ScoreComponent] = []
    total = 0.0
    measured = 0
    for key, raw in inputs.items():
        weight = float(resolved.get(key, 0.0))
        value = _as_rate(raw)
        if value is None:
            components.append(
                ScoreComponent(
                    name=key,
                    points=0.0,
                    max_points=weight,
                    explanation=f"Not counted: {_describe(key, raw).lower()}",
                )
            )
            continue
        measured += 1
        points = round(weight * value, 4)
        total += points
        components.append(
            ScoreComponent(
                name=key,
                points=points,
                max_points=weight,
                explanation=f"{value * 100:.1f}% of the {key} target ({weight:.0f} points available).",
            )
        )

    if measured == 0:
        return ScoreResult(
            score=None,
            components=components,
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no task completions, deadlines, active days "
                "or work sessions were recorded in this period, so no component of "
                "the score could be measured."
            ),
        )
    return ScoreResult(score=max(0, min(100, round(total))), components=components)


def consistency_score(*, active_days: int, window_days: int, session_count: int) -> ScoreResult:
    """How regularly the user showed up, over a window.

    Formula: ``score = round(100 * active_days / window_days)`` — the share of
    the period's days on which at least one work session was recorded.

    **Unavailable when ``session_count`` is zero.** A user who worked but never
    started a timer has no recorded evidence of working, and scoring them 0%
    consistent for that would be measuring the product, not the person. The
    reason string says so in those words.

    Two caveats stated rather than hidden: a day with a one-minute session counts
    as a day like any other, and this measures *presence*, not output — a user
    who logs in every day and does nothing scores 100. The productivity score is
    the one that combines presence with results; this is deliberately only the
    first of the two.

    **NEXUS Consistency Score**, derived from recorded work-session data.
    """
    if session_count <= 0:
        return ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no work sessions were started in this period, "
                "so there is no record of which days were active."
            ),
        )
    if window_days <= 0:
        return ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=f"{NOT_ENOUGH_ACTIVITY}: the period is empty.",
        )
    value = clamp_percentage(active_days / window_days) or 0.0
    points = round(value * 100.0, 4)
    return ScoreResult(
        score=max(0, min(100, round(points))),
        components=[
            ScoreComponent(
                name="active_days",
                points=points,
                max_points=100.0,
                explanation=(
                    f"{active_days} of {window_days} days had at least one work "
                    f"session ({session_count} session(s) recorded)."
                ),
            )
        ],
    )


def focus_score(
    *, avg_session_minutes: float | None, completed_planned_sessions: int, interruptions: int
) -> ScoreResult:
    """The NEXUS Focus Score, derived from recorded work-session behaviour.

    Formula::

        length        = min(avg_session_minutes / 45, 1)
        followthrough = completed_planned_sessions / (completed_planned_sessions + interruptions)
        score         = round(100 * (0.6 * length + 0.4 * followthrough))

    45 minutes is the target a single uninterrupted block is measured against
    (``FOCUS_TARGET_MINUTES``); longer does not score better, because this is not
    a measure of stamina.

    ``interruptions`` is the count of *reschedules* in the period — the only
    interruption signal Phase 1-5 records. It is a weak proxy and the explanation
    says "reschedules", not "interruptions", so nobody reads it as a
    concentration measurement. **This score does not measure human attention.**

    **Unavailable when there are no completed planned sessions**, because a
    follow-through rate needs a denominator and an average session needs a
    session. A user who has run no timers gets "Not enough activity yet", never
    a low number.
    """
    if completed_planned_sessions <= 0:
        return ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no planned work session was started and "
                "finished in this period, so there is nothing to measure focus from."
            ),
        )

    length = (
        None
        if avg_session_minutes is None
        else clamp_percentage(avg_session_minutes / FOCUS_TARGET_MINUTES)
    )
    followthrough = completed_planned_sessions / (completed_planned_sessions + interruptions)

    components: list[ScoreComponent] = []
    total = 0.0
    if length is None:
        components.append(
            ScoreComponent(
                name="session_length",
                points=0.0,
                max_points=round(FOCUS_LENGTH_SHARE * 100.0, 4),
                explanation="Not counted: no completed session recorded a duration.",
            )
        )
    else:
        points = round(FOCUS_LENGTH_SHARE * 100.0 * length, 4)
        total += points
        components.append(
            ScoreComponent(
                name="session_length",
                points=points,
                max_points=round(FOCUS_LENGTH_SHARE * 100.0, 4),
                explanation=(
                    f"Average session {avg_session_minutes:.0f} min against a "
                    f"{FOCUS_TARGET_MINUTES:.0f} min uninterrupted-block target."
                ),
            )
        )

    points = round(FOCUS_FOLLOWTHROUGH_SHARE * 100.0 * followthrough, 4)
    total += points
    components.append(
        ScoreComponent(
            name="plan_followthrough",
            points=points,
            max_points=round(FOCUS_FOLLOWTHROUGH_SHARE * 100.0, 4),
            explanation=(
                f"{completed_planned_sessions} planned session(s) completed against "
                f"{interruptions} reschedule(s)."
            ),
        )
    )
    return ScoreResult(score=max(0, min(100, round(total))), components=components)


def deadline_adherence(*, on_time: int, late: int, still_overdue: int) -> ScoreResult:
    """How much of the finished work landed on or before its due date.

    Formula: ``score = rate = on_time / (on_time + late)``, as a percentage.

    **Only finished work counts.** ``still_overdue`` is reported as its own
    zero-point component rather than folded into the denominator: a task due next
    month is not a missed deadline, and counting it would mean the score fell
    every time the user planned further ahead.

    **Unavailable when nothing has been finished.** A user with two completed
    tasks and no due dates on either has no deadline record; telling them they
    are at 0% adherence would be the exact fabrication the brief forbids. The
    reason says which of the two is missing.
    """
    finished = on_time + late
    if finished <= 0:
        return ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no task with a due date has been completed "
                "in this period, so there is nothing to compare against a deadline."
            ),
            components=[
                ScoreComponent(
                    name="still_overdue",
                    points=0.0,
                    max_points=0.0,
                    explanation=(
                        f"{still_overdue} task(s) are past due and still open; they do "
                        "not count against adherence until they are finished."
                    ),
                )
            ],
        )
    value = round(on_time / finished * 100.0, 4)
    adherence_points = round(value / 100.0 * 100.0, 4)
    return ScoreResult(
        score=max(0, min(100, round(adherence_points))),
        components=[
            ScoreComponent(
                name="on_time",
                points=adherence_points,
                max_points=100.0,
                explanation=f"{on_time} of {finished} completed task(s) finished by their due date.",
            ),
            ScoreComponent(
                name="still_overdue",
                points=0.0,
                max_points=0.0,
                explanation=(
                    f"{still_overdue} task(s) are past due and still open; they do "
                    "not count against adherence until they are finished."
                ),
            ),
        ],
    )


def estimation_accuracy(*, pairs: Sequence[tuple[int, int]]) -> EstimationResult:
    """How accurate ``estimated_minutes`` has been against ``actual_minutes``.

    Args:
        pairs: ``(estimated, actual)`` minute pairs. Pairs with a non-positive
            estimate are dropped rather than divided by, and are counted out of
            ``sample_count`` so the count always describes what was measured.

    Sign convention, and why it is ``estimated - actual``
    -----------------------------------------------------
    **The signed error is ``estimated - actual``.** A *negative* bias therefore
    means the estimates habitually ran below the time actually taken — you
    under-estimate, and :attr:`EstimationResult.underestimation_rate` beside it
    will be high. The two can never tell opposite stories about the same set of
    tasks, which is the whole reason for the sign.

    The opposite convention (``actual - estimated``) was considered and rejected
    for exactly that reason: on the worked example ``(60, 80)`` and ``(90, 150)``
    both estimates were short, so it would report a *positive* bias next to an
    under-estimation rate of 100%. A single figure that contradicts the figure
    printed beside it is worse than either sign.

    **Unavailable with an empty sequence.** :func:`median` over nothing is
    ``None`` and so is every field here; a user who has never estimated anything
    is told so rather than shown a flawless 0% error.
    """
    usable = [
        (float(estimated), float(actual))
        for estimated, actual in pairs
        if estimated is not None and actual is not None and float(estimated) > 0
    ]
    if not usable:
        return EstimationResult(
            available=False,
            reason_if_unavailable=(
                f"{NOT_ENOUGH_ACTIVITY}: no task in this period has both an estimate "
                "and a recorded actual duration, so accuracy cannot be measured."
            ),
        )

    signed = [estimated - actual for estimated, actual in usable]
    absolute = [abs(error) for error in signed]
    diverging = [abs(actual - estimated) / estimated * 100.0 for estimated, actual in usable]
    count = len(usable)
    bias = round(sum(signed) / count, 4)
    under_rate = round(sum(1 for error in signed if error < 0) / count * 100.0, 4)
    over_rate = round(sum(1 for error in signed if error > 0) / count * 100.0, 4)
    return EstimationResult(
        available=True,
        sample_count=count,
        absolute_error=round(sum(absolute) / count, 4),
        percentage_error=round(sum(diverging) / count, 4),
        bias=bias,
        median_error=median(absolute),
        under_estimation_rate=under_rate,
        over_estimation_rate=over_rate,
        components=[
            ScoreComponent(
                name="absolute_error",
                points=round(sum(absolute) / count, 4),
                max_points=round(sum(absolute) / count, 4),
                explanation=(
                    f"Across {count} estimated task(s) the average miss was "
                    f"{sum(absolute) / count:.1f} minutes, either way."
                ),
            ),
            ScoreComponent(
                name="bias",
                points=bias,
                max_points=round(sum(absolute) / count, 4),
                explanation=(
                    f"Estimates ran {abs(bias):.1f} minutes "
                    f"{'below' if bias < 0 else 'above'} the time actually taken, so "
                    f"{under_rate:.0f}% of tasks were under-estimated."
                ),
            ),
        ],
    )
