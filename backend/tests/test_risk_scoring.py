"""The risk scoring formulas, exercised as pure arithmetic.

No database, no HTTP, no ``integration`` marker: everything in
:mod:`app.services.risk.scoring` takes plain numbers and returns a
:class:`RiskResult`, so a score can be checked against a hand-derived integer
without provisioning a database to hold the facts that produced it. That matters
here more than it did for the analytics scores, because these numbers are the
only thing standing between "this user is at risk" and "this user is fine" — a
formula that quietly drifts by a few points moves rows between severity bands
and nobody notices, because a band is not an error.

Three things are asserted rather than merely exercised:

* **Exact figures.** Every expected value below is derived by hand from the
  documented formula, never copied from a previous run of the code. A change to
  a formula shows up as a wrong number rather than as a moved baseline.
* **The difference between "zero" and "unknown".** The module's stated contract
  is that "not enough data" scores *nothing at all* while "nothing is wrong"
  scores 0 with evidence saying so. Conflating them is how a product ends up
  telling a new user they have no risk, so the two are asserted separately for
  every detector that can decline.
* **The wording of the evidence.** A score is only trustworthy if a reader can
  see the inputs that produced it, and the brief forbids sentences that make
  claims about the person rather than the record. The evidence lines are
  therefore asserted as exact strings in the cases where the brief is specific
  about them.
"""

from __future__ import annotations

import itertools

import pytest

from app.models.enums import EvidenceStrength, RiskSeverity, RiskType
from app.services.risk.scoring import (
    DEFAULT_SEVERITY_THRESHOLDS,
    ESTIMATION_MIN_SAMPLES,
    NOT_ENOUGH_DATA,
    PROJECT_SCHEDULING_WEIGHTS,
    PROJECT_WEIGHTS,
    TASK_RESCHEDULE_THRESHOLD,
    TASK_WEIGHTS,
    RiskEvidence,
    RiskResult,
    consistency_risk,
    deadline_risk,
    estimation_risk,
    evidence_strength_for,
    project_risk,
    risk_severity_for,
    scheduling_risk,
    task_risk,
    workload_risk,
)

#: The brief's three estimation pairs, kept as the literal it states them as so a
#: reader can check the arithmetic: overruns of 50/60, 60/90 and 60/120 are
#: 0.8333, 0.6667 and 0.5000, which average to 0.6667.
OVERRUNING_PAIRS = [(60, 110), (90, 150), (120, 180)]

#: The default ladder restated, so a change to the constant has to be made
#: twice and a reviewer can see the bands without opening the scoring module.
DEFAULT_LADDER = (
    (75, RiskSeverity.CRITICAL.value),
    (50, RiskSeverity.HIGH.value),
    (25, RiskSeverity.MEDIUM.value),
    (0, RiskSeverity.LOW.value),
)

#: The band boundaries. Every score below is either one of these or has the
#: severity its position between two of them implies, so the ladder is pinned at
#: the edges as well as in the middle.
BAND_BOUNDARIES = [
    (0, RiskSeverity.LOW),
    (24, RiskSeverity.LOW),
    (25, RiskSeverity.MEDIUM),
    (49, RiskSeverity.MEDIUM),
    (50, RiskSeverity.HIGH),
    (74, RiskSeverity.HIGH),
    (75, RiskSeverity.CRITICAL),
    (100, RiskSeverity.CRITICAL),
]

#: A ladder whose lowest floor is above zero. Every score below that floor has
#: no band, so the mapping has to refuse rather than pick one by iteration order.
LADDER_WITHOUT_A_ZERO_FLOOR = (
    (75, RiskSeverity.CRITICAL.value),
    (50, RiskSeverity.HIGH.value),
    (25, RiskSeverity.MEDIUM.value),
)


def _labels(result: RiskResult) -> list[str]:
    """Evidence labels in order, for asserting which signals were reported."""
    return [item.label for item in result.evidence]


def _by_label(result: RiskResult, label: str) -> RiskEvidence:
    """The single evidence line with this label.

    Raises:
        KeyError: If no line carries the label, which is the failure the caller
            is asserting against anyway.
    """
    for item in result.evidence:
        if item.label == label:
            return item
    raise KeyError(label)


def _assert_declined(result: RiskResult, expected_type: RiskType) -> None:
    """Assert the cold-start shape every detector owes its caller.

    The three conditions are one invariant rather than three: an unavailable
    result has no score, it is not available, and it says why. A detector that
    breaks only the first has turned "cannot judge" into a confident 0.
    """
    assert result.risk_type is expected_type
    assert result.available is False
    assert result.score is None
    assert result.score != 0
    assert result.reason_if_unavailable
    assert result.severity is None
    assert result.evidence == []


# ---------------------------------------------------------------------------
# The brief's six worked examples
# ---------------------------------------------------------------------------


def test_deadline_work_unbooked_due_tomorrow_scores_sixty_and_is_high():
    """The brief's first worked example, exactly.

    300 minutes of work left, 120 booked, 24 hours until the deadline. The gap
    is 180 of 300, so the unbooked share is 0.6; a deadline tomorrow carries
    urgency 1.0; medium priority and no completion history are both neutral
    multipliers. ``100 * 0.6 * 1.0 = 60``.
    """
    result = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=24)

    assert result.risk_type is RiskType.DEADLINE
    assert result.score == 60
    assert result.severity is RiskSeverity.HIGH
    assert result.available is True
    assert result.reason_if_unavailable is None


def test_deadline_work_unbooked_with_eighteen_hours_left_scores_fifty_and_is_high():
    """Four hours of work with two booked is half unbooked, hence 50.

    Eighteen hours is still inside the 24-hour urgency rung, so the urgency
    multiplier is 1.0 and the score is the unbooked share itself.
    """
    result = deadline_risk(remaining_minutes=240, available_minutes=120, deadline_in_hours=18)

    assert result.score == 50
    assert result.severity is RiskSeverity.HIGH


