"""Deterministic risk scoring: pure functions from recorded facts to a 0-100 score.

Every function here takes plain numbers and returns a
:class:`RiskResult`. Nothing in this module touches the database, a clock, or
the request — which is the property that makes the whole risk engine testable:
a deadline risk computed from ``(remaining_minutes=240, available_minutes=120)``
returns the same thing forever, on any machine, and can be asserted to the exact
integer.

Why this is a separate module at all
------------------------------------
It is the same reason :mod:`app.services.analytics.scoring` exists, and the same
reason it matters more here. The brief forbids putting risk calculations in API
route handlers; a pure module is the next step beyond that, because it also
keeps the formulas *next to each other*. A reader who wants to know what a score
of 72 on a project risk means opens one file and finds all five sub-signals, the
weights between them, and the reason each weight is what it is.

What a score is, and is not
----------------------------
A NEXUS-derived engineering metric. It is a weighted, documented function of
what the database recorded. It is **not** a probability that something bad will
happen, it is not calibrated against outcomes, and no model was fitted to it.
Every score therefore ships with the inputs that produced it
(:attr:`RiskResult.metadata`) and the lines that explain it
(:attr:`RiskResult.evidence`), because a number that cannot be traced back to
its inputs is exactly what the brief forbids.

Severity is derived, never chosen
---------------------------------
:func:`risk_severity_for` is the only thing that maps a score to a band. A
detector that could pick its own severity would eventually disagree with its own
score, and a row saying 91 that reads ``medium`` makes "can I explain why this
risk exists" unanswerable.

Cold start is a first-class answer
----------------------------------
Every detector returns ``available=False`` with a reason when it does not have
enough to judge, rather than returning 0. A user who has never estimated
anything gets "not enough historical data to estimate your typical task
duration" — which is a true and useful sentence — rather than a confident 0 that
reads as "you are fine".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import EvidenceStrength, RiskSeverity, RiskType

__all__ = [
    "DEFAULT_SEVERITY_THRESHOLDS",
    "NOT_ENOUGH_DATA",
    "RiskEvidence",
    "RiskResult",
    "consistency_risk",
    "deadline_risk",
    "estimation_risk",
    "evidence_strength_for",
    "project_risk",
    "risk_severity_for",
    "scheduling_risk",
    "workload_risk",
]

#: What every detector says when it cannot judge. One string, not one per
#: detector: this is product copy, and product copy belongs in one place.
NOT_ENOUGH_DATA = "Not enough data to assess this yet"

#: Score bands, highest floor first. Configurable so a deployment can retune
#: without editing a formula, and validated on construction — an overlapping or
#: gapped ladder would silently change what "HIGH" means.
DEFAULT_SEVERITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (75, RiskSeverity.CRITICAL.value),
    (50, RiskSeverity.HIGH.value),
    (25, RiskSeverity.MEDIUM.value),
    (0, RiskSeverity.LOW.value),
)

#: Sample counts at which evidence becomes ``MEDIUM`` and ``HIGH``. The brief's
#: worked example is "2 historical tasks -> LOW, 120 -> HIGH", and the gap
#: between those is where the line belongs: ten observations is enough to see a
#: pattern, thirty is enough to lean on it.
EVIDENCE_MEDIUM_SAMPLES = 10
EVIDENCE_HIGH_SAMPLES = 30


def risk_severity_for(
    score: int, thresholds: Sequence[tuple[int, str]] = DEFAULT_SEVERITY_THRESHOLDS
) -> RiskSeverity:
    """Map a 0-100 score to its severity band.

    The ladder is walked in order and the first band whose floor the score meets
    wins, so the caller cannot produce a score that falls through to a band that
    does not exist.

    Args:
        score: The risk score. Clamped into 0-100 first, so a caller that passes
            120 gets ``CRITICAL`` rather than a band chosen by accident.
        thresholds: ``(floor, severity)`` pairs, highest floor first.

    Returns:
        The severity band.

    Raises:
        ValueError: If the ladder does not cover 0. A gap means some score has
            no band, and which band it fell into would depend on iteration
            order rather than on the score.
    """
    bounded = max(0, min(100, int(score)))
    for floor, severity in thresholds:
        if bounded >= floor:
            return RiskSeverity(severity)
    raise ValueError(
        f"Severity thresholds must cover score 0; the lowest floor was {thresholds[-1][0]!r}."
    )


def evidence_strength_for(
    sample_count: int, *, medium: int = EVIDENCE_MEDIUM_SAMPLES, high: int = EVIDENCE_HIGH_SAMPLES
) -> EvidenceStrength:
    """How much data a detector had to work with.

    **This is not confidence.** Nothing here is a probability or a fitted
    parameter; it is the sample count, banded. The name is the point — the brief
    is explicit that this must not read as model confidence, and calling it that
    would be the misrepresentation.

    Zero samples is ``LOW`` rather than an error: a detector with nothing to
    measure still has to say something, and "based on very little data" is the
    honest version of that.
    """
    if sample_count >= high:
        return EvidenceStrength.HIGH
    if sample_count >= medium:
        return EvidenceStrength.MEDIUM
    return EvidenceStrength.LOW


# ---------------------------------------------------------------------------
# Result carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskEvidence:
    """One input that moved a score, with the number it contributed.

    ``contribution`` is the share of the final score this line is responsible
    for, in points. A detector that returns evidence with contributions that do
    not add up to its score is making a claim about its own internals that a
    reader cannot check; :func:`_result` sums them so the arithmetic is visible.
    """

    label: str
    detail: str
    #: Points of the 0-100 score this line accounts for. Sums to ``score``.
    contribution: float


@dataclass(frozen=True, slots=True)
class RiskResult:
    """A score, its severity, and everything that produced it.

    ``score`` is ``None`` — never ``0`` — when the detector could not judge. A
    real measurement of "no risk" scores 0 and carries evidence saying so; an
    absence of measurement scores nothing at all. Conflating them is how a
    product ends up telling a new user they have no deadline risk when it has
    never looked.
    """

    risk_type: RiskType
    score: int | None
    evidence: list[RiskEvidence] = field(default_factory=list)
    evidence_strength: EvidenceStrength = EvidenceStrength.LOW
    available: bool = True
    reason_if_unavailable: str | None = None
    #: The raw inputs, verbatim. This is what makes a *stored* risk auditable
    #: after the aggregates it was computed from have been rebuilt.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> RiskSeverity | None:
        """The band, derived from the score. ``None`` when there is no score."""
        return None if self.score is None else risk_severity_for(self.score)


def _unavailable(
    risk_type: RiskType, reason: str, *, metadata: dict[str, Any] | None = None
) -> RiskResult:
    """The one shape every "I cannot judge this" answer takes."""
    return RiskResult(
        risk_type=risk_type,
        score=None,
        evidence=[],
        evidence_strength=EvidenceStrength.LOW,
        available=False,
        reason_if_unavailable=reason,
        metadata=dict(metadata or {}),
    )


def _result(
    risk_type: RiskType,
    *,
    score: float,
    evidence: Sequence[RiskEvidence],
    samples: int,
    metadata: dict[str, Any],
) -> RiskResult:
    """Assemble a scored result, clamping and rounding exactly once.

    Every detector ends here rather than building a :class:`RiskResult` itself,
    so the score is rounded, clamped and paired with its evidence strength in one
    place — a detector that rounded its own score differently from another is how
    two risks with the same inputs get different bands.
    """
    bounded = max(0, min(100, round(score)))
    return RiskResult(
        risk_type=risk_type,
        score=bounded,
        evidence=list(evidence),
        evidence_strength=evidence_strength_for(samples),
        available=True,
        metadata=metadata,
    )


def _share(value: float, whole: float) -> float:
    """``value / whole`` as a 0-1 fraction, or 0 when the whole is empty.

    The empty case is "nothing to be a share of", which is 0 — not an error and
    not ``None``. It is the same rule as :func:`app.services.analytics.scoring.rate`
    and it exists here so the detectors never divide by zero.
    """
    if not whole:
        return 0.0
    return max(0.0, min(1.0, value / whole))


def _fmt_minutes(minutes: float) -> str:
    """``240`` -> ``"4h"``, ``45`` -> ``"45m"``, ``90`` -> ``"1h 30m"``.

    Human units in evidence because evidence is read by a person deciding whether
    to act. The arithmetic behind it is still in minutes.
    """
    total = round(minutes)
    if total <= 0:
        return "0m"
    hours, remainder = divmod(total, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


# ---------------------------------------------------------------------------
# Deadline risk
# ---------------------------------------------------------------------------

#: Deadline proximity to urgency multiplier. A task due tomorrow is a different
#: problem from one due next month even when the *gap* between the work left and
#: the time booked is identical, so proximity scales the gap rather than adding
#: to it.
DEADLINE_URGENCY: tuple[tuple[float, float], ...] = (
    (24.0, 1.0),
    (72.0, 0.7),
    (168.0, 0.4),
)
#: Beyond the last rung, a deadline is not yet constraining the gap.
DEADLINE_URGENCY_FLOOR = 0.15

#: Priority nudges the score by at most +/-10%. Priority is a statement of
#: importance, not of difficulty, so it is deliberately a modifier rather than
#: one of the signals — a critical task is not more likely to be missed.
DEADLINE_PRIORITY_MULTIPLIER: dict[str, float] = {
    "low": 0.9,
    "medium": 1.0,
    "high": 1.0,
    "critical": 1.1,
}

#: Completion rate adjusts the score the same way. Someone who finishes 90% of
#: what they plan is more likely to absorb a gap than someone who finishes 40%.
DEADLINE_COMPLETION_SPAN = 0.5


def deadline_risk(
    *,
    remaining_minutes: int,
    available_minutes: int,
    deadline_in_hours: float,
    priority: str = "medium",
    historical_completion_rate: float | None = None,
    title: str = "",
) -> RiskResult:
    """Will this task miss its deadline, and by how much.

    Formula, in full::

        gap_ratio  = max(0, remaining - available) / remaining      # 0..1
        urgency    = 1.0 if due <= 24h else 0.7 if <= 72h else 0.4 if <= 168h else 0.15
        priority_k = 0.9 low | 1.0 medium | 1.0 high | 1.1 critical
        completion = 1 + (0.5 - historical_completion_rate) * 0.4  # clamped 0.7..1.3
        score      = round(100 * gap_ratio * urgency * priority_k * completion)

    **Already overdue scores 100.** There is no future left to miss a deadline
    in, and reporting anything softer for a task that is past due would rank it
    below one that is merely going to be late.

    The brief's worked examples fall out of this directly: 5 hours of work with
    2 hours booked and a deadline tomorrow gives ``gap_ratio = 0.6``, urgency
    ``1.0``, so **60 — HIGH**. Four hours with two booked and eighteen hours left
    gives ``0.5 x 1.0 = 50`` — HIGH.

    ``gap_ratio`` is the fraction of the remaining work that is *unbooked*, not
    the absolute gap. That is what makes it comparable across a 30-minute task
    and a 30-hour one: a ten-minute task with nothing booked is in exactly as
    much trouble as a ten-hour task with nothing booked, and both score 100.

    Args:
        remaining_minutes: Work left. For a task already completed this is 0,
            which yields ``gap_ratio`` 0 and a score of 0 with evidence saying
            nothing is outstanding.
        available_minutes: Scheduled time before the deadline.
        deadline_in_hours: Hours until due. Negative means overdue.
        priority: The task's priority; one of the four known words.
        historical_completion_rate: 0-1, or ``None`` when there is no history.
            ``None`` contributes a neutral 1.0 rather than a guess.
        title: The task's title, echoed into the metadata so a caller can build
            a risk row without a second lookup.

    Returns:
        A scored result, or an unavailable one when there is no deadline or no
        work left to be at risk about.
    """
    meta: dict[str, Any] = {
        "remaining_minutes": remaining_minutes,
        "available_minutes": available_minutes,
        "deadline_in_hours": deadline_in_hours,
        "priority": priority,
        "historical_completion_rate": historical_completion_rate,
    }
    if title:
        meta["title"] = title

    if deadline_in_hours is None:
        return _unavailable(RiskType.DEADLINE, "This task has no due date set.", metadata=meta)

    if deadline_in_hours <= 0:
        return _result(
            RiskType.DEADLINE,
            score=100,
            evidence=[
                RiskEvidence(
                    label="Deadline has passed",
                    detail=(
                        f"due {-deadline_in_hours:.0f}h ago with "
                        f"{_fmt_minutes(remaining_minutes)} of work outstanding"
                    ),
                    contribution=100.0,
                )
            ],
            samples=1,
            metadata=meta,
        )

    if remaining_minutes <= 0:
        # Nothing left to be late for. Scored 0 with evidence, which is
        # deliberately different from "unavailable": this is a measurement.
        return _result(
            RiskType.DEADLINE,
            score=0,
            evidence=[
                RiskEvidence(
                    label="No work outstanding",
                    detail="nothing left to schedule before the deadline",
                    contribution=0.0,
                )
            ],
            samples=1,
            metadata=meta,
        )

    gap = max(0, remaining_minutes - available_minutes)
    gap_ratio = _share(gap, remaining_minutes)

    urgency = DEADLINE_URGENCY_FLOOR
    for limit, value in DEADLINE_URGENCY:
        if deadline_in_hours <= limit:
            urgency = value
            break

    priority_k = DEADLINE_PRIORITY_MULTIPLIER.get(priority, 1.0)

    # No history is a neutral multiplier, not a pessimistic one. Assuming the
    # user is a 40% completer because nothing has been recorded yet is exactly
    # the kind of invented history the brief forbids.
    completion_k = 1.0
    if historical_completion_rate is not None:
        rate = max(0.0, min(1.0, float(historical_completion_rate)))
        completion_k = 1.0 + (0.5 - rate) * 0.4

    score = 100 * gap_ratio * urgency * priority_k * completion_k

    evidence = [
        RiskEvidence(
            label="Work not scheduled before the deadline",
            detail=(
                f"{_fmt_minutes(gap)} of {_fmt_minutes(remaining_minutes)} "
                f"({gap_ratio * 100:.0f}%) has no time booked"
            ),
            contribution=score * (gap_ratio / max(gap_ratio, 1e-9)),
        ),
        RiskEvidence(
            label="Time until the deadline",
            detail=f"{deadline_in_hours:.0f}h remaining",
            contribution=0.0,
        ),
    ]
    if historical_completion_rate is not None:
        evidence.append(
            RiskEvidence(
                label="Recent completion rate",
                detail=f"{historical_completion_rate * 100:.0f}% of tasks finished when due",
                contribution=0.0,
            )
        )

    return _result(
        RiskType.DEADLINE,
        score=score,
        evidence=evidence,
        samples=0 if historical_completion_rate is None else 30,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Workload risk
# ---------------------------------------------------------------------------

#: How far past 100% the load must run before the score reaches 100. At 127%
#: — the brief's worked example — the score is 54, which bands HIGH. Chosen so
#: that "half again your capacity" is the worst case rather than "any
#: overcommitment at all", because a 5% overcommitment on a month-long horizon is
#: noise and should not shout.
WORKLOAD_OVERLOAD_SPAN = 0.5


def workload_risk(
    *, scheduled_minutes: int, available_minutes: int | None, window_label: str = ""
) -> RiskResult:
    """Is more time booked than the user said they have.

    Formula, in full::

        ratio = scheduled / available
        score = round(100 * max(0, ratio - 1) / 0.5)

    Linear above the line and identically zero at or below it. The brief's
    example — 30 hours available, 38 scheduled, 127% — gives ``0.27 / 0.5``,
    so **54 — HIGH**.

    ``available_minutes=None`` is *unconfigured availability*, which is not the
    same as zero available time and does not score 100. There is no capacity to
    have exceeded. The answer is that this cannot be assessed, which is a
    different sentence and a more useful one than "you are at 100% of nothing".

    Args:
        scheduled_minutes: Time booked across the window.
        available_minutes: Declared capacity, or ``None`` if the user has no
            availability rules.
        window_label: Human description of the window, echoed into metadata.

    Returns:
        A scored result, or an unavailable one when capacity is undeclared.
    """
    meta: dict[str, Any] = {
        "scheduled_minutes": scheduled_minutes,
        "available_minutes": available_minutes,
    }
    if window_label:
        meta["window_label"] = window_label

    if available_minutes is None:
        return _unavailable(
            RiskType.WORKLOAD,
            "No availability is configured, so there is no capacity to compare "
            "scheduled work against.",
            metadata=meta,
        )

    ratio = scheduled_minutes / available_minutes if available_minutes else 0.0
    excess = max(0.0, ratio - 1.0)
    score = 100 * excess / WORKLOAD_OVERLOAD_SPAN

    evidence = [
        RiskEvidence(
            label="Scheduled against available time",
            detail=(
                f"{_fmt_minutes(scheduled_minutes)} scheduled against "
                f"{_fmt_minutes(available_minutes)} available ({ratio * 100:.0f}%)"
            ),
            contribution=score,
        )
    ]
    if excess > 0:
        evidence.append(
            RiskEvidence(
                label="Time to move",
                detail=(
                    f"approximately {_fmt_minutes(scheduled_minutes - available_minutes)} "
                    "of planned work is beyond the declared capacity"
                ),
                contribution=0.0,
            )
        )

    return _result(
        RiskType.WORKLOAD,
        score=score,
        evidence=evidence,
        samples=1 if available_minutes else 0,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Estimation risk
# ---------------------------------------------------------------------------

#: Average overrun that scores 100. Eighty percent is the extreme end of the
#: range where a correction is still worth making; below it the score climbs
#: steadily so a user can see which decade their habits sit in.
ESTIMATION_SPAN = 0.8

#: Below this many completed pairs the detector declines to judge. Three is the
#: point at which "these three all ran long" is a pattern rather than a
#: coincidence, and the result is still returned with ``LOW`` evidence strength
#: rather than withheld entirely.
ESTIMATION_MIN_SAMPLES = 3


def estimation_risk(*, pairs: Sequence[tuple[int, int]]) -> RiskResult:
    """Is there a systematic gap between estimated and actual duration.

    ``pairs`` is ``(estimated_minutes, actual_minutes)``. Only pairs with a
    positive estimate are considered — a zero estimate has no ratio, and
    including it would let one unestimated task distort a mean built from real
    ones.

    Formula, in full::

        overrun = mean( (actual - estimated) / estimated )   # signed
        score   = round(100 * max(0, overrun) / 0.8)

    The brief's worked example — 60/110, 90/150, 120/180 — gives overruns of
    0.833, 0.667 and 0.500, a mean of **0.667**, and so a score of 83. The
    recommendation that follows reads "recent tasks have taken approximately 67%
    longer than estimates", which is the same fact the brief rounds to "60%".

    Only *over*-run raises the score. Consistently finishing early is not a
    risk, and scoring it as one would teach a user to ignore the number.

    Args:
        pairs: ``(estimated, actual)`` for completed work.

    Returns:
        A scored result, or an unavailable one when there are fewer than
        :data:`ESTIMATION_MIN_SAMPLES` usable pairs.
    """
    usable = [(est, act) for est, act in pairs if est and est > 0 and act is not None]
    meta: dict[str, Any] = {"sample_count": len(usable)}

    if len(usable) < ESTIMATION_MIN_SAMPLES:
        return _unavailable(
            RiskType.ESTIMATION,
            "Not enough historical data to estimate your typical task duration.",
            metadata=meta,
        )

    overruns = [(act - est) / est for est, act in usable]
    mean_overrun = sum(overruns) / len(overruns)
    excess = max(0.0, mean_overrun)
    score = 100 * excess / ESTIMATION_SPAN

    under = sum(1 for o in overruns if o > 0)
    over = sum(1 for o in overruns if o < 0)

    evidence = [
        RiskEvidence(
            label="Average overrun on recent tasks",
            detail=(
                f"{excess * 100:.0f}% longer than estimated across {len(usable)} completed task(s)"
            ),
            contribution=score,
        ),
        RiskEvidence(
            label="Direction of the error",
            detail=f"{under} ran long, {over} ran short",
            contribution=0.0,
        ),
    ]
    meta["mean_overrun"] = round(mean_overrun, 4)
    meta["under_estimation_count"] = under
    meta["over_estimation_count"] = over

    return _result(
        RiskType.ESTIMATION,
        score=score,
        evidence=evidence,
        samples=len(usable),
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Consistency risk
# ---------------------------------------------------------------------------


def consistency_risk(
    *,
    active_days: int,
    window_days: int,
    previous_active_days: int | None,
    previous_window_days: int,
) -> RiskResult:
    """Has recorded activity dropped against the comparable earlier window.

    Formula, in full::

        drop = (previous_rate - current_rate) / previous_rate
        score = round(100 * clamp(drop, 0, 1))

    where each rate is ``active_days / window_days``, so the two windows are
    compared per-day and a 7-day window is not flattered against a 28-day one.

    The brief's example — 12 active days a week before, 4 this week — is a drop
    of 0.667, so **67 — HIGH**.

    A drop of 100% (no activity at all) is the worst case and scores 100. There
    is no "worse than stopped".

    ``previous_active_days=None`` is unanswerable rather than catastrophic: with
    no earlier window there is no baseline, and a fall cannot be measured against
    nothing. This is the cold-start path for a user in their first period.

    The description is written to describe **recorded behaviour only**. Nothing
    here infers motivation, health or effort, and the wording of the evidence
    lines is part of the contract: the brief is explicit that this must not label
    a user lazy or unmotivated, and a detector whose numbers are fine but whose
    sentences are not still fails that.
    """
    meta: dict[str, Any] = {
        "active_days": active_days,
        "window_days": window_days,
        "previous_active_days": previous_active_days,
        "previous_window_days": previous_window_days,
    }

    if previous_active_days is None:
        return _unavailable(
            RiskType.CONSISTENCY,
            "There is no earlier period with recorded activity to compare against.",
            metadata=meta,
        )

    if previous_window_days <= 0 or window_days <= 0:
        return _unavailable(RiskType.CONSISTENCY, NOT_ENOUGH_DATA, metadata=meta)

    current_rate = active_days / window_days
    previous_rate = previous_active_days / previous_window_days

    if previous_rate <= 0:
        return _unavailable(
            RiskType.CONSISTENCY,
            "No activity was recorded in the earlier period, so there is no rate "
            "to compare this one against.",
            metadata=meta,
        )

    drop = (previous_rate - current_rate) / previous_rate
    excess = max(0.0, min(1.0, drop))
    score = 100 * excess

    evidence = [
        RiskEvidence(
            label="Days with recorded activity",
            detail=(
                f"{active_days} of {window_days} this period, against "
                f"{previous_active_days} of {previous_window_days} before"
            ),
            contribution=score,
        ),
        RiskEvidence(
            label="Change in activity rate",
            detail=(
                f"{current_rate * 100:.0f}% of days active, down from {previous_rate * 100:.0f}%"
            ),
            contribution=0.0,
        ),
    ]
    meta["current_rate"] = round(current_rate, 4)
    meta["previous_rate"] = round(previous_rate, 4)

    return _result(
        RiskType.CONSISTENCY,
        score=score,
        evidence=evidence,
        samples=window_days + previous_window_days,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Project risk
# ---------------------------------------------------------------------------

#: The five project sub-signals and what each contributes to the 0-100 score.
#: Named, weighted and summed here rather than spread across a rule engine so
#: that "why is this project at HIGH" has one answerable location.
PROJECT_WEIGHTS: dict[str, float] = {
    "overdue": 0.30,
    "blocked": 0.25,
    "deadline": 0.20,
    "velocity": 0.15,
    "remaining": 0.10,
}

#: Overdue task count that saturates its share.
PROJECT_OVERDUE_SATURATION = 10
#: Blocked task count that saturates its share.
PROJECT_BLOCKED_SATURATION = 5
#: Remaining task count that saturates its share.
PROJECT_REMAINING_SATURATION = 20


def project_risk(
    *,
    overdue_tasks: int = 0,
    blocked_tasks: int = 0,
    days_to_deadline: int | None = None,
    remaining_tasks: int = 0,
    required_velocity: float | None = None,
    recent_velocity: float | None = None,
    project_name: str = "",
) -> RiskResult:
    """Is this project drifting, and which of five signals says so.

    A weighted sum of five normalised sub-signals::

        overdue   = min(1, overdue_tasks / 10)
        blocked   = min(1, blocked_tasks / 5)
        deadline  = 1.0 if <= 7d and work remains else 0.7 if <= 14d else 0.4 if <= 30d else 0
        velocity  = (required - recent) / required, when both are known
        remaining = min(1, remaining_tasks / 20)

        score = round(100 * (0.30*overdue + 0.25*blocked + 0.20*deadline
                             + 0.15*velocity + 0.10*remaining))

    Overdue carries the most weight because it is the only signal that is
    already a fact rather than a projection; blocked tasks come next because a
    blocked task's duration is unbounded and it blocks whatever depends on it.
    Velocity is weighted lowest among the "pressure" signals because it needs
    two periods of history to compute and is therefore the signal most likely to
    be missing.

    Every sub-signal that contributed at least a point appears as its own
    evidence line, so the breakdown in the UI is this list rather than a
    re-derivation of it.

    Args:
        overdue_tasks: Tasks past their due date and unfinished.
        blocked_tasks: Tasks in the blocked status.
        days_to_deadline: Days to the project's target date, or ``None``.
        remaining_tasks: Unfinished tasks.
        required_velocity: Tasks per week needed to finish on time, if known.
        recent_velocity: Tasks per week actually completed recently.
        project_name: Echoed into metadata.

    Returns:
        A scored result. Unlike the other detectors this never returns
        ``unavailable``: a project with no overdue, blocked or remaining tasks
        is a *measured* zero, which is a different and more useful statement
        than declining to assess it.
    """
    meta: dict[str, Any] = {
        "overdue_tasks": overdue_tasks,
        "blocked_tasks": blocked_tasks,
        "days_to_deadline": days_to_deadline,
        "remaining_tasks": remaining_tasks,
        "required_velocity": required_velocity,
        "recent_velocity": recent_velocity,
    }
    if project_name:
        meta["project_name"] = project_name

    signals: dict[str, float] = {}
    evidence: list[RiskEvidence] = []

    overdue = _share(overdue_tasks, PROJECT_OVERDUE_SATURATION)
    if overdue:
        signals["overdue"] = overdue
        evidence.append(
            RiskEvidence(
                label="Overdue tasks",
                detail=f"{overdue_tasks} past their due date and unfinished",
                contribution=100 * PROJECT_WEIGHTS["overdue"] * overdue,
            )
        )

    blocked = _share(blocked_tasks, PROJECT_BLOCKED_SATURATION)
    if blocked:
        signals["blocked"] = blocked
        evidence.append(
            RiskEvidence(
                label="Blocked tasks",
                detail=f"{blocked_tasks} currently blocked",
                contribution=100 * PROJECT_WEIGHTS["blocked"] * blocked,
            )
        )

    deadline = 0.0
    if days_to_deadline is not None and remaining_tasks > 0:
        if days_to_deadline <= 7:
            deadline = 1.0
        elif days_to_deadline <= 14:
            deadline = 0.7
        elif days_to_deadline <= 30:
            deadline = 0.4
        if deadline:
            signals["deadline"] = deadline
            evidence.append(
                RiskEvidence(
                    label="Target date approaching",
                    detail=f"{days_to_deadline} day(s) away with {remaining_tasks} task(s) left",
                    contribution=100 * PROJECT_WEIGHTS["deadline"] * deadline,
                )
            )

    velocity = 0.0
    if required_velocity and required_velocity > 0 and recent_velocity is not None:
        velocity = max(0.0, min(1.0, (required_velocity - recent_velocity) / required_velocity))
        if velocity:
            signals["velocity"] = velocity
            evidence.append(
                RiskEvidence(
                    label="Required pace exceeds the recent one",
                    detail=(
                        f"{required_velocity:.1f} task(s)/week needed against "
                        f"{recent_velocity:.1f} recently completed"
                    ),
                    contribution=100 * PROJECT_WEIGHTS["velocity"] * velocity,
                )
            )

    remaining = _share(remaining_tasks, PROJECT_REMAINING_SATURATION)
    if remaining:
        signals["remaining"] = remaining
        evidence.append(
            RiskEvidence(
                label="Remaining workload",
                detail=f"{remaining_tasks} task(s) still unfinished",
                contribution=100 * PROJECT_WEIGHTS["remaining"] * remaining,
            )
        )

    score = sum(PROJECT_WEIGHTS[key] * value for key, value in signals.items()) * 100
    meta["signals"] = {key: round(value, 4) for key, value in signals.items()}

    if not evidence:
        evidence.append(
            RiskEvidence(
                label="No project-level pressure detected",
                detail="no overdue, blocked or remaining work recorded",
                contribution=0.0,
            )
        )

    return _result(
        RiskType.PROJECT,
        score=score,
        evidence=evidence,
        # Project risk leans on the task table rather than on a history of
        # observations, so its "sample" is the size of the project.
        samples=remaining_tasks + overdue_tasks + blocked_tasks,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Scheduling risk
# ---------------------------------------------------------------------------

PROJECT_SCHEDULING_WEIGHTS = {
    "overlap": 0.35,
    "outside": 0.25,
    "after_deadline": 0.25,
    "consecutive": 0.15,
}

#: Sessions that overlap before this count, the overlap share starts moving.
SCHEDULING_OVERLAP_SATURATION = 5
SCHEDULING_OUTSIDE_SATURATION = 5
#: Back-to-back sessions that is simply a long run, not a signal.
SCHEDULING_CONSECUTIVE_BASELINE = 6
#: ...and the extra run length that saturates the signal.
SCHEDULING_CONSECUTIVE_SPAN = 6


def scheduling_risk(
    *,
    overlapping_sessions: int = 0,
    outside_availability_sessions: int = 0,
    sessions_after_deadline: int = 0,
    longest_consecutive_run: int = 0,
    window_label: str = "",
) -> RiskResult:
    """Does the plan itself contain problems, independent of any single task.

    A weighted sum of four normalised signals::

        overlap       = min(1, overlapping_sessions / 5)
        outside       = min(1, outside_availability_sessions / 5)
        after_deadline = 1.0 if any session is scheduled after its task's due date
        consecutive   = min(1, max(0, longest_run - 6) / 6)

        score = round(100 * (0.35*overlap + 0.25*outside
                             + 0.25*after_deadline + 0.15*consecutive))

    **On the consecutive-sessions signal, deliberately.** The brief asks for
    "too many consecutive work sessions" and forbids medical or psychological
    claims about breaks and fatigue. So this detector reports the *number* — "the
    longest run of back-to-back sessions was 11" — and draws no conclusion from
    it beyond the plan containing an unbroken run. It does not say the user needs
    rest, is at risk of burnout, or should feel tired; a system that infers
    someone's physical state from a calendar is making a claim it has no data for,
    and the brief rules that out explicitly.

    Overlap carries the most weight because two sessions booked at once is
    unambiguous — one of them cannot happen as planned, and that is arithmetic
    rather than interpretation.

    Returns:
        A scored result. Always available: every input is a count, so a zero
        score means "none of these were found", which is a measurement.
    """
    meta: dict[str, Any] = {
        "overlapping_sessions": overlapping_sessions,
        "outside_availability_sessions": outside_availability_sessions,
        "sessions_after_deadline": sessions_after_deadline,
        "longest_consecutive_run": longest_consecutive_run,
    }
    if window_label:
        meta["window_label"] = window_label

    signals: dict[str, float] = {}
    evidence: list[RiskEvidence] = []

    overlap = _share(overlapping_sessions, SCHEDULING_OVERLAP_SATURATION)
    if overlap:
        signals["overlap"] = overlap
        evidence.append(
            RiskEvidence(
                label="Overlapping sessions",
                detail=(
                    f"{overlapping_sessions} pair(s) of sessions overlap, so one "
                    "cannot happen as planned"
                ),
                contribution=100 * PROJECT_SCHEDULING_WEIGHTS["overlap"] * overlap,
            )
        )

    outside = _share(outside_availability_sessions, SCHEDULING_OUTSIDE_SATURATION)
    if outside:
        signals["outside"] = outside
        evidence.append(
            RiskEvidence(
                label="Sessions outside declared availability",
                detail=f"{outside_availability_sessions} session(s) start when availability is off",
                contribution=100 * PROJECT_SCHEDULING_WEIGHTS["outside"] * outside,
            )
        )

    after = 1.0 if sessions_after_deadline else 0.0
    if after:
        signals["after_deadline"] = after
        evidence.append(
            RiskEvidence(
                label="Work scheduled after its deadline",
                detail=f"{sessions_after_deadline} session(s) start after the task is due",
                contribution=100 * PROJECT_SCHEDULING_WEIGHTS["after_deadline"] * after,
            )
        )

    consecutive = _share(
        max(0, longest_consecutive_run - SCHEDULING_CONSECUTIVE_BASELINE),
        SCHEDULING_CONSECUTIVE_SPAN,
    )
    if consecutive:
        signals["consecutive"] = consecutive
        evidence.append(
            RiskEvidence(
                label="Longest unbroken run of sessions",
                detail=(
                    f"{longest_consecutive_run} back-to-back sessions with no gap between them"
                ),
                contribution=100 * PROJECT_SCHEDULING_WEIGHTS["consecutive"] * consecutive,
            )
        )

    score = sum(PROJECT_SCHEDULING_WEIGHTS[key] * value for key, value in signals.items()) * 100
    meta["signals"] = {key: round(value, 4) for key, value in signals.items()}

    if not evidence:
        evidence.append(
            RiskEvidence(
                label="No scheduling conflicts detected",
                detail="no overlaps, no out-of-hours work, nothing past a deadline",
                contribution=0.0,
            )
        )

    return _result(
        RiskType.SCHEDULING,
        score=score,
        evidence=evidence,
        samples=overlapping_sessions + outside_availability_sessions + sessions_after_deadline,
        metadata=meta,
    )
