"""The five analytics score endpoints, end to end over HTTP.

**Every test here requires a live PostgreSQL and has NOT been executed against
one.** They are marked ``integration``.

The five routes under test are the ones that answer a question about a *person*:
``/productivity``, ``/consistency``, ``/focus``, ``/deadlines`` and
``/estimation``. That is what makes them worth a dedicated file rather than a
few more cases in the overview suite: each one is a claim about how somebody
worked, and a claim about a person is only allowed to be made when the rows
behind it are known exactly.

So nothing here seeds "some activity" and asserts a plausible range. Every
figure is hand-derived from a named set of rows, and the test says how:

* the productivity breakdown is checked component by component — the four
  ``points`` must sum to the headline ``score`` and the four ``max_points`` to
  100, which is only true if the formula was actually applied;
* a brand-new account must answer ``available: false`` with a sentence, and
  ``score: null`` — **never** ``score: 0``, which the spec calls the wrong
  answer twice ("Not enough activity yet" is correct, "0% productivity" is
  wrong);
* deadline adherence is asserted as three exact counts and one exact rate, from
  the spec's own worked example (10 tasks, 8 finished on time, 2 still open);
* estimation is asserted from real ``(estimated, actual)`` pairs, including the
  **sign** of ``bias``, because ``estimated - actual`` is a convention a reader
  has to be able to check rather than trust;
* every response carries an ``explanation`` per component and labels itself a
  NEXUS-derived metric, per the spec's "do not pretend the score is
  scientifically validated".

Window arithmetic is the other half of the job: ``range`` must echo back
exactly the window that was asked for, and a missing, malformed, inverted or
oversized window must be refused with the shared error envelope rather than
quietly answered over a different period.

The daily tier is fed explicitly through ``POST /analytics/rebuild`` wherever a
score reads ``daily_metrics``. Two of the five (``/productivity`` and
``/focus``) sum the stored daily aggregates for their completion component, so
the aggregates are written first — the service would fill the gap itself, but a
test that relies on that is not testing the pipeline the client runs.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration

#: The five score routes, in the order a client meets them. Parametrised over by
#: the contract tests (auth, window validation, range echo) because the contract
#: is the same on all five and a route that drifts out of step is the failure.
SCORE_ROUTES = [
    "/api/v1/analytics/productivity",
    "/api/v1/analytics/consistency",
    "/api/v1/analytics/focus",
    "/api/v1/analytics/deadlines",
    "/api/v1/analytics/estimation",
]

#: A Monday-to-Friday working week. Five days rather than seven because the
#: figures below are chosen to add up exactly, and a five-day window makes
#: "active on every day of the window" a 100 that a reader can check at a
#: glance. ``DAY`` is a Monday, so the window never straddles a week boundary.
WINDOW_DAYS = 5


def _window() -> tuple[date, date]:
    """The working week every score test measures: ``DAY`` through Friday."""
    return DAY, DAY + timedelta(days=WINDOW_DAYS - 1)


def _days() -> list[date]:
    start, end = _window()
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def _rebuild(client, auth, start: date, end: date) -> dict:
    """Recompute ``daily_metrics`` for the window and return the 202 body."""
    response = await client.post(
        "/api/v1/analytics/rebuild",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _get(client, auth, route: str, start: date, end: date) -> dict:
    """GET one score route over an explicit window and return its body."""
    response = await client.get(
        route,
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The fixture week
# ---------------------------------------------------------------------------
#
# Ten tasks created in the window, eight of them finished. The arithmetic the
# scores make from that, written out so a reader can check each one against the
# code that produces it:
#
#   completion   8 completed / 10 created  = 80%  -> 30 x 0.80 = 24.0 points
#   deadline     8 on time, 0 late         = 100% -> 25 x 1.00 = 25.0 points
#   consistency  5 active days / 5 days    = 100% -> 20 x 1.00 = 20.0 points
#   focus        84 (below)                       -> 25 x 0.84 = 21.0 points
#   ------------------------------------------------------------
#   productivity                                           90.0 -> score 90
#
# The focus score: five sessions, three finished at 60 minutes and two left
# running at 30, so the mean is (3*60 + 2*30) / 5 = 48 minutes — above the
# 45-minute uninterrupted-block target, so the length term is a full 1.0 — and
# the follow-through term is 3 / (3 + 2) = 0.6. That is
# round(100 * (0.6 * 1.0 + 0.4 * 0.6)) = 84.
#
# The sum is deliberately an integer. A rounded total and a rounded per-term
# breakdown can disagree by a fraction on a window chosen at random, and the
# "the parts add up to the headline" assertion would then be testing floating
# point rather than the formula.


async def _seed_working_week(seed: AnalyticsSeed) -> None:
    """One realistic week: 10 tasks, 8 finished, 5 active days, 5 sessions.

    Every timestamp is written explicitly by the fixture helpers, so the
    expected figures do not depend on the wall clock or on the order the rows
    happen to be inserted in.
    """
    project = await seed.project(name="Nexus rebuild")

    # Eight finished tasks, two a day on Mon-Wed and one on Thu and Fri, each
    # completed on the day it was due so all eight are *on time*.
    finished_per_day = [2, 2, 2, 1, 1]
    for day, count in zip(_days(), finished_per_day, strict=True):
        for _ in range(count):
            await seed.completed_task(day=day, project_id=project.id)

    # Two open tasks, created on the Monday and Tuesday and due on the Monday.
    # They take the created count to 10, and they are the two "still overdue"
    # figures the deadline read reports — overdue as of the database clock,
    # which is months past their due date.
    for offset in range(2):
        await seed.task(
            project_id=project.id,
            created_at=at(_days()[offset]),
            due_date=DAY,
            status="todo",
        )

    # One activity event per day, so all five days count as active. The
    # consistency read counts days in the *feed*, not days with a session, so a
    # day with no event would be a gap even though a session ran.
    for day in _days():
        await seed.activity("TASK_COMPLETED", day=day, project_id=project.id)

    # Three finished 60-minute sessions, then two left running at 30 minutes.
    # "Left running" is a real status, not a fixture trick: it is the state a
    # session is in when it was started and never finished, and it is what the
    # focus read counts as an interruption.
    for day in _days()[:3]:
        await seed.work_session(day=day, minutes=60, project_id=project.id)
    for day in _days()[3:]:
        await seed.work_session(day=day, minutes=30, project_id=project.id, status="active")


@pytest.fixture
async def week(client, db_session):
    """A signed-in account carrying :func:`_seed_working_week`, aggregates built.

    Returns ``(auth, start, end)``: the bearer headers and the window every
    assertion in the week tests measures.
    """
    seed, auth = await seeded_client(client, db_session)
    start, end = _window()
    await _seed_working_week(seed)
    await _rebuild(client, auth, start, end)
    return auth, start, end


# ---------------------------------------------------------------------------
# The productivity score
# ---------------------------------------------------------------------------


async def test_a_working_week_scores_ninety_with_a_breakdown_that_adds_up(week, client):
    """The headline is the sum of its parts, and the parts are the documented weights.

    The spec asks for the score's formula to be shown, not merely asserted, so
    this checks the four component lines exactly: 24.0 of 30 for completion,
    25.0 of 25 for deadlines, 20.0 of 20 for consistency and 21.0 of 25 for
    focus. Those sum to 90.0, and the headline is 90.
    """
    auth, start, end = week

    body = await _get(client, auth, "/api/v1/analytics/productivity", start, end)

    assert body["available"] is True
    assert body["reason_if_unavailable"] is None
    assert body["score"] == 90
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("completion", 24.0, 30.0),
        ("deadline", 25.0, 25.0),
        ("consistency", 20.0, 20.0),
        ("focus", 21.0, 25.0),
    ]
    # The two identities the whole transparency claim rests on: the headline is
    # the sum of the parts, and the parts are denominated in 100.
    assert sum(c["points"] for c in body["components"]) == body["score"]
    assert sum(c["max_points"] for c in body["components"]) == 100
    # The completion component is 30 x 8/10 — the spec's own example, "10 tasks,
    # 8 completed, completion rate must equal 80%", weighted.
    assert body["components"][0]["points"] == 30.0 * 0.80
    assert body["weight_total"] == 100.0


async def test_the_rebuild_wrote_one_daily_row_per_day_of_the_window(week, client):
    """The aggregation tier is fed before the score is read, and it is per-day.

    Five days in, five rows written. If the rebuild ever bucketed the window
    differently, the completion component above would silently read a shorter
    period than the one the response claims to cover.
    """
    auth, start, end = week

    body = await _rebuild(client, auth, start, end)

    assert body == {"rows_written": WINDOW_DAYS}


async def test_every_component_explains_itself_and_the_score_is_labelled_derived(week, client):
    """Each component names the measurement that earned it, in one sentence.

    "Show users the factors contributing to the score" is the spec's phrasing;
    a number with no prose beside it is the thing the phase forbids. The
    percentages are quoted to one decimal because that is the precision the
    explanation is written at.
    """
    auth, start, end = week

    body = await _get(client, auth, "/api/v1/analytics/productivity", start, end)

    assert {c["name"]: c["explanation"] for c in body["components"]} == {
        "completion": "80.0% of the completion target (30 points available).",
        "deadline": "100.0% of the deadline target (25 points available).",
        "consistency": "100.0% of the consistency target (20 points available).",
        "focus": "84.0% of the focus target (25 points available).",
    }
    # The formula is part of the payload, not a docstring: a client renders it
    # verbatim, so the weights a user is shown cannot drift from the ones used.
    assert "completion x 30" in body["formula"]
    assert "the four weights total 100" in body["formula"]


async def test_the_three_scored_metrics_label_themselves_as_nexus_derived(week, client):
    """Productivity, consistency and focus all say what they are — and are not.

    The spec is blunt about this: "Do not pretend the score is scientifically
    validated. Label it as a NEXUS-derived metric." A number a user might read
    as a verdict on themselves has to arrive with its own limits attached, and
    the limit is different for each score: productivity disclaims validity
    outright, consistency disclaims *output* (it measures presence), and focus
    disclaims human attention. Asserting the three strings exactly is what pins
    those distinctions down.
    """
    auth, start, end = week

    for route, label, disclaimer in (
        (
            "/api/v1/analytics/productivity",
            "NEXUS Productivity Score",
            "A transparent, reproducible blend of four things this system "
            "recorded. It is not a validated measure of human performance.",
        ),
        (
            "/api/v1/analytics/consistency",
            "NEXUS Consistency Score",
            "Measures presence, not output. A day with a one-minute session "
            "counts as a day like any other.",
        ),
        (
            "/api/v1/analytics/focus",
            "NEXUS Focus Score",
            "Derived from recorded work-session behaviour. It does not measure "
            "human attention or concentration.",
        ),
    ):
        body = await _get(client, auth, route, start, end)

        assert body["label"] == label, route
        assert body["disclaimer"] == disclaimer, route


# ---------------------------------------------------------------------------
# Consistency and focus
# ---------------------------------------------------------------------------


async def test_consistency_counts_the_active_days_of_the_window(week, client):
    """Five active days of a five-day window is 100, and the streaks say so.

    The streak counters are checked alongside the score because they are the
    figures a settings panel renders under it, and a streak that disagreed with
    the day count would be a visible inconsistency on the same card.
    """
    auth, start, end = week

    body = await _get(client, auth, "/api/v1/analytics/consistency", start, end)

    assert body["available"] is True
    assert body["score"] == 100
    assert body["active_days"] == 5
    assert body["window_days"] == 5
    assert body["active_day_ratio"] == 100.0
    assert body["work_sessions"] == 5
    assert body["session_count"] == 5
    assert body["longest_streak"] == 5
    assert body["current_streak"] == 5
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("active_days", 100.0, 100.0)
    ]
    assert body["components"][0]["explanation"] == (
        "5 of 5 days had at least one work session (5 session(s) recorded)."
    )


async def test_focus_scores_session_depth_and_follow_through(week, client):
    """48-minute mean, 3 of 5 sessions finished: 0.6*1.0 + 0.4*0.6 = 84.

    The length term saturates at the 45-minute target — a longer block does not
    score better, because this is a measure of an uninterrupted block, not of
    stamina — so the 48-minute mean earns the full 60 of its 60 points.
    """
    auth, start, end = week

    body = await _get(client, auth, "/api/v1/analytics/focus", start, end)

    assert body["available"] is True
    assert body["score"] == 84
    assert body["avg_session_minutes"] == 48.0
    assert body["completed_planned_sessions"] == 3
    assert body["interruptions"] == 2
    assert body["reschedules"] == 0
    assert body["focused_minutes"] == 180
    assert body["total_minutes"] == 240
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("session_length", 60.0, 60.0),
        ("plan_followthrough", 24.0, 40.0),
    ]
    assert sum(c["points"] for c in body["components"]) == body["score"]


# ---------------------------------------------------------------------------
# Deadline adherence
# ---------------------------------------------------------------------------


async def _seed_finished(seed: AnalyticsSeed, project_id, *, per_day: list[int]) -> None:
    """Finish ``sum(per_day)`` tasks, ``per_day[i]`` of them on day ``i``.

    Every one is completed on the day it was due, because
    :func:`~tests.analytics_fixtures.AnalyticsSeed.completed_task` defaults
    ``due_date`` to the completion day — so a task counted as *on time* is
    counted because the fixture said so, never by accident.
    """
    for day, count in zip(_days(), per_day, strict=True):
        for _ in range(count):
            await seed.completed_task(day=day, project_id=project_id)


async def _seed_open_overdue(seed: AnalyticsSeed, project_id, *, count: int) -> None:
    """``count`` tasks created in the window, due on the Monday, still open.

    Overdue as of the database clock, which is months past their due date — the
    "still open past due" population the deadline read reports separately from
    the finished work.
    """
    for offset in range(count):
        await seed.task(
            project_id=project_id,
            created_at=at(_days()[offset]),
            due_date=DAY,
            status="todo",
        )


async def test_eight_on_time_and_two_still_overdue(client, db_session):
    """The spec's worked example, as three counts and one rate.

    10 tasks, 8 finished by their due date, 2 still open past due. The rate is
    8 / 8 = 100% — the two overdue tasks are reported but **not** counted
    against adherence, because a task that has not been finished yet has not
    missed a deadline. ``total_considered`` does include them (8 + 2 = 10),
    because they are part of the picture the user is being shown.
    """
    seed, auth = await seeded_client(client, db_session)
    start, end = _window()
    project = await seed.project(name="Deadlines")

    await _seed_finished(seed, project.id, per_day=[2, 2, 2, 1, 1])
    await _seed_open_overdue(seed, project.id, count=2)
    await _rebuild(client, auth, start, end)

    body = await _get(client, auth, "/api/v1/analytics/deadlines", start, end)

    assert body["available"] is True
    assert body["reason_if_unavailable"] is None
    assert body["on_time"] == 8
    assert body["late"] == 0
    assert body["still_overdue"] == 2
    assert body["adherence_rate"] == 100.0
    assert body["rate"] == 100.0
    assert body["overdue_open"] == 2
    assert body["total_considered"] == 10
    # The still-overdue line is reported as its own zero-point component with a
    # zero ceiling, so a reader can see the two open tasks *and* see that they
    # were not counted against the rate.
    assert body["components"][1] == {
        "name": "still_overdue",
        "points": 0.0,
        "max_points": 0.0,
        "explanation": (
            "2 task(s) are past due and still open; they do not count against "
            "adherence until they are finished."
        ),
    }


async def test_eight_on_time_against_three_late_is_seventy_two_point_seven(client, db_session):
    """8 / 11 = 72.7273%, to the four decimals the response rounds to.

    The previous test's 100% proves the arithmetic; this one proves it is a
    division and not a constant. Three extra tasks were completed two days
    *after* their due date, which is what `late` means at the day granularity
    the data has — a task completed at 09:00 on its due date is on time.
    """
    seed, auth = await seeded_client(client, db_session)
    start, end = _window()
    project = await seed.project(name="Deadlines")

    await _seed_finished(seed, project.id, per_day=[2, 2, 2, 1, 1])
    for day in _days()[:3]:
        await seed.completed_task(
            day=day,
            project_id=project.id,
            due_date=day - timedelta(days=2),
        )
    await _rebuild(client, auth, start, end)

    body = await _get(client, auth, "/api/v1/analytics/deadlines", start, end)

    assert (body["on_time"], body["late"], body["still_overdue"]) == (8, 3, 0)
    assert body["adherence_rate"] == 72.7273
    assert body["rate"] == body["adherence_rate"]
    assert body["total_considered"] == 11
    # The breakdown shows the rate and reports the still-overdue figure as its
    # own zero-point line, so the "these do not count against you yet" rule is
    # visible rather than implied.
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("on_time", 72.7273, 100.0),
        ("still_overdue", 0.0, 0.0),
    ]
    assert body["components"][0]["explanation"] == (
        "8 of 11 completed task(s) finished by their due date."
    )


# ---------------------------------------------------------------------------
# Estimation accuracy
# ---------------------------------------------------------------------------


async def test_estimation_reports_a_negative_bias_for_habitual_under_estimating(client, db_session):
    """The three seeded pairs, worked out by hand.

    ``(estimated, actual)`` = ``(60, 80)``, ``(90, 150)``, ``(120, 90)``:

    ==============  =======  =========  ====================
    pair           signed   absolute   percentage
    ==============  =======  =========  ====================
    (60, 80)          -20        20   20/60  = 33.3333%
    (90, 150)         -60        60   60/90  = 66.6667%
    (120, 90)        +30        30   30/120 = 25.0000%
    ==============  =======  =========  ====================

    means: 36.6667 absolute, 41.6667 percentage, bias -16.6667.

    **The sign is the point of this test.** ``bias`` is ``estimated - actual``,
    so a negative figure means the estimates ran *below* the time actually
    taken, and it must sit beside a high under-estimation rate. The opposite
    convention would print a positive bias next to 66.67% under-estimated,
    which reads as a contradiction about the same three tasks.
    """
    seed, auth = await seeded_client(client, db_session)
    start, end = _window()
    project = await seed.project(name="Estimates")

    for day, (estimated, actual) in zip(_days()[:3], [(60, 80), (90, 150), (120, 90)], strict=True):
        await seed.completed_task(
            day=day,
            project_id=project.id,
            estimated_minutes=estimated,
            actual_minutes=actual,
        )
    # A fourth finished task with no estimate at all. It is not a zero estimate,
    # so it must be dropped rather than counted as a perfect 0-minute miss —
    # which is why the sample count is 3, not 4.
    await seed.completed_task(
        day=_days()[4], project_id=project.id, estimated_minutes=None, actual_minutes=45
    )

    body = await _get(client, auth, "/api/v1/analytics/estimation", start, end)

    assert body["available"] is True
    assert body["reason_if_unavailable"] is None
    assert body["sample_count"] == 3
    assert body["pairs_compared"] == 3
    assert body["absolute_error"] == 36.6667
    assert body["mean_absolute_error"] == 36.6667
    assert body["percentage_error"] == 41.6667
    assert body["mean_percentage_error"] == 41.6667
    assert body["bias"] == -16.6667
    assert body["median_error"] == 30.0
    assert body["under_estimation_rate"] == 66.6667
    assert body["underestimation_rate"] == 66.6667
    assert body["over_estimation_rate"] == 33.3333
    assert body["overestimation_rate"] == 33.3333
    # The two spellings of the under-estimation figure are kept identical, and
    # both agree in sign with the bias above them.
    assert body["bias"] < 0
    assert body["under_estimation_rate"] > body["over_estimation_rate"]


# ---------------------------------------------------------------------------
# "Not enough activity yet" — never a zero
# ---------------------------------------------------------------------------


async def test_a_new_account_has_no_productivity_score_and_says_why(client, db_session):
    """Zero activity is an absence of evidence, and it is reported as one.

    This is the case the spec names explicitly::

        "Not enough activity yet"     <- correct
        "0% productivity"             <- wrong

    So a brand-new account gets ``score: null`` with all four components
    contributing zero of their full weight, and each component's explanation
    says it was *not counted* — which is a different statement from scoring zero
    for it. A 90 in the breakdown and a 0 in the headline would be the failure
    this test exists to catch.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    body = await _get(client, auth, "/api/v1/analytics/productivity", start, end)

    assert body["score"] is None
    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")
    assert "no task completions" in body["reason_if_unavailable"]
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("completion", 0.0, 30.0),
        ("deadline", 0.0, 25.0),
        ("consistency", 0.0, 20.0),
        ("focus", 0.0, 25.0),
    ]
    assert all(c["explanation"].startswith("Not counted:") for c in body["components"])