def test_workload_at_thirty_eight_against_thirty_hours_scores_fifty_three_and_is_high():
    """2280 scheduled against 1800 available is 126.7%.

    The excess over capacity is 0.2667, and the span is 0.5, so
    ``100 * 0.2667 / 0.5 = 53.33`` — **53**, not 54. The contracts table agrees
    with the arithmetic; the module docstring's prose says 54 and is wrong.
    """
    result = workload_risk(scheduled_minutes=2280, available_minutes=1800, window_label="this week")

    assert result.risk_type is RiskType.WORKLOAD
    assert result.score == 53
    assert result.severity is RiskSeverity.HIGH
    assert result.metadata["scheduled_minutes"] == 2280
    assert result.metadata["available_minutes"] == 1800


def test_the_three_overrunning_estimation_pairs_score_eighty_three_and_are_critical():
    """The brief's third worked example, with its mean spelled out.

    Overruns of 0.8333, 0.6667 and 0.5000 average to 0.6667, and 0.6667 of the
    0.8 span is 83.33 — **83, CRITICAL**.
    """
    result = estimation_risk(pairs=OVERRUNING_PAIRS)

    assert result.risk_type is RiskType.ESTIMATION
    assert result.score == 83
    assert result.severity is RiskSeverity.CRITICAL
    assert result.metadata["mean_overrun"] == 0.6667
    assert result.metadata["sample_count"] == ESTIMATION_MIN_SAMPLES


def test_a_four_sevenths_week_after_a_twelve_sevenths_one_scores_sixty_seven_and_is_high():
    """The brief's fourth worked example: 4 of 7 active days against 12 of 7.

    The previous rate is 1.7143 days a day and the current one 0.5714, so the
    drop is ``(1.7143 - 0.5714) / 1.7143 = 0.6667`` — **67, HIGH**. An earlier
    window with more active days than it has days is legal input, not a bug;
    the rate is per-day precisely so the two windows are comparable.
    """
    result = consistency_risk(
        active_days=4, window_days=7, previous_active_days=12, previous_window_days=7
    )

    assert result.risk_type is RiskType.CONSISTENCY
    assert result.score == 67
    assert result.severity is RiskSeverity.HIGH
    assert result.metadata["current_rate"] == 0.5714
    assert result.metadata["previous_rate"] == 1.7143


def test_a_deadline_that_has_already_passed_scores_one_hundred_and_is_critical():
    """Nothing can be softer than "the date has passed".

    Reporting a task that is already three hours overdue as anything but the
    maximum would rank it below one that is merely going to be late.
    """
    result = deadline_risk(
        remaining_minutes=120,
        available_minutes=0,
        deadline_in_hours=-3,
        title="Ship the migration",
    )

    assert result.risk_type is RiskType.DEADLINE
    assert result.score == 100
    assert result.severity is RiskSeverity.CRITICAL
    assert result.available is True
    assert _by_label(result, "Deadline has passed").detail == (
        "due 3h ago with 2h of work outstanding"
    )
    assert result.metadata["title"] == "Ship the migration"


# ---------------------------------------------------------------------------
# risk_severity_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("score", "expected"), BAND_BOUNDARIES)
def test_severity_bands_start_on_the_score_that_reaches_them(score: int, expected: RiskSeverity):
    """Every band edge, asserted at the edge itself.

    A boundary tested one point inside is a boundary that can move: 25 is the
    first MEDIUM score, so 24 has to be LOW and 50 the first HIGH score, so 49
    has to be MEDIUM.
    """
    assert risk_severity_for(score) is expected


def test_a_score_above_the_top_of_the_scale_is_clamped_rather_than_banded_by_accident():
    """A caller passing 150 gets CRITICAL, not whatever a fifth band would say.

    The clamp is what stops an unvalidated score from reading as a severity the
    band ladder has no opinion about.
    """
    assert risk_severity_for(150) is RiskSeverity.CRITICAL
    assert risk_severity_for(1000) is RiskSeverity.CRITICAL


def test_a_score_below_zero_is_clamped_to_the_lowest_band():
    """-10 is not "less than low", it is LOW.

    Nothing in the engine should produce a negative score, but a caller with a
    hand-built result must not be able to produce a severity the ladder has no
    floor for.
    """
    assert risk_severity_for(-10) is RiskSeverity.LOW


def test_a_ladder_that_does_not_cover_zero_is_refused():
    """A ladder with no low floor leaves some scores with no band at all.

    Which band they fell into would then depend on iteration order rather than
    on the score, so the mapping refuses instead of guessing. The default ladder
    is complete, which is why this only shows up when a deployment retunes it.
    """
    with pytest.raises(ValueError, match="must cover score 0"):
        risk_severity_for(0, thresholds=LADDER_WITHOUT_A_ZERO_FLOOR)

    with pytest.raises(ValueError, match="must cover score 0"):
        risk_severity_for(10, thresholds=LADDER_WITHOUT_A_ZERO_FLOOR)


def test_the_default_ladder_is_the_four_documented_bands_highest_first():
    """The order is load-bearing: the first floor the score meets wins.

    A ladder sorted the other way round would band every score LOW, so the
    ordering is part of the constant rather than an implementation detail.
    """
    assert DEFAULT_SEVERITY_THRESHOLDS == DEFAULT_LADDER


# ---------------------------------------------------------------------------
# evidence_strength_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample_count", "expected"),
    [
        (0, EvidenceStrength.LOW),
        (2, EvidenceStrength.LOW),
        (9, EvidenceStrength.LOW),
        (10, EvidenceStrength.MEDIUM),
        (29, EvidenceStrength.MEDIUM),
        (30, EvidenceStrength.HIGH),
        (120, EvidenceStrength.HIGH),
    ],
)
def test_evidence_strength_is_the_sample_count_banded_at_ten_and_thirty(
    sample_count: int, expected: EvidenceStrength
):
    """Ten observations show a pattern; thirty are enough to lean on.

    The brief's own example is "2 historical tasks -> LOW, 120 -> HIGH", and
    both edges are asserted here at the count that changes the answer rather
    than one either side of it.

    This is not confidence and must never be read as it: nothing below is a
    probability or a fitted parameter, it is the number of observations the rule
    had, which is why the caller still has to show the score alongside it.
    """
    assert evidence_strength_for(sample_count) is expected


