"""The analytics scoring formulas, exercised as pure arithmetic.

No database, no HTTP, no ``integration`` marker: everything in
:mod:`app.services.analytics.scoring` takes plain numbers and returns a
:class:`ScoreResult`, so the whole module can be verified by hand against a
fixed input. That is the point of these tests — the brief's rule is that
"if: 10 tasks, 8 completed / then: completion rate must equal 80%", and 80% is
only checkable if the calculation is reachable without provisioning a database
to hold ten tasks.

Two things are asserted rather than merely exercised:

* **Exact figures.** Every expected value below is derived by hand from the
  documented formula, never copied from a previous run of the code, so a change
  to a formula shows up as a wrong number instead of a moved baseline.
* **The difference between "zero" and "unknown."** The module's stated purpose
  is that ``"Not enough activity yet"`` is correct and ``"0% productivity"`` is
  not. So a measured zero (``0.8`` completion, ``0`` active days, ``0`` on-time
  tasks) and an unavailable metric are asserted in separate tests and must never
  be allowed to drift together.
"""

from __future__ import annotations

import math

from app.services.analytics.scoring import (
    DEFAULT_WEIGHTS,
    NOT_ENOUGH_ACTIVITY,
    ScoreComponent,
    ScoreResult,
    absolute_change,
    clamp_percentage,
    consistency_score,
    deadline_adherence,
    estimation_accuracy,
    focus_score,
    median,
    percent_change,
    productivity_score,
    rate,
)

#: The default weighting, restated here so a change to the constant has to be
#: made twice. The four numbers are what the four component points are out of.
WEIGHTS = {"completion": 30.0, "deadline": 25.0, "consistency": 20.0, "focus": 25.0}

#: The worked example from the brief: completion 24, consistency 19, deadline
#: 18, focus 17 — a productivity score of 78. Each rate is the component's
#: share of its weight, so the four inputs below are the exact inverse of the
#: 78 shown in the specification.
EXAMPLE_INPUTS = {
    "completion_rate": 0.8,  # 30 * 0.80 = 24
    "deadline_adherence": 0.72,  # 25 * 0.72 = 18
    "consistency": 0.95,  # 20 * 0.95 = 19
    "focus": 0.68,  # 25 * 0.68 = 17
}
EXAMPLE_SCORE = 78


def _by_name(result: ScoreResult) -> dict[str, ScoreComponent]:
    """Index a breakdown by component name, for readable exact assertions."""
    return {component.name: component for component in result.components}


# -- clamp_percentage -------------------------------------------------------


def test_clamp_percentage_passes_an_ordinary_rate_through_unchanged():
    """The common case must be a no-op, not a rounding of its own."""
    assert clamp_percentage(0.8) == 0.8
    assert clamp_percentage(0.0) == 0.0
    assert clamp_percentage(1.0) == 1.0


def test_clamp_percentage_clamps_above_one_and_below_zero():
    """A share above 1.0 is an over-count somewhere upstream, not a 140% rate.

    Clamping rather than passing the number through is what stops a duplicated
    event from producing a component worth more than the whole score.
    """
    assert clamp_percentage(1.4) == 1.0
    assert clamp_percentage(3.0) == 1.0
    assert clamp_percentage(-0.2) == 0.0
    assert clamp_percentage(-1.0) == 0.0


def test_clamp_percentage_keeps_none_as_none():
    """``None`` means "no rate exists" and must survive the clamp.

    Turning it into ``0.0`` here would invent a 0% out of an absence, which is
    the single failure the brief calls out by name.
    """
    assert clamp_percentage(None) is None


# -- absolute_change --------------------------------------------------------


def test_absolute_change_is_the_signed_difference():
    assert absolute_change(27, 22) == 5.0
    assert absolute_change(22, 27) == -5.0
    assert absolute_change(0, 0) == 0.0


def test_absolute_change_is_defined_where_a_percentage_is_not():
    """Zero to something has an absolute change, and no percentage change.

    The two helpers exist so that the pair can say that without disagreeing
    about it: the absolute change from nothing to 27 tasks is 27, and the
    percentage change from nothing to 27 tasks does not exist.
    """
    assert absolute_change(27, 0) == 27.0
    assert absolute_change(0, 0) == 0.0
    assert percent_change(27, 0) is None