async def test_a_new_account_has_no_consistency_score_and_says_why(client, db_session):
    """No sessions means no record of which days were active, so no score.

    The reason names the missing thing specifically: a user who worked but
    never started a timer is measuring the product, not themselves.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    body = await _get(client, auth, "/api/v1/analytics/consistency", start, end)

    assert body["score"] is None
    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")
    assert "no work sessions were started" in body["reason_if_unavailable"]
    assert body["active_days"] == 0
    assert body["window_days"] == 5
    assert body["longest_streak"] == 0
    assert body["current_streak"] == 0


async def test_a_new_account_has_no_focus_score_and_says_why(client, db_session):
    """A follow-through rate needs a denominator, and there is none.

    ``focused_minutes`` and ``total_minutes`` are genuinely 0 — nothing was
    timed — but the *score* is null, because there is no planned session that
    was ever finished to measure against.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    body = await _get(client, auth, "/api/v1/analytics/focus", start, end)

    assert body["score"] is None
    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")
    assert "no planned work session was started" in body["reason_if_unavailable"]
    assert body["avg_session_minutes"] is None
    assert body["completed_planned_sessions"] == 0
    assert body["focused_minutes"] == 0


async def test_a_new_account_has_no_deadline_adherence_and_says_why(client, db_session):
    """Nothing has a deadline, so the rate is null rather than 0%.

    ``adherence_rate`` and its ``rate`` alias are both null, and the response
    still carries the ``still_overdue`` line so the panel has something concrete
    to show beside the reason.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    body = await _get(client, auth, "/api/v1/analytics/deadlines", start, end)

    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")
    assert body["adherence_rate"] is None
    assert body["rate"] is None
    assert (body["on_time"], body["late"], body["still_overdue"]) == (0, 0, 0)
    assert body["total_considered"] == 0
    assert [(c["name"], c["points"], c["max_points"]) for c in body["components"]] == [
        ("still_overdue", 0.0, 0.0)
    ]


async def test_a_new_account_has_no_estimation_accuracy_and_says_why(client, db_session):
    """A user who has never estimated anything is told so, not shown 0% error.

    A perfect 0% estimation error for a user who has made no estimates would be
    the most flattering and most false number in the product, and Phase 10 would
    train on it.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    body = await _get(client, auth, "/api/v1/analytics/estimation", start, end)

    assert body["available"] is False
    assert body["reason_if_unavailable"].startswith("Not enough activity yet")
    assert body["sample_count"] == 0
    assert body["pairs_compared"] == 0
    assert body["absolute_error"] is None
    assert body["percentage_error"] is None
    assert body["bias"] is None
    assert body["median_error"] is None
    assert body["under_estimation_rate"] is None
    assert body["over_estimation_rate"] is None