def test_the_evidence_bands_are_configurable_without_moving_the_default():
    """A deployment retunes the sample counts, not the meaning of the names."""
    assert evidence_strength_for(5, medium=4, high=8) is EvidenceStrength.MEDIUM
    assert evidence_strength_for(9, medium=4, high=8) is EvidenceStrength.HIGH


# ---------------------------------------------------------------------------
# Cold start: unavailable, and never a score of zero
# ---------------------------------------------------------------------------


def test_estimation_with_no_pairs_declines_and_never_scores_zero():
    """A user who has never estimated anything gets no score at all.

    Zero would be read as "your estimates have been exact", which is a claim
    about the person rather than an absence of measurement.
    """
    result = estimation_risk(pairs=[])

    _assert_declined(result, RiskType.ESTIMATION)
    assert result.reason_if_unavailable == (
        "Not enough historical data to estimate your typical task duration."
    )
    assert result.metadata["sample_count"] == 0


def test_estimation_with_two_pairs_declines_because_three_is_the_pattern_threshold():
    """Two pairs are below :data:`ESTIMATION_MIN_SAMPLES`.

    Three is the point at which "all of these ran long" is a pattern rather than
    a coincidence, so the detector reports a reason instead of a number built
    from two data points.
    """
    result = estimation_risk(pairs=[(60, 110), (90, 150)])

    _assert_declined(result, RiskType.ESTIMATION)
    assert result.metadata["sample_count"] == 2


def test_workload_without_declared_availability_declines_rather_than_scoring_zero():
    """Unconfigured availability is not zero available time.

    There is no capacity to have exceeded, so the honest answer is "this cannot
    be assessed" rather than "you are at 100% of nothing" — and never a score.
    """
    result = workload_risk(scheduled_minutes=2280, available_minutes=None)

    _assert_declined(result, RiskType.WORKLOAD)
    assert result.metadata["scheduled_minutes"] == 2280
    assert result.metadata["available_minutes"] is None


def test_consistency_without_a_previous_window_declines_and_explains_the_missing_baseline():
    """A fall cannot be measured against nothing.

    This is the first-period case: there is no earlier window, so there is no
    baseline, and reporting a drop of 100% would be an invention.
    """
    result = consistency_risk(
        active_days=5, window_days=7, previous_active_days=None, previous_window_days=7
    )

    _assert_declined(result, RiskType.CONSISTENCY)
    assert result.reason_if_unavailable == (
        "There is no earlier period with recorded activity to compare against."
    )


def test_consistency_against_a_previous_window_with_no_activity_declines():
    """Zero recorded days before is not a 100% drop.

    A percentage against a zero base is undefined, so the detector declines
    rather than reporting the worst possible score on a division it cannot make.
    """
    result = consistency_risk(
        active_days=0, window_days=7, previous_active_days=0, previous_window_days=7
    )

    _assert_declined(result, RiskType.CONSISTENCY)
    assert result.reason_if_unavailable == (
        "No activity was recorded in the earlier period, so there is no rate "
        "to compare this one against."
    )


def test_a_deadline_risk_with_no_due_date_declines_because_there_is_no_deadline_to_miss():
    """A task with no due date has no deadline risk.

    This is the one cold start that is not about history at all: the input the
    formula needs is simply absent, and the reason has to say which.
    """
    result = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=None)

    _assert_declined(result, RiskType.DEADLINE)
    assert result.reason_if_unavailable == "This task has no due date set."


def test_consistency_over_an_empty_window_uses_the_one_shared_message():
    """A zero-length window is the generic cold start, not a specific one.

    It shares the module's single phrase so a user in the first period sees one
    answer to "can you assess this?" rather than a different sentence per
    detector.
    """
    result = consistency_risk(
        active_days=0, window_days=0, previous_active_days=3, previous_window_days=7
    )

    _assert_declined(result, RiskType.CONSISTENCY)
    assert result.reason_if_unavailable == NOT_ENOUGH_DATA


# ---------------------------------------------------------------------------
# Deadline risk
# ---------------------------------------------------------------------------


def test_a_deadline_with_nothing_outstanding_is_a_measured_zero_not_an_absence():
    """The distinction the whole module is built around.

    A completed task still has a due date, so the detector *can* judge — and
    what it finds is that there is nothing left to schedule. Scoring that 0
    keeps it out of the Risk Center as a measurement; returning unavailable
    would claim there was nothing to look at.
    """
    result = deadline_risk(remaining_minutes=0, available_minutes=120, deadline_in_hours=48)

    assert result.available is True
    assert result.score == 0
    assert result.severity is RiskSeverity.LOW
    assert result.reason_if_unavailable is None
    assert _by_label(result, "No work outstanding").detail == (
        "nothing left to schedule before the deadline"
    )


def test_fully_booked_work_is_a_measured_zero_too():
    """More time booked than the work needs is the good case, at score 0."""
    result = deadline_risk(remaining_minutes=120, available_minutes=240, deadline_in_hours=24)

    assert result.available is True
    assert result.score == 0


def test_the_same_gap_scores_higher_the_soon_the_deadline_is():
    """Proximity scales the gap rather than adding to it.

    180 unbooked minutes out of 300 is 60 tomorrow and 9 thirty days out:
    ``100 * 0.6 * 0.15``. A month is a different problem from tonight even when
    the arithmetic of the shortfall is identical, and the urgency multiplier is
    where that difference lives.
    """
    soon = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=24)
    later = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=720)

    assert soon.score == 60
    assert later.score == 9
    assert later.severity is RiskSeverity.LOW