def test_absolute_change_rounds_to_four_places():
    assert absolute_change(10.0, 3.3333) == 6.6667


# -- percent_change ---------------------------------------------------------


def test_percent_change_computes_the_signed_percentage():
    """The brief's own worked example: 27 this week against 22 last week."""
    assert percent_change(27, 22) == 22.7273
    assert percent_change(22, 27) == -18.5185
    assert percent_change(10, 4) == 150.0
    assert percent_change(0, 10) == -100.0


def test_percent_change_is_none_when_the_previous_period_was_zero():
    """Zero to something is a division by zero, not an infinite growth rate.

    ``(27 - 0) / 0`` is ``inf`` and ``(0 - 0) / 0`` is ``nan``; both would reach
    the browser as the literal text ``Infinity%`` or ``NaN``, which the brief
    forbids. The honest answer is that there is no percentage change from
    nothing to something.
    """
    assert percent_change(27, 0) is None
    assert percent_change(0, 0) is None
    assert percent_change(-27, 0) is None


def test_percent_change_is_none_from_a_negative_base():
    """A negative previous figure has no readable percentage change.

    ``(5 - -5) / -5 * 100`` would render as ``-200%``, a number that is both
    sign-flipped and less informative than saying nothing at all.
    """
    assert percent_change(5, -5) is None
    assert percent_change(0, -5) is None


def test_percent_change_never_returns_infinity_or_nan():
    """The whole reason the function exists, checked over a grid.

    Every zero and negative base comes back as ``None`` rather than as a
    non-finite float, so a later refactor cannot quietly reintroduce the
    ``Infinity`` the module docstring warns about.
    """
    currents = [-50, -1, 0, 1, 50, 1000]
    previous = [-10, -1, 0, 1, 10, 100]

    for current in currents:
        for base in previous:
            change = percent_change(current, base)
            if base <= 0:
                assert change is None, f"percent_change({current}, {base}) = {change}"
            else:
                assert change is not None
                assert math.isfinite(change), f"percent_change({current}, {base}) = {change}"


# -- median -----------------------------------------------------------------


def test_median_of_nothing_is_none():
    """``None``, not ``0``.

    The median of an empty set is unknown, and zero is a claim about a middle
    value that does not exist.
    """
    assert median([]) is None


def test_median_of_an_odd_number_of_values_is_the_middle_one():
    assert median([3, 1, 2]) == 2.0
    assert median([7]) == 7.0


def test_median_of_an_even_number_of_values_is_the_mean_of_the_two_middle_ones():
    assert median([4, 1, 3, 2]) == 2.5


def test_median_sorts_its_input_rather_than_trusting_the_order():
    """Callers pass unsorted column values, so ordering is the function's job."""
    assert median([10, 2, 38, 23, 38, 23, 21]) == 23.0


# -- rate -------------------------------------------------------------------


def test_rate_is_the_briefs_completion_example():
    """8 of 10 is 80%. Nothing here is a fraction of a fraction."""
    assert rate(8, 10) == 80.0
    assert rate(2, 10) == 20.0


def test_rate_of_a_zero_numerator_is_a_real_zero():
    """0 of 10 completed is a measurement of zero, and must read as one."""
    assert rate(0, 10) == 0.0


def test_rate_is_none_for_a_zero_denominator():
    """0 of 0 has no rate. It is not 0% and it is not 100%."""
    assert rate(0, 0) is None
    assert rate(5, 0) is None


def test_rate_rounds_to_four_places():
    assert rate(2, 3) == 66.6667
    assert rate(18, 21) == 85.7143


# -- productivity_score -----------------------------------------------------


def test_the_default_weights_are_the_four_documented_shares_of_one_hundred():
    assert DEFAULT_WEIGHTS == WEIGHTS
    assert sum(DEFAULT_WEIGHTS.values()) == 100.0


def test_the_productivity_score_is_the_weighted_blend_of_its_four_components():
    """The brief's worked example, expanded: 24 + 19 + 18 + 17 = 78.

    ``30*0.8 + 25*0.72 + 20*0.95 + 25*0.68 = 24 + 18 + 19 + 17 = 78``.
    """
    result = productivity_score(**EXAMPLE_INPUTS)

    assert result.available is True
    assert result.score == EXAMPLE_SCORE