# ---------------------------------------------------------------------------
# The window is echoed, and a bad window is refused
# ---------------------------------------------------------------------------


async def test_the_response_range_echoes_the_window_that_was_asked_for(week, client):
    """A client never has to re-derive which days a number covers.

    Every aggregate response carries the window it was computed over. The
    granularity is ``day`` on all five routes because these are scores, not
    series: bucketing never changes what a score counts, so the routes take no
    granularity parameter and report the one they measured in.
    """
    auth, start, end = week

    for route in SCORE_ROUTES:
        body = await _get(client, auth, route, start, end)

        assert body["range"] == {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "granularity": "day",
        }, route


@pytest.mark.parametrize("route", SCORE_ROUTES)
async def test_every_score_route_refuses_an_anonymous_caller(route, client, assert_error_envelope):
    """401 with the shared envelope — and the refusal is the same on all five.

    Analytics is the one surface that characterises a person, so it is a
    capability of its own. An anonymous caller must not be able to learn
    whether the route exists, let alone read anyone's figures.
    """
    response = await client.get(route)

    assert_error_envelope(response, status_code=401, code="unauthorized")


@pytest.mark.parametrize("route", SCORE_ROUTES)
async def test_a_malformed_date_is_a_validation_error(
    route, client, db_session, assert_error_envelope
):
    """``?start_date=not-a-date`` is a 422 naming the offending parameter.

    Answering over a default window instead would be the dangerous outcome: the
    caller would be shown a real number computed over a period they never asked
    for, and the response's own ``range`` would be the only clue.
    """
    _seed, auth = await seeded_client(client, db_session)

    response = await client.get(route, params={"start_date": "not-a-date"}, headers=auth)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert [entry["field"] for entry in error["details"]["errors"]] == ["query.start_date"]