def test_urgency_steps_down_at_each_documented_rung():
    """72 hours and 168 hours are the two boundaries the ladder names.

    At the gap of the worked example, the multipliers 0.7 and 0.4 give 42 and
    24, which is exactly the difference between a HIGH band and a LOW one.
    """

    def _score(deadline_in_hours: float) -> int | None:
        return deadline_risk(
            remaining_minutes=300,
            available_minutes=120,
            deadline_in_hours=deadline_in_hours,
        ).score

    assert _score(72) == 42
    assert _score(168) == 24


def test_absent_completion_history_is_neutral_and_not_pessimistic():
    """``None`` must not be read as a low completion rate.

    Assuming a user finishes 40% of what they plan because nothing has been
    recorded yet is exactly the invented history the brief forbids. The score
    with no history is therefore the *same* score as the neutral 0.5 rate, and
    lower than the 0.0 rate a pessimistic reading would produce.
    """
    no_history = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=24)
    neutral = deadline_risk(
        remaining_minutes=300,
        available_minutes=120,
        deadline_in_hours=24,
        historical_completion_rate=0.5,
    )
    pessimistic = deadline_risk(
        remaining_minutes=300,
        available_minutes=120,
        deadline_in_hours=24,
        historical_completion_rate=0.0,
    )

    assert no_history.score == 60
    assert neutral.score == 60
    assert pessimistic.score == 72


def test_a_reliable_completion_history_lowers_the_score_for_the_same_gap():
    """Finishing 100% of what is planned absorbs a quarter of the gap.

    ``1 + (0.5 - 1.0) * 0.4 = 0.8``, and ``60 * 0.8 = 48``.
    """
    result = deadline_risk(
        remaining_minutes=300,
        available_minutes=120,
        deadline_in_hours=24,
        historical_completion_rate=1.0,
    )

    assert result.score == 48


def test_a_completion_history_is_reported_as_evidence_and_raises_the_strength():
    """Supplying the history adds the line that explains it, and widens the base.

    Evidence strength is the number of observations behind the rule, so a score
    adjusted by a real completion history is reported at HIGH while the same
    gap with no history is reported at LOW — the score moved *down* while its
    backing got stronger, which is the honest pair of facts.
    """
    without = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=24)
    with_history = deadline_risk(
        remaining_minutes=300,
        available_minutes=120,
        deadline_in_hours=24,
        historical_completion_rate=0.9,
    )

    assert len(without.evidence) == 2
    assert len(with_history.evidence) == 3
    assert _by_label(with_history, "Recent completion rate").detail == (
        "90% of tasks finished when due"
    )
    assert without.evidence_strength is EvidenceStrength.LOW
    assert with_history.evidence_strength is EvidenceStrength.HIGH


def test_the_deadline_evidence_states_the_gap_in_human_units():
    """The numbers are read by a person, so the detail line is in hours.

    The arithmetic behind it stays in minutes, but "3h of 5h (60%)" is the form
    a user can check against their own calendar.
    """
    result = deadline_risk(remaining_minutes=300, available_minutes=120, deadline_in_hours=48)

    assert _by_label(result, "Work not scheduled before the deadline").detail == (
        "3h of 5h (60%) has no time booked"
    )
    assert _by_label(result, "Time until the deadline").detail == "48h remaining"


# ---------------------------------------------------------------------------
# Workload risk
# ---------------------------------------------------------------------------


def test_scheduled_time_at_or_below_available_time_is_exactly_zero():
    """Exactly at capacity, and comfortably under it, both score 0.

    The line is inclusive because "booked to the minute" is not an
    overcommitment, and a formula using a strict comparison would report it as
    one.
    """
    at_capacity = workload_risk(scheduled_minutes=1800, available_minutes=1800)
    under_capacity = workload_risk(scheduled_minutes=1200, available_minutes=1800)

    assert at_capacity.available is True
    assert at_capacity.score == 0
    assert at_capacity.severity is RiskSeverity.LOW
    assert under_capacity.score == 0
    assert len(at_capacity.evidence) == 1


def test_half_again_the_available_time_is_the_worst_case_and_clamps_at_one_hundred():
    """Twice the capacity is 200% over the 0.5 span, so it saturates.

    A score above 100 would be meaningless on a 0-100 scale, and the detector
    clamps rather than letting the arithmetic escape.
    """
    result = workload_risk(scheduled_minutes=3600, available_minutes=1800)

    assert result.score == 100
    assert result.severity is RiskSeverity.CRITICAL
    assert _by_label(result, "Scheduled against available time").detail == (
        "60h scheduled against 30h available (200%)"
    )


def test_the_time_to_move_appears_only_when_there_is_an_excess_to_move():
    """At or under capacity there is nothing to move, so nothing is claimed."""
    overloaded = workload_risk(scheduled_minutes=2280, available_minutes=1800)
    within_capacity = workload_risk(scheduled_minutes=1800, available_minutes=1800)

    assert _by_label(overloaded, "Time to move").detail == (
        "approximately 8h of planned work is beyond the declared capacity"
    )
    assert "Time to move" not in _labels(within_capacity)


# ---------------------------------------------------------------------------
# Estimation risk
# ---------------------------------------------------------------------------


def test_a_zero_estimate_pair_is_ignored_rather_than_diluting_the_mean():
    """An unestimated task has no ratio and is dropped, not counted as 100%.

    Including it would let one task with no estimate distort a mean built from
    real ones — and it would push the sample count up to hide the thin sample
    the detector is supposed to be reporting.
    """
    with_the_zero = estimation_risk(pairs=[(0, 300), *[(60, 90), (90, 120), (120, 150)]])
    without_it = estimation_risk(pairs=[(60, 90), (90, 120), (120, 150)])

    # Overruns of 0.5, 0.3333 and 0.25 average to 0.3611, which is 45 of 100.
    assert without_it.score == 45
    assert with_the_zero.score == 45
    assert with_the_zero.metadata["sample_count"] == 3
    assert with_the_zero.evidence_strength is EvidenceStrength.LOW