def test_the_breakdown_reports_each_components_share_of_its_weight():
    """The components are the score's provenance, in points out of the weight."""
    result = productivity_score(**EXAMPLE_INPUTS)
    components = _by_name(result)

    assert components["completion"].points == 24.0
    assert components["deadline"].points == 18.0
    assert components["consistency"].points == 19.0
    assert components["focus"].points == 17.0
    assert [component.max_points for component in result.components] == [
        30.0,
        25.0,
        20.0,
        25.0,
    ]


def test_the_component_points_add_up_to_the_score_exactly():
    """The breakdown must add up to the headline number without rounding.

    Otherwise a user checking the arithmetic against the components finds a
    gap between the two figures the dashboard printed.
    """
    result = productivity_score(**EXAMPLE_INPUTS)

    assert sum(component.points for component in result.components) == result.score


def test_a_component_explains_what_it_earned():
    assert (
        _by_name(productivity_score(**EXAMPLE_INPUTS))["completion"].explanation
        == "80.0% of the completion target (30 points available)."
    )


def test_a_perfect_set_of_rates_scores_one_hundred():
    result = productivity_score(
        completion_rate=1.0,
        deadline_adherence=1.0,
        consistency=1.0,
        focus=1.0,
    )

    assert result.score == 100


def test_measured_zero_scores_zero_rather_than_being_unavailable():
    """Zero on all four components is a measurement, not a missing one.

    These are the users who have data and did not use it. The unavailable case
    is a different thing entirely, and is asserted separately below.
    """
    result = productivity_score(
        completion_rate=0.0,
        deadline_adherence=0.0,
        consistency=0.0,
        focus=0.0,
    )

    assert result.available is True
    assert result.score == 0
    assert result.reason_if_unavailable is None


def test_the_score_is_clamped_to_one_hundred_even_when_the_weights_overflow():
    """The clamp to 100 holds even for a weighting that overflows.

    The clamp is otherwise only reachable with weights the settings validator
    would reject, which is exactly why it is worth pinning: a caller passing
    an unvalidated mapping must still not be able to print 1000.
    """
    result = productivity_score(
        completion_rate=1.0,
        deadline_adherence=1.0,
        consistency=1.0,
        focus=1.0,
        weights=dict.fromkeys(DEFAULT_WEIGHTS, 250.0),
    )

    assert result.score == 100
    assert [component.max_points for component in result.components] == [
        250.0,
        250.0,
        250.0,
        250.0,
    ]


def test_a_nested_score_result_and_a_bare_rate_agree():
    """A nested sub-score and a bare rate are the same input seen two ways.

    The service passes each sub-score as a :class:`ScoreResult`, so the nested
    form and the bare rate must agree exactly. Here that is the brief's 78,
    with each component arriving as its own 0-100 result rather than as a 0-1
    rate.
    """
    nested = productivity_score(
        completion_rate=ScoreResult(score=80),
        deadline_adherence=ScoreResult(score=72),
        consistency=ScoreResult(score=95),
        focus=ScoreResult(score=68),
    )
    bare = productivity_score(**EXAMPLE_INPUTS)

    assert nested == bare


def test_a_missing_component_earns_zero_points_and_says_why():
    """A user with a completion history and no clock history still has a score.

    The components they have no evidence for contribute nothing, and say so.
    The explanation has to be there: a silent zero in the breakdown reads as
    "you scored 0 at deadlines", which is precisely the fabrication the brief
    forbids.
    """
    result = productivity_score(
        completion_rate=0.8,
        deadline_adherence=None,
        consistency=None,
        focus=None,
    )
    components = _by_name(result)

    assert result.available is True
    assert result.reason_if_unavailable is None
    assert result.score == 24
    for name in ("deadline", "consistency", "focus"):
        assert components[name].points == 0.0
        assert components[name].explanation == (
            f"Not counted: no {name} data recorded for this period."
        )
    assert components["completion"].points == 24.0


def test_a_partially_available_score_keeps_the_full_weight_of_what_was_measured():
    """One measured component out of four is worth its whole weight.

    The denominator stays 100, which is the number shown next to the score, so
    a single available component is not rescaled to fill the score on its own.
    """
    result = productivity_score(
        completion_rate=1.0,
        deadline_adherence=None,
        consistency=None,
        focus=None,
    )
    components = _by_name(result)

    assert result.score == 30
    assert components["completion"].max_points == 30.0
    assert components["deadline"].max_points == 25.0
    assert sum(component.max_points for component in result.components) == 100.0


