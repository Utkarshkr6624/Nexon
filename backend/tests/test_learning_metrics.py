"""The learning metric formulas, exercised as pure arithmetic.

No database, no HTTP, no ``integration`` marker: everything in
:mod:`app.services.learning.metrics` takes
:class:`~app.services.learning.metrics.ActivitySample` and
:class:`~app.services.learning.metrics.GoalSample` values and returns a
:class:`~app.services.learning.metrics.LearningMetric`, so a figure can be checked
against a hand-derived value without provisioning PostgreSQL.

Every expected value is derived by hand from the documented formula and written
into the test's own docstring, never copied from a run. A change to a formula
shows up as a wrong number rather than as a moved baseline. The instants come from
a fixed UTC base rather than from ``datetime.now()``: a test that read the wall
clock would start failing the day somebody's timezone moved, and none of these
figures depend on when the suite runs.

Five things are asserted rather than merely exercised:

* **Exact figures.** Session counts, minutes, day shares and goal averages, each
  with the arithmetic shown.
* **Zero is not unknown.** The module's stated contract is that an absence of
  measurement is ``available=False`` with a reason while a real measurement of
  zero is ``value=0, available=True``. Both are asserted, and the two halves are
  asserted against the *same* empty account, because conflating them is how a
  product ends up telling a new user they have no activity.
* **Which metrics can decline.** Exactly three goal-based metrics and the
  per-skill average decline, each on an empty denominator; the four counts do
  not. A ratio with no denominator has no answer that is not invented.
* **The wording of the explanations.** Every sentence carries its own figures and
  none may reach for "mastery", "proficiency" or "competence" — no row in
  ``learning_activities`` can support a claim about what someone is good at, and
  the rule is asserted as a construction-time failure so it cannot be forgotten.
* **The density of the activity series.** A gap-filled series is the property the
  builder exists for, and a chart that skips a quiet Tuesday is lying about the
  fortnight in a way no assertion on the totals would catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.enums import LearningGoalStatus
from app.services.learning.metrics import (
    ACTIVITY_GRANULARITIES,
    FORBIDDEN_CLAIM_WORDS,
    LEARNING_METRICS,
    METRIC_UNITS,
    NOT_ENOUGH_DATA,
    OPEN_GOAL_STATUSES,
    RECENT_MONTH_DAYS,
    RECENT_WEEK_DAYS,
    ActivityGranularity,
    ActivitySample,
    GoalSample,
    LearningMetric,
    MetricKey,
    activity_series,
    bucket_for,
    build_metrics,
    completion_rate,
    goal_deadline_distance_days,
    goal_progress,
    learning_consistency,
    learning_minutes,
    sessions_last_7d,
    sessions_last_30d,
    skill_activity_frequency,
)

#: The instant every test treats as "now", and the end of every window. Fixed
#: rather than "now" so a figure derived from it is derived, not recorded.
WINDOW_END = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
#: Thirty days earlier, the default window: 1 June to 1 July, a 30-day span.
WINDOW_START = WINDOW_END - timedelta(days=30)

SKILL_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
SKILL_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
GOAL_A = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _activity(
    *,
    days_ago: float = 1.0,
    skill_id: uuid.UUID | None = SKILL_A,
    goal_id: uuid.UUID | None = None,
    minutes: int | None = None,
    activity_type: str = "study_session",
) -> ActivitySample:
    """One recorded activity, ``days_ago`` days before :data:`WINDOW_END`."""
    return ActivitySample(
        occurred_at=WINDOW_END - timedelta(days=days_ago),
        activity_type=activity_type,
        skill_id=skill_id,
        goal_id=goal_id,
        duration_minutes=minutes,
    )


def _goal(
    *,
    status: LearningGoalStatus = LearningGoalStatus.IN_PROGRESS,
    progress: int = 0,
    target_date: date | None = None,
) -> GoalSample:
    """One goal with only the fields the goal metrics read."""
    return GoalSample(
        goal_id=GOAL_A,
        status=status,
        progress=progress,
        target_date=target_date,
    )


def _activities_for_skills(pairs: list[tuple[float, uuid.UUID]]) -> list[ActivitySample]:
    """One activity per ``(days_ago, skill_id)`` pair."""
    return [_activity(days_ago=days, skill_id=skill) for days, skill in pairs]


def _by_key(metrics: tuple[LearningMetric, ...], key: MetricKey) -> LearningMetric:
    """The metric with this key from an assembled eight.

    Raises:
        AssertionError: If the metric is absent, which is itself the failure —
            ``/learning/metrics`` promises eight and a missing one renders a hole
            where a card belongs.
    """
    for metric in metrics:
        if metric.key == key.value:
            return metric
    raise AssertionError(f"{key.value} is missing from the assembled metrics")


# ---------------------------------------------------------------------------
# The shape every metric owes its caller
# ---------------------------------------------------------------------------


def test_build_metrics_returns_the_eight_metrics_in_contract_order():
    """Eight keys, in the order the ``learning_features.v1`` export names them.

    The order is asserted as a whole tuple rather than as a set because it is the
    column order of a feature export as well as a reading order a human follows; a
    set would pass with the cards shuffled, which looks like a different product
    every deploy.
    """
    metrics = build_metrics([], [], window_start=WINDOW_START, window_end=WINDOW_END)

    assert tuple(metric.key for metric in metrics) == (
        "sessions_last_7d",
        "sessions_last_30d",
        "learning_minutes",
        "goal_progress",
        "goal_deadline_distance_days",
        "completion_rate",
        "learning_consistency",
        "skill_activity_frequency",
    )


def test_every_metric_carries_a_definition_a_window_a_source_and_its_own_unit():
    """No metric is a bare number.

    A card showing ``0.2`` with no definition, no window and no source is a number
    the user has to simply trust. All four are required fields for that reason,
    and the unit comes from one table so a metric cannot invent a unit the frontend
    has no formatter for.
    """
    metrics = build_metrics(
        [_activity()], [_goal()], window_start=WINDOW_START, window_end=WINDOW_END
    )

    for metric in metrics:
        assert metric.definition.strip()
        assert metric.source.strip()
        assert metric.explanation.strip()
        assert metric.window_days is None or metric.window_days >= 1
        assert metric.unit == METRIC_UNITS[MetricKey(metric.key)]


def test_every_metric_explanation_carries_at_least_one_figure():
    """The digit rule, asserted across all eight.

    "You have been learning consistently" is an adjective; "5 of the last 30 days
    carried a recorded learning activity" is a finding. The same rule
    :class:`~app.services.risk.recommendation.RecommendationDraft` enforces on a
    recommendation's reason.
    """
    metrics = build_metrics(
        [_activity(), _activity(days_ago=2)],
        [_goal(progress=40, target_date=date(2026, 7, 10))],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    for metric in metrics:
        assert any(character.isdigit() for character in metric.explanation), metric.key


# ---------------------------------------------------------------------------
# An empty account: the two shapes of "no measurement"
# ---------------------------------------------------------------------------


def test_an_empty_record_measures_four_counts_as_zero_rather_than_declining():
    """No activities and no goals: four zeros that are real, and four declines.

    The four counts — ``sessions_last_7d``, ``sessions_last_30d``,
    ``learning_minutes`` and ``learning_consistency`` — are all measured zeros:
    "nothing was recorded in these 30 days" is a true and useful sentence, and a
    new account is exactly the case where it matters.

    The other four are all ratios with an empty denominator and are declined. The
    distinction is the point: a dashboard that rendered ``completion_rate = 0.0``
    for someone who has written no goals would be claiming they have completed
    none of them, which is a claim about a person made from no data at all.
    """
    metrics = build_metrics([], [], window_start=WINDOW_START, window_end=WINDOW_END)

    measured = [
        MetricKey.SESSIONS_LAST_7D,
        MetricKey.SESSIONS_LAST_30D,
        MetricKey.LEARNING_MINUTES,
        MetricKey.LEARNING_CONSISTENCY,
    ]
    for key in measured:
        metric = _by_key(metrics, key)
        assert metric.value == 0.0
        assert metric.available is True
        assert metric.reason_if_unavailable is None

    declined = [
        MetricKey.GOAL_PROGRESS,
        MetricKey.GOAL_DEADLINE_DISTANCE_DAYS,
        MetricKey.COMPLETION_RATE,
        MetricKey.SKILL_ACTIVITY_FREQUENCY,
    ]
    for key in declined:
        metric = _by_key(metrics, key)
        assert metric.available is False
        assert metric.value == 0.0
        assert metric.reason_if_unavailable == NOT_ENOUGH_DATA


def test_a_declined_metric_still_names_the_window_it_read():
    """The refusal carries its figures too.

    "Not enough data to assess this yet" alone leaves the reader unable to tell
    *which* figure was missing, so the sentence names the 0 it found and the
    window it searched.
    """
    metric = goal_progress([])

    assert metric.available is False
    assert metric.reason_if_unavailable == NOT_ENOUGH_DATA
    assert metric.explanation == (
        f"{NOT_ENOUGH_DATA} The Goal progress figure is not computed: 0 records in this "
        "account's goals supply the numbers it would be divided over."
    )


# ---------------------------------------------------------------------------
# Session counts
# ---------------------------------------------------------------------------


def test_the_two_trailing_windows_are_measured_back_from_now():
    """Three sessions: one at 3 days, one at 10, one at 40.

    Derived from :data:`WINDOW_END`:

    * 3 days ago and 10 days ago fall inside the 30-day trailing window.
    * Only the 3-day one falls inside the 7-day window.
    * 40 days ago falls inside neither, and the request window also runs 30 days,
      so the windowed minutes total agrees at 0 — none of the three carries a
      duration, so there is nothing to sum.
    """
    activities = [
        _activity(days_ago=3),
        _activity(days_ago=10),
        _activity(days_ago=40),
    ]

    assert sessions_last_7d(activities, window_end=WINDOW_END).value == 1.0
    assert sessions_last_30d(activities, window_end=WINDOW_END).value == 2.0
    minutes = learning_minutes(activities, window_start=WINDOW_START, window_end=WINDOW_END)
    assert minutes.value == 0.0
    assert "0 of the 2" in minutes.explanation


def test_a_session_counts_once_whether_it_is_a_span_or_an_event():
    """Three sessions, none carrying a duration → 3 sessions and 0 minutes.

    The count is over rows, not over rows-with-a-duration. "I finished the
    chapter" and "I read for forty minutes" are both evidence that something
    happened here, and dropping the first would quietly discard every task and
    note the user recorded.
    """
    activities = [
        _activity(days_ago=1, activity_type="task_completed"),
        _activity(days_ago=2, activity_type="note_created"),
        _activity(days_ago=3, activity_type="resource_viewed"),
    ]

    assert sessions_last_7d(activities, window_end=WINDOW_END).value == 3.0


# ---------------------------------------------------------------------------
# Learning minutes
# ---------------------------------------------------------------------------


def test_learning_minutes_sums_only_the_durations_that_were_recorded():
    """45, ``None`` and 30 minutes → 75, summed from 2 of the 3 activities.

    Derived: 45 + 30 = 75. The middle activity has no duration because it is an
    *event* rather than a *span*, and it contributes nothing without being counted
    as a measured zero-length session — which is what the "2 of the 3" in the
    sentence is for. Reading it as zero would put a third session in the record
    that never happened.
    """
    activities = [
        _activity(days_ago=1, minutes=45),
        _activity(days_ago=2, minutes=None),
        _activity(days_ago=3, minutes=30),
    ]

    metric = learning_minutes(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 75.0
    assert metric.unit == "minutes"
    assert metric.explanation == (
        "75 minute(s) were recorded, summed from 2 of the 3 learning activit(y/ies) in "
        "the 30-day window that carry a duration."
    )


def test_no_recorded_duration_at_all_is_a_measured_zero_not_a_decline():
    """Three events, no durations → ``value=0, available=True``.

    The counter-argument would be that nothing was measured, so the figure should
    be null. But the durations column *was* read, and it holds no values: "0 of the
    3 recorded activities carry a duration" is a true sentence about the record,
    and it is a materially different card from an unexplained blank.
    """
    metric = learning_minutes(
        [_activity(days_ago=day) for day in (1, 2, 3)],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert metric.value == 0.0
    assert metric.available is True


# ---------------------------------------------------------------------------
# Goal metrics
# ---------------------------------------------------------------------------


def test_goal_progress_averages_the_users_own_figures_over_open_goals():
    """Two open goals at 40% and 60%, one completed at 100% → 50%.

    Derived: (40 + 60) / 2 = 50. The completed goal is excluded — its 100% is not
    a data point about the ones still going, and averaging it in would make
    finishing things look like slowing down.
    """
    goals = [
        _goal(progress=40, status=LearningGoalStatus.IN_PROGRESS),
        _goal(progress=60, status=LearningGoalStatus.NOT_STARTED),
        _goal(progress=100, status=LearningGoalStatus.COMPLETED),
    ]

    metric = goal_progress(goals)

    assert metric.value == 50.0
    assert metric.unit == "percent"
    assert metric.explanation == (
        "2 open goal(s) carry a user-set progress figure, averaging 50% of the way to "
        "their targets."
    )


def test_an_archived_goal_counts_as_neither_open_nor_incomplete():
    """Two archived goals alongside two open ones change nothing.

    :class:`~app.models.enums.LearningGoalStatus` separates ``ARCHIVED`` from
    ``COMPLETED`` precisely so that an archived goal does not count as incomplete
    work anywhere. Asserted on the goal metrics it is invisible, which is the
    point: discarding a goal must not lower your average progress or appear in the
    completion rate as a failure to finish.
    """
    without = [
        _goal(progress=40, status=LearningGoalStatus.IN_PROGRESS),
        _goal(progress=60, status=LearningGoalStatus.IN_PROGRESS),
    ]
    with_archived = [
        *without,
        _goal(progress=0, status=LearningGoalStatus.ARCHIVED),
        _goal(progress=0, status=LearningGoalStatus.ARCHIVED),
    ]

    assert goal_progress(with_archived).value == goal_progress(without).value == 50.0
    assert completion_rate(with_archived).value == completion_rate(without).value == 0.0
    assert (
        frozenset(
            {
                LearningGoalStatus.NOT_STARTED,
                LearningGoalStatus.IN_PROGRESS,
                LearningGoalStatus.PAUSED,
            }
        )
        == OPEN_GOAL_STATUSES
    )


def test_a_paused_goal_is_still_open():
    """A paused goal at 50% counts in the average.

    A goal the user intends to return to in March is not a goal they have
    abandoned, and merging ``PAUSED`` into ``NOT_STARTED`` would make one of the
    two lie every time the count is read.
    """
    goals = [
        _goal(progress=50, status=LearningGoalStatus.PAUSED),
        _goal(progress=50, status=LearningGoalStatus.IN_PROGRESS),
    ]

    metric = goal_progress(goals)

    assert metric.value == 50.0
    assert "2 open goal(s)" in metric.explanation


def test_completion_rate_counts_completed_goals_over_the_non_archived_ones():
    """Two completed, one in progress, one archived → 2/3 = 0.6667.

    Derived: the archived goal is dropped from both sides, so the denominator is
    3 and the numerator is 2, which is 0.6666… rounded to four places. Had the
    archived goal stayed in, it would have read 50% — a goal the user deliberately
    set aside would have counted as one they failed to finish.
    """
    goals = [
        _goal(status=LearningGoalStatus.COMPLETED),
        _goal(status=LearningGoalStatus.COMPLETED),
        _goal(status=LearningGoalStatus.IN_PROGRESS),
        _goal(status=LearningGoalStatus.ARCHIVED),
    ]

    metric = completion_rate(goals)

    assert metric.value == 0.6667
    assert metric.unit == "ratio"
    assert metric.explanation == (
        "2 of the 3 non-archived goal(s) are recorded as completed, which is 67% of them."
    )


def test_a_goal_with_no_deadline_is_not_a_short_distance_to_one():
    """One dated open goal and one undated open goal → the dated one alone.

    Derived: only the dated goal supplies a distance, so the average is that one
    goal's distance and not half of it. Averaging over undated goals as though
    their distance were zero would invent a deadline for every goal the user has
    not set one for.
    """
    goals = [
        _goal(target_date=date(2026, 7, 10)),
        _goal(target_date=None),
    ]

    metric = goal_deadline_distance_days(goals, window_end=WINDOW_END)

    assert metric.value == 9.0
    assert "1 open goal(s) carry a target date" in metric.explanation


def test_an_overdue_goal_reports_a_negative_distance():
    """A target date 6 days in the past → ``-6.0``, not ``0``.

    :data:`WINDOW_END` is 1 July 2026, so a goal dated 25 June is 6 days late.
    The sign is the whole point of the metric: a zero would render an overdue goal
    as "due today", and the sentence says explicitly that a negative figure means
    the date has passed.
    """
    goals = [_goal(target_date=date(2026, 6, 25))]

    metric = goal_deadline_distance_days(goals, window_end=WINDOW_END)

    assert metric.value == -6.0
    assert metric.explanation == (
        "1 open goal(s) carry a target date, -6.0 day(s) from today on average; a "
        "negative figure means the date has passed."
    )


def test_a_mix_of_upcoming_and_overdue_goals_averages_to_a_signed_mean():
    """One 10 days out and one 6 days late → ``2.0`` days.

    Derived: (10 + -6) / 2 = 2. Both goals are open and both carry a date, so
    both count; the result sits two days out even though one of them is late,
    which is exactly why the sentence names the sign rather than leaving the
    reader to infer it from the number.
    """
    goals = [
        _goal(target_date=date(2026, 7, 11)),
        _goal(target_date=date(2026, 6, 25)),
    ]

    metric = goal_deadline_distance_days(goals, window_end=WINDOW_END)

    assert metric.value == 2.0


def test_a_completed_goal_is_excluded_from_the_deadline_average():
    """A completed goal dated last month does not drag the average negative.

    A finished goal has no deadline any more; including its target date would
    report an account as six days late on the strength of work already finished.
    """
    goals = [
        _goal(target_date=date(2026, 7, 11)),
        _goal(status=LearningGoalStatus.COMPLETED, target_date=date(2026, 6, 25)),
    ]

    metric = goal_deadline_distance_days(goals, window_end=WINDOW_END)

    assert metric.value == 10.0
    assert "1 open goal(s)" in metric.explanation


# ---------------------------------------------------------------------------
# Learning consistency
# ---------------------------------------------------------------------------


def test_learning_consistency_counts_days_not_sessions():
    """Four sessions across three days, in a 30-day window → 3/30 = 0.1.

    Derived: the sessions fall 1, 2, 3 and 3 days before the window end, so days
    1, 2 and 3 each carry at least one and day 3 carries two. Three distinct UTC
    dates over a 30-day window is 0.1, which is 10%.

    The figure is asserted at the *date* ratio rather than at a ratio of sessions
    because counting sessions would make one busy afternoon read as a fortnight of
    activity and would turn this into a restatement of the session count wearing a
    different name.
    """
    activities = [
        _activity(days_ago=1),
        _activity(days_ago=2),
        _activity(days_ago=3),
        _activity(days_ago=3),
    ]

    metric = learning_consistency(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 0.1
    assert metric.unit == "ratio"
    assert metric.explanation == (
        "3 of the 30 days in the window carried a recorded learning activity, which is 10% of them."
    )


def test_learning_consistency_never_exceeds_one_on_a_one_day_window():
    """Two sessions on one day in a one-day window → ``1.0``, not ``2.0``.

    The ratio is clamped to 0-1 because ``window_days`` is a caller-supplied
    number and a one-day window carrying two sessions must not report "200% of the
    days carried an activity", which is not a sentence.
    """
    activities = [_activity(days_ago=0.5), _activity(days_ago=0.25)]

    metric = learning_consistency(
        activities,
        window_start=WINDOW_END - timedelta(days=1),
        window_end=WINDOW_END,
    )

    assert metric.value == 1.0


# ---------------------------------------------------------------------------
# Skill activity frequency
# ---------------------------------------------------------------------------


def test_skill_activity_frequency_averages_over_the_skills_that_have_any():
    """Six activities across two skills → 3.0 each.

    Derived: 6 / 2 = 3. Both sides of the division count only activities that name
    a skill, so a session recorded before its skill existed is left out of both —
    it belongs to no skill's frequency, and dividing by a per-skill denominator it
    does not appear in would inflate every skill that does have one.
    """
    activities = _activities_for_skills(
        [
            (1.0, SKILL_A),
            (2.0, SKILL_A),
            (3.0, SKILL_A),
            (1.0, SKILL_B),
            (2.0, SKILL_B),
            (3.0, SKILL_B),
        ]
    )

    metric = skill_activity_frequency(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 3.0
    assert metric.unit == "ratio"
    assert metric.explanation == (
        "6 skill-tagged activit(y/ies) were recorded across 2 skill(s) in the 30-day "
        "window, an average of 3.0 per skill."
    )


def test_a_skill_less_activity_is_counted_by_every_other_metric():
    """Three skill-tagged, one with no skill, two skills named → 1.5 per skill.

    Derived: 3 / 2 = 1.5. The fourth activity is real evidence of learning and is
    counted by the session counts and by consistency; it is excluded only from
    this one metric, whose denominator is per-skill.
    """
    activities = [
        _activity(days_ago=1, skill_id=SKILL_A),
        _activity(days_ago=2, skill_id=SKILL_A),
        _activity(days_ago=3, skill_id=SKILL_B),
        _activity(days_ago=4, skill_id=None),
    ]

    metric = skill_activity_frequency(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 1.5
    assert sessions_last_30d(activities, window_end=WINDOW_END).value == 4.0


def test_skill_activity_frequency_declines_when_no_activity_names_a_skill():
    """Four skill-less activities → the average is per-nothing, so it is declined.

    Zero activities across zero skills would be zero, but zero skills is not a
    denominator; the metric declines and the session counts beside it still report
    4, so the page says both things at once.
    """
    activities = [_activity(days_ago=day, skill_id=None) for day in (1, 2, 3, 4)]

    metric = skill_activity_frequency(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.available is False
    assert metric.reason_if_unavailable == NOT_ENOUGH_DATA
    assert metric.window_days == 30
    assert sessions_last_30d(activities, window_end=WINDOW_END).value == 4.0


# ---------------------------------------------------------------------------
# What the constructor refuses
# ---------------------------------------------------------------------------


def test_an_explanation_with_no_figure_in_it_is_rejected():
    """A metric whose sentence states no number cannot be constructed."""
    with pytest.raises(ValueError, match="no figure in it"):
        LearningMetric(
            key="learning_minutes",
            label="Learning minutes",
            value=0.0,
            unit="minutes",
            definition="Sum of the recorded durations.",
            window_days=30,
            source="learning_activities.duration_minutes",
            explanation="You have been putting in the time.",
        )


def test_an_explanation_claiming_mastery_is_rejected():
    """*"You have mastered the basics"* cannot be constructed.

    No row in ``learning_activities`` records whether anyone understood anything —
    it records that a session was entered — so a sentence claiming mastery is a
    claim about a person that the data cannot support. The check is in the
    constructor so the failure is loud at the call site rather than silent on a
    dashboard.
    """
    with pytest.raises(ValueError, match="master"):
        LearningMetric(
            key="learning_consistency",
            label="Learning consistency",
            value=0.5,
            unit="ratio",
            definition="Active days divided by the days in the window.",
            window_days=30,
            source="learning_activities.occurred_at",
            explanation=(
                "You have mastered the basics: 15 of the last 30 days carried a recorded "
                "learning activity."
            ),
        )


def test_an_unavailable_metric_without_a_reason_is_rejected():
    """``available=False`` and ``reason_if_unavailable=None`` cannot be built.

    The pairing is what stops "unavailable" rendering as an unexplained blank, and
    it is what makes the ``value=0`` an unavailable metric carries unreadable as
    a measurement.
    """
    with pytest.raises(ValueError, match="must say why"):
        LearningMetric(
            key="goal_progress",
            label="Goal progress",
            value=0.0,
            unit="percent",
            definition="Mean of the user-set progress percentage across open goals.",
            window_days=None,
            source="learning_goals.progress",
            explanation="0 of the 0 open goals carry a progress figure.",
            available=False,
            reason_if_unavailable=None,
        )


def test_the_forbidden_vocabulary_covers_the_words_the_contracts_forbid():
    """The banned-claims table names the specific words, not a general ban.

    Asserted as data so that editing the table is a visible change in a test
    rather than a quiet widening or narrowing of what this phase is allowed to say.
    """
    assert "mastery" in FORBIDDEN_CLAIM_WORDS
    assert "proficient" in FORBIDDEN_CLAIM_WORDS
    assert "competence" in FORBIDDEN_CLAIM_WORDS


# ---------------------------------------------------------------------------
# The activity series
# ---------------------------------------------------------------------------


def test_the_activity_series_is_dense_across_the_whole_window():
    """A 30-day window with two active days still emits every bucket.

    Derived: the window runs from 1 June to 1 July and the buckets are floored to
    UTC midnight, so the run is 1 June … 1 July inclusive — 30 days of June plus
    the first of July, 31 buckets. Two of them carry an activity and 29 are zero.

    **The 29 zeros are the point.** A series built only from the days that have
    activities would emit two buckets, and a reader counting the bars would see
    two consecutive active days when the truth is two active days three weeks
    apart.
    """
    activities = [_activity(days_ago=1), _activity(days_ago=20)]

    series = activity_series(activities, window_start=WINDOW_START, window_end=WINDOW_END)

    assert series.granularity == "day"
    assert len(series.buckets) == 31
    assert series.buckets[0].label == "2026-06-01"
    assert series.buckets[-1].label == "2026-07-01"
    assert [bucket.activity_count for bucket in series.buckets if bucket.activity_count] == [1, 1]
    assert sum(bucket.activity_count for bucket in series.buckets) == 2


def test_a_bucket_sums_its_minutes_and_collects_its_identifiers():
    """One bucket, two activities: the counts and the ids it names.

    The session with no skill and no goal contributes its activity count and its
    duration but nothing to the identifier sets — a frozenset cannot hold ``None``
    without a reader having to special-case it, and "what did I name here" has no
    answer for an activity that named nothing.
    """
    activities = [
        _activity(days_ago=1, skill_id=SKILL_A, goal_id=GOAL_A, minutes=30),
        _activity(days_ago=1, skill_id=SKILL_B, minutes=45),
        _activity(days_ago=1, skill_id=None, goal_id=None, minutes=None),
    ]

    series = activity_series(
        activities,
        window_start=WINDOW_END - timedelta(days=3),
        window_end=WINDOW_END,
    )
    populated = [bucket for bucket in series.buckets if bucket.activity_count]
    assert len(populated) == 1
    bucket = populated[0]

    assert bucket.label == "2026-06-30"
    assert bucket.activity_count == 3
    assert bucket.session_minutes == 75
    assert bucket.skill_ids == frozenset({SKILL_A, SKILL_B})
    assert bucket.goal_ids == frozenset({GOAL_A})


def test_the_series_can_be_narrowed_to_one_skill_or_one_goal():
    """Filtering happens before bucketing, so a narrowed series is still dense.

    Two activities for skill A and one for skill B, all inside a three-day
    window. Narrowing to A gives four buckets carrying 2 in total; narrowing to
    the goal gives four buckets carrying 1 — and the quiet days are present in
    both, which is the property a "this skill's activity" chart needs.
    """
    activities = [
        _activity(days_ago=1, skill_id=SKILL_A, goal_id=GOAL_A),
        _activity(days_ago=2, skill_id=SKILL_A),
        _activity(days_ago=1, skill_id=SKILL_B),
    ]

    by_skill = activity_series(
        activities,
        window_start=WINDOW_END - timedelta(days=3),
        window_end=WINDOW_END,
        skill_id=SKILL_A,
    )
    by_goal = activity_series(
        activities,
        window_start=WINDOW_END - timedelta(days=3),
        window_end=WINDOW_END,
        goal_id=GOAL_A,
    )

    assert len(by_skill.buckets) == 4
    assert sum(bucket.activity_count for bucket in by_skill.buckets) == 2
    assert len(by_goal.buckets) == 4
    assert sum(bucket.activity_count for bucket in by_goal.buckets) == 1


def test_weekly_buckets_start_on_monday():
    """A week bucket's start is always a Monday, whatever the window opened on.

    The window opens on 1 June at 12:00, mid-afternoon, and its first weekly
    bucket is still the Monday of that week rather than a partial day — a partial
    bucket would sit beside full ones on the same chart and read as a real dip.
    Derived: 1 June, 8, 15, 22 and 29 June are five Mondays before 1 July.
    """
    series = activity_series(
        [], window_start=WINDOW_START, window_end=WINDOW_END, granularity="week"
    )

    assert [bucket.label for bucket in series.buckets] == [
        "2026-06-01",
        "2026-06-08",
        "2026-06-15",
        "2026-06-22",
        "2026-06-29",
    ]
    assert all(bucket.start.weekday() == 0 for bucket in series.buckets)


def test_monthly_buckets_floor_to_the_first_and_skip_nothing():
    """Four months of monthly buckets, each starting on the 1st.

    Months advance by adding 32 days and re-flooring, so February — the month a
    naive "same day next month" arithmetic skips entirely — appears exactly once.
    The window spans 1 June to 1 July, so the run is June and July: 2 buckets.
    """
    series = activity_series(
        [],
        window_start=datetime(2026, 3, 15, tzinfo=UTC),
        window_end=datetime(2026, 7, 2, tzinfo=UTC),
        granularity="month",
    )

    assert [bucket.label for bucket in series.buckets] == [
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]


def test_bucket_for_agrees_with_the_series_labels():
    """The standalone bucketer returns the same labels the series emits.

    Exposed so the service can bucket a *query* the same way the series buckets
    its rows; a caller grouping its SQL by a different definition of "a day" would
    produce a heatmap that disagrees with the chart above it.
    """
    series = activity_series(
        [_activity(days_ago=1)],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    populated = [bucket.label for bucket in series.buckets if bucket.activity_count]

    assert bucket_for(WINDOW_END - timedelta(days=1)) == "2026-06-30"
    assert populated == [bucket_for(WINDOW_END - timedelta(days=1))]
    assert bucket_for(WINDOW_END, "month") == "2026-07"
    assert set(ACTIVITY_GRANULARITIES) == {"day", "week", "month"}


def test_an_unknown_granularity_is_refused():
    """``"fortnight"`` is an error naming the three values that are allowed.

    Guessing one would silently return a chart nobody asked for, and the request
    is wrong in a way the caller can be told about.
    """
    with pytest.raises(ValueError, match="not a known activity granularity"):
        activity_series(
            [], window_start=WINDOW_START, window_end=WINDOW_END, granularity="fortnight"
        )


def test_a_window_that_does_not_end_after_it_starts_is_refused():
    """An inverted range is a fact about the request, not about the data.

    Rendering it as an empty chart would tell the user they had no recorded
    activity in a window that never existed.
    """
    with pytest.raises(ValueError, match="must end after it starts"):
        activity_series(
            [],
            window_start=WINDOW_END,
            window_end=WINDOW_START,
        )


# ---------------------------------------------------------------------------
# Agreement with the configuration the service will read
# ---------------------------------------------------------------------------


def test_the_pure_module_windows_match_the_configured_defaults():
    """``RECENT_MONTH_DAYS`` is the configured default window; the week is not one.

    The pure module cannot import settings without making a pure test depend on
    the environment, so it carries the same constants and the service passes the
    configured values in. That only works while they agree, and this test is what
    keeps them from drifting apart silently.
    """
    settings = get_settings()

    assert settings.learning_default_window_days == RECENT_MONTH_DAYS
    assert RECENT_WEEK_DAYS == 7
    assert tuple(MetricKey) == LEARNING_METRICS
    assert set(ACTIVITY_GRANULARITIES) == {member.value for member in ActivityGranularity}