def test_consistently_finishing_early_does_not_raise_the_score():
    """Only over-running counts against you.

    A user who finishes every task in half the time they estimated has a
    negative mean overrun, and scoring that as a risk would teach them to
    ignore the number. The result is a measurement of zero, not an absence of
    one, so it stays available and the Risk Center can drop it as a zero.
    """
    result = estimation_risk(pairs=[(120, 60), (90, 45), (60, 30)])

    assert result.available is True
    assert result.score == 0
    assert result.severity is RiskSeverity.LOW
    assert result.metadata["mean_overrun"] == -0.5
    assert result.metadata["over_estimation_count"] == 3
    assert _by_label(result, "Direction of the error").detail == "0 ran long, 3 ran short"


def test_estimating_exactly_right_is_a_measured_zero_as_well():
    """No error at all is the same measured zero, reached from the other side."""
    result = estimation_risk(pairs=[(60, 60), (90, 90), (120, 120)])

    assert result.available is True
    assert result.score == 0
    assert result.metadata["mean_overrun"] == 0.0


def test_the_estimation_evidence_reports_the_average_and_the_direction_separately():
    """The two lines answer two different questions about the same mean.

    67% longer than estimated across three tasks is the magnitude; "3 ran long,
    0 ran short" is whether the pattern is consistent, which is what tells a
    user whether the mean is reliable.
    """
    result = estimation_risk(pairs=OVERRUNING_PAIRS)

    assert _by_label(result, "Average overrun on recent tasks").detail == (
        "67% longer than estimated across 3 completed task(s)"
    )
    assert _by_label(result, "Direction of the error").detail == "3 ran long, 0 ran short"


def test_a_mixed_set_of_pairs_reports_both_directions():
    """One task finished late and one finished early; the mean cancels to zero.

    Overruns of +0.5 and -0.5 average to nothing, so the score is 0 — but the
    direction line still reports one of each, which is the honest description of
    a user whose estimates are noisy rather than biased.
    """
    result = estimation_risk(pairs=[(60, 90), (60, 30), (60, 60)])

    assert result.score == 0
    assert _by_label(result, "Direction of the error").detail == "1 ran long, 1 ran short"


# ---------------------------------------------------------------------------
# Consistency risk
# ---------------------------------------------------------------------------


def test_no_activity_at_all_against_a_full_previous_window_scores_one_hundred():
    """A complete fall from every day to no day is the worst case there is."""
    result = consistency_risk(
        active_days=0, window_days=7, previous_active_days=7, previous_window_days=7
    )

    assert result.available is True
    assert result.score == 100
    assert result.severity is RiskSeverity.CRITICAL


def test_more_activity_than_the_previous_window_is_a_measured_zero():
    """An improvement is not a risk, so it scores 0 and stays available.

    A negative drop is clamped rather than passed through; a score of -20 would
    be meaningless on a 0-100 scale and would band as LOW for the wrong reason.
    """
    result = consistency_risk(
        active_days=7, window_days=7, previous_active_days=3, previous_window_days=7
    )

    assert result.available is True
    assert result.score == 0
    assert result.severity is RiskSeverity.LOW


def test_the_two_windows_are_compared_per_day_not_as_raw_counts():
    """Seven active days against four is not an increase to worry about.

    Seven active days out of a 28-day month and four out of a 16-day stretch are
    both a rate of 0.25, so the drop is 0 and the score is 0. Comparing raw
    counts instead would have read the larger number as a fall to be explained,
    which is why the windows do not have to be the same length.
    """
    result = consistency_risk(
        active_days=7, window_days=28, previous_active_days=4, previous_window_days=16
    )

    assert result.score == 0
    assert result.metadata["current_rate"] == 0.25
    assert result.metadata["previous_rate"] == 0.25


def test_the_consistency_evidence_describes_recorded_activity_and_nothing_else():
    """The wording is part of the contract, not decoration.

    The brief forbids describing a user as lazy, unmotivated or unproductive on
    the strength of a calendar, and these two lines are the only sentences a
    reader sees. They report days and rates — countable facts — and draw no
    conclusion about the person behind them.
    """
    result = consistency_risk(
        active_days=4, window_days=7, previous_active_days=12, previous_window_days=7
    )

    assert _by_label(result, "Days with recorded activity").detail == (
        "4 of 7 this period, against 12 of 7 before"
    )
    assert _by_label(result, "Change in activity rate").detail == (
        "57% of days active, down from 171%"
    )


# ---------------------------------------------------------------------------
# Project risk
# ---------------------------------------------------------------------------


def test_the_project_weights_are_the_five_documented_shares_of_one():
    """They must sum to one, or the maximum score is not 100.

    Overdue carries the most because it is the only signal that is already a
    fact rather than a projection; velocity carries the least because it needs
    two periods of history and is the signal most likely to be missing.
    """
    assert PROJECT_WEIGHTS == {
        "overdue": 0.30,
        "blocked": 0.25,
        "deadline": 0.20,
        "velocity": 0.15,
        "remaining": 0.10,
    }
    assert sum(PROJECT_WEIGHTS.values()) == 1.0


def test_every_saturated_project_signal_together_scores_one_hundred():
    """10 overdue, 5 blocked, a week to the deadline and no pace at all.

    ``0.30 + 0.25 + 0.20 + 0.15 + 0.10 = 1.0``, so 20 remaining tasks
    saturates the last signal and the score is the maximum.
    """
    result = project_risk(
        overdue_tasks=10,
        blocked_tasks=5,
        days_to_deadline=7,
        remaining_tasks=20,
        required_velocity=10.0,
        recent_velocity=0.0,
    )

    assert result.risk_type is RiskType.PROJECT
    assert result.score == 100
    assert result.severity is RiskSeverity.CRITICAL
    assert result.metadata["signals"] == {
        "overdue": 1.0,
        "blocked": 1.0,
        "deadline": 1.0,
        "velocity": 1.0,
        "remaining": 1.0,
    }