def test_a_nested_result_that_is_unavailable_counts_as_a_missing_component():
    """A sub-score that declined to be calculated must not be re-read as a 0."""
    result = productivity_score(
        completion_rate=ScoreResult(score=80),
        deadline_adherence=ScoreResult(
            score=None,
            available=False,
            reason_if_unavailable=f"{NOT_ENOUGH_ACTIVITY}: nothing finished yet.",
        ),
        consistency=None,
        focus=None,
    )
    components = _by_name(result)

    assert result.score == 24
    assert components["deadline"].points == 0.0
    assert components["deadline"].explanation == (
        f"Not counted: {NOT_ENOUGH_ACTIVITY.lower()}: nothing finished yet."
    )


def test_a_score_with_no_measurable_component_is_unavailable_and_never_zero():
    """The headline case of the brief.

    An empty dashboard says "Not enough activity yet". It does not say "0%
    productivity".
    """
    result = productivity_score(
        completion_rate=None,
        deadline_adherence=None,
        consistency=None,
        focus=None,
    )

    assert result.score is None
    assert result.available is False
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)
    assert all(component.points == 0.0 for component in result.components)


def test_four_unavailable_sub_scores_leave_nothing_to_measure():
    """The same answer by the route the service actually takes.

    Every component arrives as an already-declined :class:`ScoreResult`, which
    is what ``AnalyticsService.productivity`` passes in.
    """
    declined = ScoreResult(
        score=None, available=False, reason_if_unavailable=f"{NOT_ENOUGH_ACTIVITY}: nothing."
    )
    result = productivity_score(
        completion_rate=declined,
        deadline_adherence=declined,
        consistency=declined,
        focus=declined,
    )

    assert result.score is None
    assert result.available is False


def test_a_custom_weighting_is_honoured_in_both_the_score_and_the_breakdown():
    """Weights are injected, not embedded, so a deployment can change them.

    A weighting that believes in deadlines can double their share without
    touching the formula. Completion at full marks against zero everywhere
    else is 50 of the 100 points this mapping defines.
    """
    weights = {"completion": 50.0, "deadline": 20.0, "consistency": 20.0, "focus": 10.0}
    result = productivity_score(
        completion_rate=1.0,
        deadline_adherence=0.0,
        consistency=0.0,
        focus=0.0,
        weights=weights,
    )
    components = _by_name(result)

    assert result.score == 50
    assert components["completion"].points == 50.0
    assert components["completion"].max_points == 50.0
    assert components["focus"].max_points == 10.0
    assert (
        components["completion"].explanation
        == "100.0% of the completion target (50 points available)."
    )
    assert sum(component.max_points for component in result.components) == 100.0


def test_a_weighting_that_omits_a_component_leaves_it_worth_nothing():
    """A partial mapping is honoured literally rather than being topped up.

    No missing weight is borrowed from the defaults, so the score can only
    ever be built from weights the caller actually asked for.
    """
    result = productivity_score(
        completion_rate=1.0,
        deadline_adherence=1.0,
        consistency=1.0,
        focus=1.0,
        weights={"completion": 100.0},
    )
    components = _by_name(result)

    assert result.score == 100
    assert components["deadline"].max_points == 0.0
    assert components["deadline"].points == 0.0


# -- consistency_score ------------------------------------------------------


def test_consistency_is_the_share_of_window_days_with_a_session():
    """5 active days of a 7-day window is 71.4%."""
    result = consistency_score(active_days=5, window_days=7, session_count=12)

    assert result.available is True
    assert result.score == 71
    assert result.components[0].points == 71.4286
    assert result.components[0].max_points == 100.0
    assert result.components[0].explanation == (
        "5 of 7 days had at least one work session (12 session(s) recorded)."
    )


def test_consistency_with_no_active_day_is_a_measured_zero():
    """Sessions were recorded, so the record exists and the answer is 0.

    The two cases are asserted separately, with and without a session, because
    conflating them is the failure the brief describes.
    """
    result = consistency_score(active_days=0, window_days=7, session_count=3)

    assert result.available is True
    assert result.score == 0
    assert result.reason_if_unavailable is None