@pytest.mark.parametrize("route", SCORE_ROUTES)
async def test_an_inverted_window_is_refused(route, client, db_session, assert_error_envelope):
    """``end_date`` before ``start_date`` is rejected at the edge of the request.

    Passed down to an aggregation it would produce an empty series that reads
    exactly like "you did nothing" — the failure the window resolver exists to
    prevent.
    """
    _seed, auth = await seeded_client(client, db_session)
    start, end = _window()

    response = await client.get(
        route,
        params={"start_date": end.isoformat(), "end_date": start.isoformat()},
        headers=auth,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["message"] == "end_date must not be earlier than start_date."


@pytest.mark.parametrize("route", SCORE_ROUTES)
async def test_an_oversized_window_is_refused(
    route, client, db_session, settings, assert_error_envelope
):
    """A window wider than the configured ceiling is a 422, not a table scan.

    An unbounded analytics read is the one shape this system's indexes cannot
    serve: every aggregate below scans the owner's whole history. The limit is
    read from settings rather than spelled out, so the refusal and the
    configuration cannot drift apart.
    """
    _seed, auth = await seeded_client(client, db_session)

    response = await client.get(
        route,
        params={"start_date": "2020-01-01", "end_date": "2026-01-01"},
        headers=auth,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["message"] == (
        f"The analytics window may span at most {settings.analytics_max_range_days} days."
    )