def test_no_evidence_contributes_a_negative_number_of_points():
    """A negative contribution would make the score disagree with its breakdown.

    ``_share`` clamps to 0-1 so this is structurally impossible today; the
    assertion is here so a future normalisation that forgets to clamp shows up
    as a failing number rather than as a score that silently goes up.
    """
    result = project_risk(
        overdue_tasks=4,
        blocked_tasks=2,
        days_to_deadline=10,
        remaining_tasks=12,
        required_velocity=5.0,
        recent_velocity=9.0,
        project_name="Migration",
    )

    assert result.available is True
    assert result.evidence
    assert all(item.contribution >= 0.0 for item in result.evidence)
    assert all(item.contribution <= 100.0 for item in result.evidence)
    assert sum(item.contribution for item in result.evidence) == pytest.approx(
        result.score, abs=0.5
    )


def test_a_project_with_nothing_wrong_scores_zero_and_the_evidence_says_so():
    """A measured zero that explains itself, rather than a blank breakdown.

    This is the case where the project detector is available and the others are
    not: every input is a count, so "nothing is wrong" is a fact it can report
    rather than something it has to decline.
    """
    result = project_risk()

    assert result.available is True
    assert result.score == 0
    assert result.severity is RiskSeverity.LOW
    assert result.reason_if_unavailable is None
    assert _labels(result) == ["No project-level pressure detected"]
    assert _by_label(result, "No project-level pressure detected").detail == (
        "no overdue, blocked or remaining work recorded"
    )


def test_a_project_with_no_remaining_work_has_no_deadline_pressure_to_report():
    """A target date with nothing left to do is not pressure.

    The deadline signal is conditioned on remaining work, so a project whose
    tasks are all done does not score 20 points for a date that is merely near.
    """
    finished = project_risk(days_to_deadline=3, remaining_tasks=0)
    outstanding = project_risk(days_to_deadline=3, remaining_tasks=1)

    assert finished.score == 0
    assert "Target date approaching" not in _labels(finished)
    assert outstanding.score == 20
    assert _by_label(outstanding, "Target date approaching").detail == (
        "3 day(s) away with 1 task(s) left"
    )


def test_a_required_velocity_with_no_recent_figure_is_not_a_pressure_signal():
    """A missing recent figure is not a missing one, it is an absent signal.

    ``(required - recent) / required`` cannot be computed when nothing has been
    completed recently, so the velocity signal is skipped rather than evaluated
    against a default. Eight required against nothing achieved is a full signal
    and 15 points; eight required against four achieved is half a signal and
    ``0.15 * 0.5 * 100 = 7.5``, which rounds to **8**.
    """
    without_recent = project_risk(required_velocity=8.0, recent_velocity=None)
    half_the_pace = project_risk(required_velocity=8.0, recent_velocity=4.0)
    none_of_the_pace = project_risk(required_velocity=8.0, recent_velocity=0.0)

    assert without_recent.score == 0
    assert without_recent.metadata["signals"] == {}
    assert half_the_pace.score == 8
    assert none_of_the_pace.score == 15
    assert _by_label(half_the_pace, "Required pace exceeds the recent one").detail == (
        "8.0 task(s)/week needed against 4.0 recently completed"
    )


def test_a_pace_ahead_of_the_required_one_is_clamped_to_no_pressure():
    """Finishing faster than required contributes zero, not a negative score."""
    result = project_risk(required_velocity=4.0, recent_velocity=9.0)

    assert result.score == 0
    assert result.metadata["signals"] == {}


def test_project_risk_is_always_available_because_every_input_is_a_count():
    """Unlike the other detectors, this one never declines.

    There is no cold start to guard: zero overdue, zero blocked and zero
    remaining is a measurable state of a project, and reporting "not enough
    data" for it would hide the one answer that is definitely true. The grid
    covers the inputs that are ``None`` as well as the empty ones.
    """
    for overdue, blocked, days, remaining, required, recent in itertools.product(
        (0, 3, 25),
        (0, 2, 9),
        (None, -5, 3, 20, 400),
        (0, 1, 60),
        (None, 0.0, 6.0),
        (None, 0.0, 12.0),
    ):
        result = project_risk(
            overdue_tasks=overdue,
            blocked_tasks=blocked,
            days_to_deadline=days,
            remaining_tasks=remaining,
            required_velocity=required,
            recent_velocity=recent,
            project_name="Grid",
        )

        assert result.available is True, result.metadata
        assert result.reason_if_unavailable is None, result.metadata
        assert result.score is not None, result.metadata


# ---------------------------------------------------------------------------
# Scheduling risk
# ---------------------------------------------------------------------------


def test_scheduling_weights_are_the_four_documented_shares_of_one():
    """Same reasoning as the project weights: the maximum must be 100."""
    assert PROJECT_SCHEDULING_WEIGHTS == {
        "overlap": 0.35,
        "outside": 0.25,
        "after_deadline": 0.25,
        "consecutive": 0.15,
    }
    assert sum(PROJECT_SCHEDULING_WEIGHTS.values()) == 1.0