def test_consistency_with_full_coverage_is_one_hundred():
    result = consistency_score(active_days=7, window_days=7, session_count=9)

    assert result.score == 100
    assert result.components[0].points == 100.0


def test_consistency_is_clamped_when_the_active_days_outnumber_the_window():
    """Over-counted rows must not produce a 140% consistency score."""
    result = consistency_score(active_days=9, window_days=7, session_count=11)

    assert result.score == 100
    assert result.components[0].points == 100.0


def test_consistency_is_unavailable_without_any_work_session():
    """Without a work session there is no record of which days were active.

    The metric is then unknown rather than zero: scoring such a user 0%
    consistent would be measuring the product, not the person.
    """
    result = consistency_score(active_days=7, window_days=7, session_count=0)

    assert result.score is None
    assert result.available is False
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)
    assert result.components == []


def test_consistency_over_an_empty_window_is_unavailable():
    result = consistency_score(active_days=0, window_days=0, session_count=4)

    assert result.score is None
    assert result.available is False
    assert result.reason_if_unavailable == f"{NOT_ENOUGH_ACTIVITY}: the period is empty."


# -- focus_score ------------------------------------------------------------


def test_focus_blends_a_session_length_share_with_a_follow_through_share():
    """Focus is 60% session length and 40% follow-through.

    45 minutes is the uninterrupted-block target, so an average session of 45
    earns the full 60 points of the length share, and 8 completed sessions
    against 2 reschedules is a follow-through of 8/10, or 32 of the 40.
    """
    result = focus_score(avg_session_minutes=45, completed_planned_sessions=8, interruptions=2)
    components = _by_name(result)

    assert result.available is True
    assert result.score == 92
    assert components["session_length"].points == 60.0
    assert components["session_length"].max_points == 60.0
    assert components["plan_followthrough"].points == 32.0
    assert components["plan_followthrough"].max_points == 40.0


def test_a_half_length_session_earns_half_the_length_points():
    """22.5 minutes against a 45 minute target is exactly half of the 60."""
    result = focus_score(avg_session_minutes=22.5, completed_planned_sessions=5, interruptions=5)
    components = _by_name(result)

    assert components["session_length"].points == 30.0
    assert components["plan_followthrough"].points == 20.0
    assert result.score == 50


def test_a_session_longer_than_the_target_does_not_score_better():
    """The length share saturates at 45 minutes.

    This is not a measure of stamina, and an eight-hour block must not
    out-score a 45-minute one. Both are 60 length points plus a follow-through
    of 3/4 of 40, which is 30.
    """
    at_target = focus_score(avg_session_minutes=45, completed_planned_sessions=3, interruptions=1)
    far_past = focus_score(avg_session_minutes=480, completed_planned_sessions=3, interruptions=1)

    assert _by_name(at_target)["session_length"].points == 60.0
    assert _by_name(far_past)["session_length"].points == 60.0
    assert at_target.score == far_past.score == 90


def test_focus_with_no_completed_sessions_is_unavailable_and_never_a_low_number():
    """A user who has run no timers gets "Not enough activity yet".

    Never a score of zero, which would read as "you never concentrate".
    """
    result = focus_score(avg_session_minutes=90, completed_planned_sessions=0, interruptions=4)

    assert result.score is None
    assert result.available is False
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)
    assert result.components == []


def test_focus_without_a_recorded_duration_keeps_the_follow_through_half():
    """The average duration needs a completed session to average.

    Without one the length share is not counted and says why. The follow-through
    share is still a real measurement of 5 against 5, or 20 of its 40 points.
    """
    result = focus_score(avg_session_minutes=None, completed_planned_sessions=5, interruptions=5)
    components = _by_name(result)

    assert result.available is True
    assert result.score == 20
    assert components["session_length"].points == 0.0
    assert components["session_length"].explanation == (
        "Not counted: no completed session recorded a duration."
    )
    assert components["plan_followthrough"].points == 20.0


def test_focus_without_reschedules_earns_the_full_follow_through_share():
    result = focus_score(avg_session_minutes=30, completed_planned_sessions=4, interruptions=0)
    components = _by_name(result)

    assert components["plan_followthrough"].points == 40.0
    assert result.score == 80


def test_a_focus_component_explains_the_measurement_it_made():
    """The brief's rule for this score, which the wording must honour.

    It is derived from recorded work-session behaviour, so neither explanation
    may imply it measured attention.
    """
    result = focus_score(avg_session_minutes=45, completed_planned_sessions=8, interruptions=2)
    components = _by_name(result)

    assert components["session_length"].explanation == (
        "Average session 45 min against a 45 min uninterrupted-block target."
    )
    assert components["plan_followthrough"].explanation == (
        "8 planned session(s) completed against 2 reschedule(s)."
    )


# -- deadline_adherence -----------------------------------------------------


def test_deadline_adherence_is_the_on_time_share_of_finished_work():
    """The brief's worked example: 18 on time, 3 late, a rate of 85.7%.

    The percentage is the rate; the score itself is that same figure rounded to
    the whole number the dashboard shows, which is 86.
    """
    result = deadline_adherence(on_time=18, late=3, still_overdue=0)
    components = _by_name(result)

    assert rate(18, 21) == 85.7143
    assert result.available is True
    assert result.score == 86
    assert components["on_time"].points == 85.7143
    assert components["on_time"].max_points == 100.0
    assert components["on_time"].explanation == (
        "18 of 21 completed task(s) finished by their due date."
    )


def test_deadline_adherence_of_eight_tenths_being_on_time_is_eighty():
    """The spec's other canonical figure: 8 of 10 completed on time is 80%."""
    result = deadline_adherence(on_time=8, late=2, still_overdue=0)

    assert result.score == 80
    assert _by_name(result)["on_time"].points == 80.0


def test_still_overdue_work_does_not_count_against_adherence():
    """A task due next month is not a missed deadline.

    Folding open overdue tasks into the denominator would drop the score every
    time the user planned further ahead, so they are reported as their own
    zero-point component instead.
    """
    without = deadline_adherence(on_time=8, late=2, still_overdue=0)
    with_open = deadline_adherence(on_time=8, late=2, still_overdue=100)
    components = _by_name(with_open)

    assert with_open.score == without.score == 80
    assert components["still_overdue"].points == 0.0
    assert components["still_overdue"].max_points == 0.0
    assert components["still_overdue"].explanation == (
        "100 task(s) are past due and still open; they do not count against "
        "adherence until they are finished."
    )


def test_every_finished_task_being_late_is_a_measured_zero():
    result = deadline_adherence(on_time=0, late=5, still_overdue=0)

    assert result.available is True
    assert result.score == 0
    assert result.reason_if_unavailable is None


def test_deadline_adherence_is_unavailable_when_nothing_has_been_finished():
    """A user with two completed tasks and no due dates on either has no record.

    "0% adherence" would then be invented from nothing, so the metric is
    reported as unavailable instead.
    """
    result = deadline_adherence(on_time=0, late=0, still_overdue=5)
    components = _by_name(result)

    assert result.score is None
    assert result.available is False
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)
    # The open overdue work is still reported, so the dashboard is not blank.
    assert components["still_overdue"].points == 0.0
    assert components["still_overdue"].max_points == 0.0


# -- estimation_accuracy ----------------------------------------------------


def test_estimation_reports_the_briefs_worked_example():
    """Estimated 60, actual 80 — a 20 minute miss, under-estimated.

    With ``(90, 150)`` alongside: signed errors of -20 and -60, so a mean bias
    of -40 (both estimates ran *below* the time taken), a mean absolute miss
    of 40, and percentage errors of 33.33% and 66.67% averaging exactly 50.
    """
    result = estimation_accuracy(pairs=[(60, 80), (90, 150)])

    assert result.available is True
    assert result.sample_count == 2
    assert result.absolute_error == 40.0
    assert result.percentage_error == 50.0
    assert result.median_error == 40.0
    assert result.bias == -40.0
    assert result.under_estimation_rate == 100.0
    assert result.over_estimation_rate == 0.0


def test_the_bias_sign_and_the_under_estimation_rate_tell_the_same_story():
    """``bias = estimated - actual``, so negative means under-estimating.

    The two fields must never disagree: a positive bias printed beside a 100%
    under-estimation rate would be the rejected opposite convention. Here
    ``(120, 60)`` over-shot by 60 and ``(90, 150)`` under-shot by 60, so the
    signed errors cancel and the absolute misses do not.
    """
    result = estimation_accuracy(pairs=[(120, 60), (90, 150)])

    assert result.bias == 0.0
    assert result.absolute_error == 60.0
    assert result.median_error == 60.0
    assert result.percentage_error == 58.3333
    assert result.under_estimation_rate == 50.0
    assert result.over_estimation_rate == 50.0