def test_the_four_scheduling_signals_are_detected_and_weighted_separately():
    """Each signal alone, then all four at once, at 35 + 25 + 25 + 15.

    Overlap carries the most weight because two sessions booked at once is
    arithmetic rather than interpretation: one of them cannot happen as planned.
    """
    overlap = scheduling_risk(overlapping_sessions=5)
    outside = scheduling_risk(outside_availability_sessions=5)
    after_deadline = scheduling_risk(sessions_after_deadline=1)
    everything = scheduling_risk(
        overlapping_sessions=5,
        outside_availability_sessions=5,
        sessions_after_deadline=1,
        longest_consecutive_run=12,
    )

    assert overlap.score == 35
    assert outside.score == 25
    assert after_deadline.score == 25
    assert everything.score == 100
    assert everything.severity is RiskSeverity.CRITICAL
    assert _labels(overlap) == ["Overlapping sessions"]
    assert _labels(outside) == ["Sessions outside declared availability"]
    assert _labels(after_deadline) == ["Work scheduled after its deadline"]


def test_a_short_run_of_sessions_is_not_reported_as_a_signal():
    """Six back-to-back sessions is a long run, not a finding.

    The detector reports the *excess* over six, so an ordinary week of daily
    work produces no line and no points.
    """
    ordinary = scheduling_risk(longest_consecutive_run=6)
    long_run = scheduling_risk(longest_consecutive_run=12)

    assert ordinary.score == 0
    assert _labels(ordinary) == ["No scheduling conflicts detected"]
    assert long_run.score == 15


def test_the_consecutive_sessions_evidence_states_only_the_count():
    """The brief asks for the number and rules out conclusions drawn from it.

    No claim about rest, fatigue, wellbeing or needing a break: a system that
    infers someone's physical state from a calendar is asserting something it
    has no data for. The line reports how long the run was and stops there.
    """
    result = scheduling_risk(longest_consecutive_run=11)
    evidence = _by_label(result, "Longest unbroken run of sessions")

    assert evidence.label == "Longest unbroken run of sessions"
    assert evidence.detail == "11 back-to-back sessions with no gap between them"
    forbidden = ("rest", "fatigue", "burnout", "tired", "break", "wellbeing", "well-being")
    assert not any(word in evidence.detail.lower() for word in forbidden)


def test_a_clean_schedule_is_a_measured_zero_that_explains_itself():
    """Zero here means "none of these were found", and the line says which."""
    result = scheduling_risk()

    assert result.available is True
    assert result.score == 0
    assert result.reason_if_unavailable is None
    assert _labels(result) == ["No scheduling conflicts detected"]
    assert _by_label(result, "No scheduling conflicts detected").detail == (
        "no overlaps, no out-of-hours work, nothing past a deadline"
    )


def test_one_session_after_a_deadline_scores_the_whole_signal():
    """Any session starting after its task is due is the signal, not a rate.

    There is no count to divide by — the question is whether the plan contains
    such a session at all — so one is as strong as five.
    """
    one = scheduling_risk(sessions_after_deadline=1)
    five = scheduling_risk(sessions_after_deadline=5)

    assert one.score == five.score == 25
    assert _by_label(one, "Work scheduled after its deadline").detail == (
        "1 session(s) start after the task is due"
    )


# ---------------------------------------------------------------------------
# The invariant that ties the scores to the bands
# ---------------------------------------------------------------------------


def _grid_of_results() -> list[RiskResult]:
    """One result per cell of a small grid across all six detectors.

    Wide enough to cross band boundaries rather than merely sampling inside
    them: the point is to catch a detector that banded its own score.
    """
    results: list[RiskResult] = []

    for remaining, available, hours in itertools.product(
        (0, 30, 120, 300, 900), (0, 60, 240, 600), (-10, 1, 24, 72, 200, 1000)
    ):
        results.append(
            deadline_risk(
                remaining_minutes=remaining,
                available_minutes=available,
                deadline_in_hours=hours,
                priority="critical" if remaining % 2 else "low",
                historical_completion_rate=None if hours % 3 else 0.3,
            )
        )

    for scheduled, capacity in itertools.product((0, 900, 1800, 2400, 5400), (None, 0, 600, 1800)):
        results.append(workload_risk(scheduled_minutes=scheduled, available_minutes=capacity))

    for pairs in (
        [],
        [(0, 30)],
        [(60, 60), (90, 90)],
        OVERRUNING_PAIRS,
        [(120, 60), (90, 45), (60, 30)],
        [(60, 300), (60, 30), (60, 90)],
    ):
        results.append(estimation_risk(pairs=pairs))

    for active, window, previous in itertools.product(
        (0, 2, 4, 7, 12), (7, 14, 28), (None, 0, 3, 12)
    ):
        results.append(
            consistency_risk(
                active_days=active,
                window_days=window,
                previous_active_days=previous,
                previous_window_days=7,
            )
        )

    for overdue, blocked, days, remaining in itertools.product(
        (0, 4, 20), (0, 3, 9), (None, 3, 20, 90), (0, 6, 40)
    ):
        results.append(
            project_risk(
                overdue_tasks=overdue,
                blocked_tasks=blocked,
                days_to_deadline=days,
                remaining_tasks=remaining,
                required_velocity=6.0,
                recent_velocity=3.0,
            )
        )

    for overlap, outside, after, run in itertools.product(
        (0, 2, 9), (0, 1, 7), (0, 3), (0, 5, 12, 30)
    ):
        results.append(
            scheduling_risk(
                overlapping_sessions=overlap,
                outside_availability_sessions=outside,
                sessions_after_deadline=after,
                longest_consecutive_run=run,
            )
        )

    return results


def test_every_detectors_severity_agrees_with_the_ladder_applied_to_its_score():
    """Severity is derived, so a detector cannot disagree with its own score.

    A row reading 91 and ``medium`` makes "why does this risk exist"
    unanswerable, and the way that happens is a detector band for itself instead
    of delegating. Grid-wide rather than per-case, because the failure would
    appear at whichever band boundary a single example happened to miss.
    """
    results = _grid_of_results()

    assert results
    for result in results:
        if result.score is None:
            assert result.severity is None, result.metadata
            continue
        assert result.severity is risk_severity_for(result.score), result.metadata