def test_over_estimating_habits_produce_a_positive_bias():
    """The mirror of the under-estimation case.

    100 estimated against 60 taken is +40, and both pairs over-shot.
    """
    result = estimation_accuracy(pairs=[(100, 60), (80, 40)])

    assert result.bias == 40.0
    assert result.under_estimation_rate == 0.0
    assert result.over_estimation_rate == 100.0
    assert result.absolute_error == 40.0


def test_a_forecast_made_on_the_nose_reports_a_zero_bias():
    result = estimation_accuracy(pairs=[(60, 60), (45, 45)])

    assert result.bias == 0.0
    assert result.absolute_error == 0.0
    assert result.percentage_error == 0.0
    assert result.median_error == 0.0
    assert result.under_estimation_rate == 0.0
    assert result.over_estimation_rate == 0.0


def test_estimation_explains_the_bias_in_words():
    result = estimation_accuracy(pairs=[(60, 80), (90, 150)])
    components = {component.name: component for component in result.components}

    assert components["absolute_error"].explanation == (
        "Across 2 estimated task(s) the average miss was 40.0 minutes, either way."
    )
    assert components["bias"].explanation == (
        "Estimates ran 40.0 minutes below the time actually taken, so 100% of "
        "tasks were under-estimated."
    )


def test_a_pair_with_no_estimate_is_dropped_and_counted_out_of_the_sample():
    """A task estimated at zero minutes has no percentage to divide by.

    It is excluded from the measurement, and ``sample_count`` describes what
    was measured rather than what was supplied.
    """
    result = estimation_accuracy(pairs=[(0, 30), (60, 80)])

    assert result.sample_count == 1
    assert result.absolute_error == 20.0
    assert result.percentage_error == 33.3333
    assert result.bias == -20.0
    assert result.median_error == 20.0


def test_a_negative_estimate_is_dropped_for_the_same_reason():
    result = estimation_accuracy(pairs=[(-5, 10), (60, 80)])

    assert result.sample_count == 1
    assert result.absolute_error == 20.0


def test_estimation_is_unavailable_with_no_pairs_at_all():
    """Never a flawless 0% error for a user who has never estimated anything."""
    result = estimation_accuracy(pairs=[])

    assert result.available is False
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)
    assert result.sample_count == 0
    assert result.components == []
    assert result.absolute_error is None
    assert result.percentage_error is None
    assert result.bias is None
    assert result.median_error is None
    assert result.under_estimation_rate is None
    assert result.over_estimation_rate is None


def test_estimation_is_unavailable_when_every_estimate_is_zero():
    result = estimation_accuracy(pairs=[(0, 30), (0, 45)])

    assert result.available is False
    assert result.sample_count == 0
    assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY)


# -- The shared unavailable message -----------------------------------------


def test_every_unavailable_path_uses_the_one_shared_message():
    """One user-facing phrase, not one per function.

    The wording is a product decision and belongs in a single constant. If two
    functions invented their own, the dashboard would show two different
    answers to the same question — "Not enough activity yet" beside something
    else entirely — for a user who simply has not used the product yet.
    """
    declined = ScoreResult(
        score=None, available=False, reason_if_unavailable=f"{NOT_ENOUGH_ACTIVITY}: nothing."
    )
    unavailable = [
        productivity_score(
            completion_rate=None,
            deadline_adherence=None,
            consistency=None,
            focus=None,
        ),
        productivity_score(
            completion_rate=declined,
            deadline_adherence=declined,
            consistency=declined,
            focus=declined,
        ),
        consistency_score(active_days=0, window_days=7, session_count=0),
        consistency_score(active_days=0, window_days=0, session_count=1),
        focus_score(avg_session_minutes=30, completed_planned_sessions=0, interruptions=1),
        deadline_adherence(on_time=0, late=0, still_overdue=2),
    ]

    for result in unavailable:
        assert result.available is False, result
        assert result.score is None, result
        assert result.reason_if_unavailable.startswith(NOT_ENOUGH_ACTIVITY), result


def test_the_shared_message_is_the_briefs_wording():
    assert NOT_ENOUGH_ACTIVITY == "Not enough activity yet"