def test_no_scored_result_ever_escapes_the_zero_to_one_hundred_scale():
    """Every score is a clamped integer, whether the formula overflowed or not."""
    results = _grid_of_results()

    for result in results:
        if result.score is None:
            continue
        assert isinstance(result.score, int), result.metadata
        assert 0 <= result.score <= 100, result.metadata


# ---------------------------------------------------------------------------
# Task risk — the seventh detector
# ---------------------------------------------------------------------------


def test_one_blocked_task_scores_sixty_and_is_high():
    """The blocked signal on its own is ``0.60 x 100 = 60``, the ``high`` band.

    A blocked task is binary because its remaining duration is unbounded rather
    than large: one is exactly as blocking as five, so a weight scaled by a count
    of blocked tasks would be scaling by how much of the plan the user has
    already noticed.
    """
    result = task_risk(blocked=True, title="Ship the quarterly report")

    assert result.risk_type is RiskType.TASK
    assert result.score == 60
    assert result.severity is RiskSeverity.HIGH
    assert result.available is True
    assert result.reason_if_unavailable is None
    assert result.metadata["blocked"] is True
    assert result.metadata["reschedules"] == 0
    assert result.metadata["reschedule_threshold"] == TASK_RESCHEDULE_THRESHOLD
    assert result.metadata["title"] == "Ship the quarterly report"
    assert result.metadata["signals"] == {"blocked": 1.0}


def test_three_recorded_reschedules_scores_forty_and_is_medium():
    """The threshold itself is the signal, not the beginning of a ramp.

    ``0.40 x 100 = 40`` for the third move and for the ninth. The action the
    number leads to — break the task into pieces — is the same at both, and a
    ramp would let a task moved nine times outrank one that cannot be worked on
    at all, which is the wrong order for the same list.
    """
    threshold = task_risk(reschedules=TASK_RESCHEDULE_THRESHOLD)
    beyond = task_risk(reschedules=TASK_RESCHEDULE_THRESHOLD + 6)

    assert threshold.score == beyond.score == 40
    assert threshold.severity is RiskSeverity.MEDIUM
    assert threshold.metadata["signals"] == {"rescheduled": 1.0}
    assert _by_label(threshold, "Repeatedly rescheduled").detail == (
        "3 reschedules recorded against this task, against a threshold of 3"
    )


def test_a_blocked_task_that_was_also_rescheduled_saturates_the_scale():
    """``0.60 + 0.40`` is every point there is, and the weights are the whole score.

    Asserted as the weight table as well as the number, because the weights are
    the design decision and a formula that quietly stopped adding them up would
    produce a plausible-looking score from no stated reason.
    """
    result = task_risk(blocked=True, reschedules=TASK_RESCHEDULE_THRESHOLD)

    assert sum(TASK_WEIGHTS.values()) == pytest.approx(1.0)
    assert result.score == 100
    assert result.severity is RiskSeverity.CRITICAL
    assert result.metadata["signals"] == {"blocked": 1.0, "rescheduled": 1.0}
    assert sum(line.contribution for line in result.evidence) == pytest.approx(
        result.score, abs=0.5
    )


def test_two_reschedules_is_a_measured_zero_that_explains_the_threshold():
    """Below the threshold the answer is zero with evidence, never unavailability.

    "Not enough data" is for a detector that *cannot* judge. This one can: it has
    counted two reschedules and the threshold is three, and that is a
    measurement. The detail line carries both numbers so the zero can be read
    rather than assumed.
    """
    result = task_risk(reschedules=TASK_RESCHEDULE_THRESHOLD - 1)

    assert result.available is True
    assert result.score == 0
    assert result.severity is RiskSeverity.LOW
    assert result.metadata["signals"] == {}
    assert _labels(result) == ["No task-level condition recorded"]
    assert _by_label(result, "No task-level condition recorded").detail == (
        "not blocked, and 2 reschedule(s) against a threshold of 3"
    )


def test_the_task_evidence_names_the_record_and_nothing_about_the_person():
    """The count is the claim; whether the task is too large is not stated.

    The brief's worked example pairs the condition with the break-it-down
    suggestion, and it is worth keeping the inference in the recommendation and
    out of the risk: the rows record how often a date moved, not why.
    """
    result = task_risk(blocked=True, reschedules=4)

    details = " ".join(line.detail.lower() for line in result.evidence)
    assert "4 reschedules" in details
    for forbidden in ("lazy", "too large", "failing", "disorganised", "unproductive"):
        assert forbidden not in details


def test_task_risk_counts_its_evidence_from_the_rows_it_read():
    """One blocked row plus the moves behind it, and nothing invented.

    ``evidence_strength_for`` bands a sample count and never calls it confidence,
    so the question this pins is only whether the count is honest: a blocked task
    with no history is one observation, and a task that has been moved nine times
    and is blocked is ten.
    """
    blocked = task_risk(blocked=True)
    moved = task_risk(reschedules=TASK_RESCHEDULE_THRESHOLD)
    both = task_risk(blocked=True, reschedules=9)

    assert blocked.evidence_strength is EvidenceStrength.LOW
    assert moved.evidence_strength is EvidenceStrength.LOW
    assert both.evidence_strength is EvidenceStrength.MEDIUM


def test_every_task_score_agrees_with_the_ladder_applied_to_it():
    """Severity stays derived for the new detector, grid and all.

    The same invariant the other six are held to, over the whole input space the
    detector can be given rather than the four points the section above pins.
    """
    results = [
        task_risk(blocked=blocked, reschedules=reschedules)
        for blocked in (False, True)
        for reschedules in (0, 1, 2, 3, 5, 40)
    ]

    assert {result.score for result in results} == {0, 40, 60, 100}
    for result in results:
        assert result.available is True
        assert 0 <= result.score <= 100
        assert result.severity is risk_severity_for(result.score), result.metadata
        assert sum(line.contribution for line in result.evidence) == pytest.approx(
            result.score, abs=0.5
        ), result.metadata
