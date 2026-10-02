"""The analytics dashboard surface, end to end through HTTP.

``GET /analytics/overview`` is what the dashboard paints first, and ``/workload``,
``/time``, ``/trends`` and ``/series`` are the panels that hang off it. Every one
of them reads the same thing — rows this application wrote — so a test here can
state an exact figure and be *right*, and that is what the brief demands:

    If: 10 tasks, 8 completed
    Then: completion rate must equal 80%

The dataset below is therefore written out as a table rather than generated: ten
tasks created and eight completed inside one fixed week, 330 tracked minutes
across six sessions, five days carrying an activity event, six distinct
project-days. :data:`WEEK_TOTALS` is the hand sum of those rows and is asserted
literally; nothing in this file recomputes an expectation from the response it
is checking.

Three properties get most of the attention because they are the ones a
dashboard gets wrong quietly:

* **A narrower window must answer a narrower question.** Summing the aggregates
  that happen to exist would silently return a week's total for a day.
* **A comparison from zero has no percentage.** ``Infinity``/``NaN``/
  ``undefined%`` are what the brief names, and the only honest answer is null.
* **No activity is an answer, not an error.** Every score the rows cannot support
  comes back ``available=False`` with a reason; none of them comes back ``0``.

**Every test here requires a live PostgreSQL and has NOT been executed against
production data.** They are marked ``integration``.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta

import pytest

from app.models.enums import ActivityEvent
from app.models.planner import AvailabilityRule
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration

#: The fixed week every hand-computed figure in this file is written against:
#: Monday 2026-01-05 through Sunday 2026-01-11, inclusive.
WEEK_START = DAY
WEEK_END = DAY + timedelta(days=6)

#: The equal-length window immediately before it — what ``/overview`` compares
#: against, and what ``_previous_window`` derives from the request's own dates.
PREVIOUS_START = WEEK_START - timedelta(days=7)
PREVIOUS_END = WEEK_START - timedelta(days=1)

#: A later week with nothing in it at all. Far enough from :data:`WEEK_START`
#: that no seeded row can reach it, and Monday-to-Sunday so a week-shaped
#: comparison window applies.
QUIET_START = date(2026, 3, 2)
QUIET_END = date(2026, 3, 8)

#: A 28-day range starting on the same Monday, used for the granularity tests.
SPAN_START = WEEK_START
SPAN_END = WEEK_START + timedelta(days=27)

#: The twelve counters ``/overview`` reports, and the hand sum of the seeded
#: week. Derived by hand from :func:`_seed_week`:
#:
#: * ``tasks_created`` 2 (Mon) + 2 (Tue) + 4 (Wed) + 2 (Fri) = 10
#: * ``tasks_completed`` 2 (Mon) + 4 (Wed) + 2 (Fri) = 8
#: * ``tasks_overdue`` 1 — the Tue task is due Wed and still open
#: * ``tasks_blocked`` / ``tasks_rescheduled`` 1 each, from one event apiece
#: * ``planned_minutes`` / ``actual_minutes`` 60+45+30+90+60+45 = 330
#: * ``work_sessions`` 6, one per seeded session
#: * ``calendar_events`` 1, ``knowledge_events`` 1 (one ``note_created`` event)
#: * ``projects_touched`` 1+1+1+1+2 — distinct projects per day, summed
WEEK_TOTALS: dict[str, float] = {
    "actual_minutes": 330.0,
    "calendar_events": 1.0,
    "knowledge_events": 1.0,
    "planned_minutes": 330.0,
    "projects_touched": 6.0,
    "tasks_blocked": 1.0,
    "tasks_cancelled": 0.0,
    "tasks_completed": 8.0,
    "tasks_created": 10.0,
    "tasks_overdue": 1.0,
    "tasks_rescheduled": 1.0,
    "work_sessions": 6.0,
}

#: The two work sessions whose combined length is the week's tracked time, used
#: to prove the totals are read from rows rather than estimated.
WEEK_ACTIVE_DAYS = 5  # Mon-Fri carry an activity event; Sat and Sun do not.
WEEK_WINDOW_DAYS = 7


# -- Helpers ----------------------------------------------------------------


def _window(start: date, end: date) -> dict[str, str]:
    """The query parameters every analytics read shares."""
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


async def _rebuild(client, headers: dict[str, str], *, start: date, end: date) -> int:
    """Recompute ``daily_metrics`` for a window, returning the rows written.

    Every read below is served from the aggregate table, so a test that seeded
    rows and forgot this would be asserting against an empty dashboard. The
    endpoint answers **202**, and the row count is one per day in the window
    whether or not that day had activity.
    """
    response = await client.post(
        "/api/v1/analytics/rebuild",
        params=_window(start, end),
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return json.loads(response.text)["rows_written"]


async def _get(client, path: str, headers: dict[str, str], **params: str):
    """GET an analytics route over an explicit window, asserting 200."""
    response = await client.get(f"/api/v1/analytics/{path}", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _total(body: dict, label: str) -> dict:
    """One entry of ``OverviewRead.totals``, by metric name."""
    return next(point for point in body["totals"] if point["label"] == label)


def _with_availability(db_session, seed: AnalyticsSeed, *, weekday: int, start: time, end: time):
    """Declare one recurring "I am free then" window.

    ``AnalyticsSeed`` covers the rows the *analytics* engine aggregates, and an
    availability rule is not one of them: it is the one input the engine cannot
    derive from recorded work, so it is written here rather than invented by the
    seed helper.
    """
    db_session.add(
        AvailabilityRule(
            owner_id=seed.owner.id,
            weekday=weekday,
            starts_at=start,
            ends_at=end,
            label="fixture",
        )
    )


async def _seed_week(seed: AnalyticsSeed) -> None:
    """Write the fixed week described by :data:`WEEK_TOTALS`.

    Every helper takes an explicit instant, so nothing here depends on the wall
    clock and the same rows are produced on every run. The comments name the
    arithmetic the constants depend on; changing a count without changing them
    would make the assertions wrong rather than merely stale.
    """
    atlas = await seed.project(name="atlas")
    borealis = await seed.project(name="borealis")

    # Monday: two tasks created and completed on the day.
    for _ in range(2):
        await seed.completed_task(day=WEEK_START, project_id=atlas.id)

    # Tuesday: two created, neither finished. The first is due **Wednesday**, so
    # it is the week's single `tasks_overdue` day — counted on the day it was
    # due and not finished by its end, which is the definition the repository
    # states.
    await seed.task(
        project_id=atlas.id,
        created_at=at(DAY + timedelta(days=1)),
        due_date=DAY + timedelta(days=2),
    )
    await seed.task(project_id=borealis.id, created_at=at(DAY + timedelta(days=1)))

    # Wednesday: four created and completed.
    for _ in range(4):
        await seed.completed_task(day=DAY + timedelta(days=2), project_id=borealis.id)

    # Friday: two more created and completed.
    for _ in range(2):
        await seed.completed_task(day=DAY + timedelta(days=4), project_id=atlas.id)

    # Six sessions: 60 + 45 (Mon), 30 (Tue), 90 (Wed), 60 (Fri), 45 (Sun).
    for minutes in (60, 45):
        await seed.work_session(day=WEEK_START, minutes=minutes)
    await seed.work_session(day=DAY + timedelta(days=1), minutes=30)
    await seed.work_session(day=DAY + timedelta(days=2), minutes=90)
    await seed.work_session(day=DAY + timedelta(days=4), minutes=60)
    await seed.work_session(day=WEEK_END, minutes=45)

    # One calendar entry, and one knowledge event, so those two counters are
    # non-zero too rather than accidentally passing as 0.
    await seed.calendar_event(day=DAY + timedelta(days=2), project_id=borealis.id, minutes=60)

    # Activity events on Mon-Thu plus two on Friday, touching 1+1+1+1+2 distinct
    # projects. Five distinct days carry an event, which is `active_days`.
    await seed.activity(ActivityEvent.TASK_CREATED, day=WEEK_START, project_id=atlas.id)
    await seed.activity(
        ActivityEvent.TASK_RESCHEDULED, day=DAY + timedelta(days=1), project_id=atlas.id
    )
    await seed.activity(
        ActivityEvent.NOTE_CREATED, day=DAY + timedelta(days=2), project_id=borealis.id
    )
    await seed.activity(
        ActivityEvent.TASK_BLOCKED, day=DAY + timedelta(days=3), project_id=atlas.id
    )
    for project in (atlas, borealis):
        await seed.activity(
            ActivityEvent.TASK_CREATED, day=DAY + timedelta(days=4), project_id=project.id
        )


# -- The overview aggregate --------------------------------------------------


async def test_the_overview_totals_are_the_hand_sum_of_the_seeded_week(client, db_session):
    """Twelve counters, twelve exact figures, taken straight from the rows.

    The spec's worked example — ten tasks created, eight completed — is the
    ``tasks_created``/``tasks_completed`` pair here. The other ten counters exist
    so a bug in one grouped query cannot hide behind a correct neighbour.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)

    written = await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)
    assert written == WEEK_WINDOW_DAYS

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    assert {point["label"]: point["current"] for point in body["totals"]} == WEEK_TOTALS


async def test_the_overview_reports_the_window_it_was_asked_for(client, db_session):
    """``range`` is echoed back, and ``daily`` is one row per day, in order."""
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    assert body["range"] == {
        "start_date": WEEK_START.isoformat(),
        "end_date": WEEK_END.isoformat(),
        "granularity": "day",
    }
    assert [row["metric_date"] for row in body["daily"]] == [
        (WEEK_START + timedelta(days=offset)).isoformat() for offset in range(WEEK_WINDOW_DAYS)
    ]
    # The rebuild covered every day of the window, so nothing is stale.
    assert body["stale"] is False


async def test_the_overview_reports_active_days_and_active_projects(client, db_session):
    """Five active days out of seven, and six project-days touched.

    ``active_days`` is read from the event feed (days the user showed up), and
    ``projects_touched`` is a *distinct* count per day summed across the week —
    which is why Friday, where two projects were touched, contributes 2.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    consistency = body["consistency"]
    assert consistency["active_days"] == WEEK_ACTIVE_DAYS
    assert consistency["window_days"] == WEEK_WINDOW_DAYS
    assert consistency["active_day_ratio"] == pytest.approx(71.4286, abs=1e-4)
    assert consistency["available"] is True
    assert consistency["work_sessions"] == 6
    assert _total(body, "projects_touched")["current"] == 6.0


async def test_the_productivity_score_is_the_sum_of_its_own_components(client, db_session):
    """88, and the four components that produce it, spelled out.

    10 created / 8 completed is 80% of the 30 completion points (24.0); all
    eight finished on time, so 25.0 of 25 deadline points; 5 of 7 active days is
    a 71% consistency score, i.e. 0.71 x 20 = 14.2 of 20; and six completed
    sessions averaging 55 minutes with no interruptions is a full 25.0 focus
    component. 24 + 25 + 14.2 + 25 = 88.2, which clamps-and-rounds to 88.

    Written out because a score nobody can re-derive is exactly the "fake
    intelligence" the brief forbids — if this test ever fails, the *arithmetic*
    changed, not the expectation.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    productivity = body["productivity"]
    assert productivity["available"] is True
    assert productivity["score"] == 88
    assert [component["points"] for component in productivity["components"]] == [
        24.0,
        25.0,
        14.2,
        25.0,
    ]
    assert [component["name"] for component in productivity["components"]] == [
        "completion",
        "deadline",
        "consistency",
        "focus",
    ]


# -- Date range filters ------------------------------------------------------


async def test_a_narrower_window_answers_a_narrower_question(client, db_session):
    """One day of the seeded week is strictly less than the week.

    This is the failure the aggregate tier exists to prevent: summing "whatever
    daily rows happen to be there" would answer the same number for both windows,
    which reads as a working dashboard right up until the user checks it.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    monday = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_START))
    week = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    # Monday alone: 2 tasks created, 2 completed, 60 + 45 tracked minutes.
    assert _total(monday, "tasks_created")["current"] == 2.0
    assert _total(monday, "tasks_completed")["current"] == 2.0
    assert _total(monday, "actual_minutes")["current"] == 105.0

    for label in ("tasks_created", "tasks_completed", "actual_minutes", "work_sessions"):
        assert _total(monday, label)["current"] < _total(week, label)["current"], label


async def test_a_window_with_no_activity_returns_the_empty_shape_not_a_zero_score(
    client, db_session
):
    """A quiet week is ``available=False`` with a reason, never ``0``.

    The brief calls this out by name — ``"Not enough activity yet"`` is correct
    and ``"0% productivity"`` is wrong — so this asserts the *absence* of a
    number, not the presence of a small one.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)
    await _rebuild(client, headers, start=QUIET_START, end=QUIET_END)

    body = await _get(client, "overview", headers, **_window(QUIET_START, QUIET_END))

    assert {point["label"]: point["current"] for point in body["totals"]} == dict.fromkeys(
        WEEK_TOTALS, 0.0
    )
    assert body["reason_if_empty"].startswith("Not enough activity yet")

    # A rebuilt quiet week still has one aggregate row per day, and every one of
    # them is a real zero: "nothing happened on Tuesday" is an observation.
    assert len(body["daily"]) == WEEK_WINDOW_DAYS
    assert all(row["tasks_completed"] == 0 for row in body["daily"])

    assert body["productivity"]["available"] is False
    assert body["productivity"]["score"] is None
    assert body["deadlines"]["available"] is False
    assert body["deadlines"]["adherence_rate"] is None
    assert body["deadlines"]["rate"] is None
    assert body["consistency"]["available"] is False
    assert body["consistency"]["score"] is None
    assert body["consistency"]["active_days"] == 0
    assert body["focus"]["available"] is False
    assert body["focus"]["score"] is None
    assert body["estimation"]["available"] is False


async def test_a_day_series_has_one_row_per_day_and_buckets_that_sum(client, db_session):
    """Seven daily rows; one weekly bucket holding all seven days.

    The week aggregate is not a second table — it is the seven stored days
    summed — so the bucket and the days are the same numbers read twice.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    days = await _get(client, "series", headers, **_window(WEEK_START, WEEK_END))
    weeks = await _get(
        client, "series", headers, granularity="week", **_window(WEEK_START, WEEK_END)
    )

    assert len(days) == WEEK_WINDOW_DAYS
    assert [row["metric_date"] for row in days] == [
        (WEEK_START + timedelta(days=offset)).isoformat() for offset in range(WEEK_WINDOW_DAYS)
    ]

    # The seeded week starts on a Monday, so exactly one bucket holds all of it.
    assert len(weeks) == 1
    assert weeks[0]["metric_date"] == WEEK_START.isoformat()
    for column in WEEK_TOTALS:
        assert weeks[0][column] == sum(row[column] for row in days), column


async def test_a_week_series_has_seven_times_fewer_points_than_a_day_series(client, db_session):
    """28 days become 4 weekly buckets, and each is the sum of its own seven days.

    The 28-day span below is seeded so that every one of the four Monday-anchored
    weeks carries a known number of tracked minutes — 180, 15, 45 and 130, which
    sum to the same 370 the daily rows do. A week bucket that drifted from its
    days would break the second assertion, not the first.
    """
    seed, headers = await seeded_client(client, db_session)
    for day, minutes in (
        (SPAN_START, 60),  # week 1
        (SPAN_START + timedelta(days=1), 30),
        (SPAN_START + timedelta(days=6), 90),
        (SPAN_START + timedelta(days=7), 15),  # week 2
        (SPAN_START + timedelta(days=14), 45),  # week 3
        (SPAN_START + timedelta(days=21), 120),  # week 4
        (SPAN_END, 10),  # the last day, still inside week 4
    ):
        await seed.work_session(day=day, minutes=minutes)
    await _rebuild(client, headers, start=SPAN_START, end=SPAN_END)

    days = await _get(client, "series", headers, **_window(SPAN_START, SPAN_END))
    weeks = await _get(
        client, "series", headers, granularity="week", **_window(SPAN_START, SPAN_END)
    )

    assert len(days) == 28
    assert len(weeks) == 4
    assert len(days) == 7 * len(weeks)

    assert [row["metric_date"] for row in weeks] == [
        (SPAN_START + timedelta(days=7 * offset)).isoformat() for offset in range(4)
    ]
    assert [row["actual_minutes"] for row in weeks] == [180, 15, 45, 130]
    assert sum(row["actual_minutes"] for row in weeks) == 370
    assert sum(row["actual_minutes"] for row in days) == 370

    for week in weeks:
        first = date.fromisoformat(week["metric_date"])
        in_bucket = [
            row
            for row in days
            if first <= date.fromisoformat(row["metric_date"]) <= first + timedelta(days=6)
        ]
        assert week["actual_minutes"] == sum(row["actual_minutes"] for row in in_bucket)
        assert week["work_sessions"] == sum(row["work_sessions"] for row in in_bucket)


async def test_a_month_series_buckets_by_calendar_month(client, db_session):
    """28 days from 5 January fall into a January bucket and a February one.

    The January bucket holds 27 of the 28 days (360 minutes) and the February
    bucket the last (10), because a month bucket is the first of the month
    regardless of where the window was cut.
    """
    seed, headers = await seeded_client(client, db_session)
    for day, minutes in (
        (SPAN_START, 60),
        (SPAN_START + timedelta(days=1), 30),
        (SPAN_START + timedelta(days=6), 90),
        (SPAN_START + timedelta(days=7), 15),
        (SPAN_START + timedelta(days=14), 45),
        (SPAN_START + timedelta(days=21), 120),
        (SPAN_END, 10),
    ):
        await seed.work_session(day=day, minutes=minutes)
    await _rebuild(client, headers, start=SPAN_START, end=SPAN_END)

    months = await _get(
        client, "series", headers, granularity="month", **_window(SPAN_START, SPAN_END)
    )

    assert [row["metric_date"] for row in months] == ["2026-01-01", "2026-02-01"]
    assert [row["actual_minutes"] for row in months] == [360, 10]
    assert [row["work_sessions"] for row in months] == [6, 1]


async def test_an_unknown_granularity_is_refused_rather_than_defaulted(client, db_session):
    """``quarter`` is a 422, not a silent fall back to daily.

    A granularity that fell back to a default would plot a plausible line for a
    question the caller did not ask.
    """
    _seed, headers = await seeded_client(client, db_session)

    response = await client.get(
        "/api/v1/analytics/series",
        params={**_window(WEEK_START, WEEK_END), "granularity": "quarter"},
        headers=headers,
    )

    assert response.status_code == 422, response.text


# -- Comparison --------------------------------------------------------------


async def test_the_overview_carries_the_previous_equal_length_window(client, db_session):
    """Seven days of January compared against the seven days before them."""
    _seed, headers = await seeded_client(client, db_session)

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    assert body["previous_range"] == {
        "start_date": PREVIOUS_START.isoformat(),
        "end_date": PREVIOUS_END.isoformat(),
        "granularity": "day",
    }
    assert (
        date.fromisoformat(body["range"]["end_date"])
        - date.fromisoformat(body["range"]["start_date"])
    ).days + 1 == (
        date.fromisoformat(body["previous_range"]["end_date"])
        - date.fromisoformat(body["previous_range"]["start_date"])
    ).days + 1


async def test_the_absolute_and_percentage_changes_are_exactly_right(client, db_session):
    """6 against 4 is +2 and +50.0%, both of them, not approximately.

    Both windows are rebuilt explicitly: ``/overview`` never fills the previous
    window itself, so an un-rebuilt comparison period reads as zero and the
    percentages come back null.
    """
    seed, headers = await seeded_client(client, db_session)
    project = await seed.project(name="atlas")

    # Previous week: 4 tasks created and completed, 120 tracked minutes.
    for _ in range(4):
        await seed.completed_task(day=date(2026, 1, 2), project_id=project.id)
    await seed.work_session(day=date(2026, 1, 2), minutes=120)

    # Current week: 6 created and completed, 180 tracked minutes.
    for _ in range(6):
        await seed.completed_task(day=WEEK_START, project_id=project.id)
    await seed.work_session(day=WEEK_START, minutes=180)

    await _rebuild(client, headers, start=PREVIOUS_START, end=PREVIOUS_END)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))

    completions = _total(body, "tasks_completed")
    assert (completions["current"], completions["previous"]) == (6.0, 4.0)
    assert completions["absolute_change"] == 2.0
    assert completions["percent_change"] == 50.0

    creations = _total(body, "tasks_created")
    assert (creations["current"], creations["previous"]) == (6.0, 4.0)
    assert creations["absolute_change"] == 2.0
    assert creations["percent_change"] == 50.0

    minutes = _total(body, "actual_minutes")
    assert (minutes["current"], minutes["previous"]) == (180.0, 120.0)
    assert minutes["absolute_change"] == 60.0
    assert minutes["percent_change"] == 50.0


async def test_a_previous_period_of_zero_yields_null_never_infinity(client, db_session):
    """Nothing to something has an absolute change and no percentage.

    ``(3 - 0) / 0`` is ``Infinity``, which serialises into a rendered page as
    the literal text ``Infinity%``. The response asserts both halves: the field
    is ``None``, and the serialised body contains none of the three forbidden
    spellings the brief names.
    """
    seed, headers = await seeded_client(client, db_session)

    for offset in range(3):
        await seed.activity(ActivityEvent.TASK_RESCHEDULED, day=WEEK_START + timedelta(days=offset))
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    response = await client.get(
        "/api/v1/analytics/overview",
        params=_window(WEEK_START, WEEK_END),
        headers=headers,
    )
    assert response.status_code == 200, response.text

    body = response.json()
    reschedules = _total(body, "tasks_rescheduled")
    assert (reschedules["current"], reschedules["previous"]) == (3.0, 0.0)
    assert reschedules["absolute_change"] == 3.0
    assert reschedules["percent_change"] is None

    # Every other metric is 0 against 0 for the same reason.
    for point in body["totals"]:
        assert point["percent_change"] is None, point["label"]

    for forbidden in ("Infinity", "NaN", "undefined%"):
        assert forbidden not in response.text


async def test_a_trend_omits_days_with_no_recorded_activity(client, db_session):
    """Six days carry a completion; the trend has six points, not seven.

    A zero-filled point would draw a straight line through a day nothing was
    recorded on, which is a claim about that day.
    """
    seed, headers = await seeded_client(client, db_session)
    project = await seed.project(name="atlas")
    for offset in (0, 1, 3, 5, 6):
        await seed.completed_task(day=WEEK_START + timedelta(days=offset), project_id=project.id)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    points = await _get(
        client, "trends", headers, metric="tasks_completed", **_window(WEEK_START, WEEK_END)
    )

    assert [point["value"] for point in points] == [1.0, 1.0, 1.0, 1.0, 1.0]
    assert [point["bucket"] for point in points] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-08",
        "2026-01-10",
        "2026-01-11",
    ]
    assert [point["period_end"] for point in points] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-08",
        "2026-01-10",
        "2026-01-11",
    ]


async def test_a_trend_metric_outside_the_allowlist_is_refused(client, db_session):
    """``?metric=user_id`` is a 422 rather than a column read the caller chose."""
    _seed, headers = await seeded_client(client, db_session)

    response = await client.get(
        "/api/v1/analytics/trends",
        params={**_window(WEEK_START, WEEK_END), "metric": "user_id"},
        headers=headers,
    )

    assert response.status_code == 422, response.text


async def test_a_trend_bucket_compares_against_the_same_bucket_last_period(client, db_session):
    """Every point should carry the previous week's figure for the same day.

    The two windows are paired by **position** — the first bucket of this week
    against the first of last week — because the windows sit at different dates
    and a date-keyed lookup would match none of them.
    """
    seed, headers = await seeded_client(client, db_session)
    project = await seed.project(name="atlas")

    # Same weekday pattern in each window: two completions on the Monday.
    await seed.completed_task(day=date(2026, 1, 2), project_id=project.id)
    await seed.completed_task(day=date(2026, 1, 2), project_id=project.id)
    await seed.completed_task(day=WEEK_START, project_id=project.id)
    await seed.completed_task(day=WEEK_START, project_id=project.id)

    await _rebuild(client, headers, start=PREVIOUS_START, end=PREVIOUS_END)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    points = await _get(
        client, "trends", headers, metric="tasks_completed", **_window(WEEK_START, WEEK_END)
    )

    assert [(point["value"], point["previous"]) for point in points] == [(2.0, 2.0)]
    assert points[0]["absolute_change"] == 0.0
    assert points[0]["percent_change"] == 0.0


# -- Workload ----------------------------------------------------------------


async def test_the_workload_ratio_is_scheduled_against_declared_availability(client, db_session):
    """330 scheduled minutes over 900 declared available is 36.6667%.

    Availability is a **Monday-to-Friday** 09:00-12:00 rule, so the window's two
    weekend days contribute nothing and the declared total is 5 x 180 = 900. The
    ratio is the only figure here that needs an input the analytics engine
    cannot derive from recorded work, which is why it is the one that is null
    when it is missing.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    for weekday in range(5):  # Monday..Friday
        _with_availability(db_session, seed, weekday=weekday, start=time(9, 0), end=time(12, 0))
    await db_session.commit()
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "workload", headers, **_window(WEEK_START, WEEK_END))

    assert body["scheduled_minutes"] == 330
    assert body["actual_minutes"] == 330
    assert body["available_minutes"] == 900
    assert body["workload_ratio"] == pytest.approx(36.6667, abs=1e-4)
    # 330 minutes spread over seven days, not over the five the sessions fall on.
    assert body["average_daily_scheduled_minutes"] == pytest.approx(47.14, abs=1e-9)
    assert body["open_tasks"] == 2
    assert body["range"]["start_date"] == WEEK_START.isoformat()


async def test_the_workload_ratio_is_null_when_no_availability_is_declared(client, db_session):
    """No rules is *unconfigured*, not "no working hours".

    The schema documents this as ``None`` rather than 0 for exactly that reason,
    and a 0 here would be a confident claim about somebody's week.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "workload", headers, **_window(WEEK_START, WEEK_END))

    assert body["scheduled_minutes"] == 330
    assert body["available_minutes"] is None
    assert body["workload_ratio"] is None
    # The average is still derivable: it needs only the window and the rows.
    assert body["average_daily_scheduled_minutes"] == pytest.approx(47.14, abs=1e-9)
    assert body["available"] is True


async def test_the_workload_compares_actual_against_planned_for_every_day(client, db_session):
    """One comparison point per day, labelled by its ISO date.

    The workload panel's per-day row is actual-versus-planned rather than
    period-over-period, and the seeded week planned exactly what it ran, so
    every change is zero and no percentage can be divided.
    """
    seed, headers = await seeded_client(client, db_session)
    await _seed_week(seed)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "workload", headers, **_window(WEEK_START, WEEK_END))

    assert [point["label"] for point in body["comparison"]] == [
        (WEEK_START + timedelta(days=offset)).isoformat() for offset in range(WEEK_WINDOW_DAYS)
    ]
    assert sum(point["current"] for point in body["comparison"]) == 330.0
    assert sum(point["absolute_change"] for point in body["comparison"]) == 0.0
    # Every session was planned for exactly the time it ran, so a worked day
    # changes by 0% — while an idle day divides 0 by 0 and must answer null.
    for point in body["comparison"]:
        if point["current"] == 0:
            assert point["percent_change"] is None, point["label"]
        else:
            assert point["percent_change"] == 0.0, point["label"]


# -- Time distribution -------------------------------------------------------


async def test_the_time_distribution_shares_sum_to_a_hundred(client, db_session):
    """90 + 60 + 30 of 180 minutes is 50% + 33.3333% + 16.6667%.

    A session with no project is reported as **Unassigned** rather than folded
    into an arbitrary "other" project: a session is either attributable or it
    is not, and inventing a bucket for it would overstate every real project.
    """
    seed, headers = await seeded_client(client, db_session)
    atlas = await seed.project(name="atlas")
    borealis = await seed.project(name="borealis")

    for minutes in (30, 30, 30):
        await seed.work_session(day=WEEK_START, minutes=minutes, project_id=atlas.id)
    await seed.work_session(day=DAY + timedelta(days=1), minutes=60, project_id=borealis.id)
    await seed.work_session(day=DAY + timedelta(days=2), minutes=30)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    body = await _get(client, "time", headers, **_window(WEEK_START, WEEK_END))

    assert body["available"] is True
    assert body["total_minutes"] == 180
    assert body["unassigned_minutes"] == 30
    assert [
        (bucket["key"], bucket["label"], bucket["minutes"], bucket["share"])
        for bucket in body["by_project"]
    ] == [
        (str(atlas.id), "atlas", 90, 50.0),
        (str(borealis.id), "borealis", 60, 33.3333),
        ("unassigned", "Unassigned", 30, 16.6667),
    ]
    # Descending by minutes, and the shares account for every minute of the
    # total rather than being independent per bucket.
    assert sum(bucket["minutes"] for bucket in body["by_project"]) == body["total_minutes"]
    assert sum(bucket["share"] for bucket in body["by_project"]) == pytest.approx(100.0, abs=1e-3)


async def test_the_time_distribution_filters_to_one_project(client, non_raising_client, db_session):
    """``?project_id=`` should narrow the total *and* every share with it.

    Uses ``non_raising_client`` so a server error would be observable as a
    response rather than re-raised inside the test — which is how this route's
    validation failure surfaced in the first place.
    """
    seed, headers = await seeded_client(client, db_session)
    atlas = await seed.project(name="atlas")
    borealis = await seed.project(name="borealis")
    await seed.work_session(day=WEEK_START, minutes=30, project_id=atlas.id)
    await seed.work_session(day=WEEK_START, minutes=30, project_id=borealis.id)
    await seed.work_session(day=WEEK_START, minutes=30)

    response = await non_raising_client.get(
        "/api/v1/analytics/time",
        params={**_window(WEEK_START, WEEK_END), "project_id": str(atlas.id)},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["project_id"] == str(atlas.id)
    assert body["total_minutes"] == 30
    assert [
        (bucket["label"], bucket["minutes"], bucket["share"]) for bucket in body["by_project"]
    ] == [("atlas", 30, 100.0)]


async def test_another_accounts_project_is_a_404_not_a_403(client, db_session):
    """A foreign ``project_id`` is refused as a row that does not exist.

    Resolved through the owner-scoped lookup, so the route cannot be used to
    learn which project ids are real — identical to an id nobody ever issued.
    """
    _seed, headers = await seeded_client(client, db_session)
    other, _other_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign = await other.project(name="borrowed")

    response = await client.get(
        "/api/v1/analytics/time",
        params={**_window(WEEK_START, WEEK_END), "project_id": str(foreign.id)},
        headers=headers,
    )

    assert response.status_code == 404, response.text


# -- No activity at all ------------------------------------------------------


async def test_the_whole_surface_answers_two_hundred_with_nothing_recorded(client, db_session):
    """A brand-new account gets 200 from every route, never a 500.

    "Never crash because there is no activity" is a data-quality requirement the
    brief states outright, and a 500 here would be a dashboard that is unusable
    precisely when a new user first opens it.
    """
    _seed, headers = await seeded_client(client, db_session)
    await _rebuild(client, headers, start=WEEK_START, end=WEEK_END)

    overview = await _get(client, "overview", headers, **_window(WEEK_START, WEEK_END))
    workload = await _get(client, "workload", headers, **_window(WEEK_START, WEEK_END))
    time_body = await _get(client, "time", headers, **_window(WEEK_START, WEEK_END))
    trends = await _get(
        client, "trends", headers, metric="tasks_completed", **_window(WEEK_START, WEEK_END)
    )
    series = await _get(client, "series", headers, **_window(WEEK_START, WEEK_END))

    assert overview["reason_if_empty"].startswith("Not enough activity yet")
    assert all(point["current"] == 0.0 for point in overview["totals"])

    assert workload["scheduled_minutes"] == 0
    assert workload["available_minutes"] is None
    assert workload["workload_ratio"] is None
    assert workload["average_daily_scheduled_minutes"] is None
    assert workload["available"] is False
    assert workload["reason_if_unavailable"].startswith("Not enough activity yet")

    assert time_body["available"] is False
    assert time_body["total_minutes"] == 0
    assert time_body["by_project"] == []
    assert time_body["reason_if_unavailable"].startswith("Not enough activity yet")

    assert trends == []
    # The rebuild wrote one aggregate row per day even though nothing was
    # recorded on any of them: "nothing happened on Tuesday" is an observation,
    # so the series is seven real zeroes rather than seven missing days.
    assert len(series) == WEEK_WINDOW_DAYS
    assert all(row[metric] == 0 for row in series for metric in WEEK_TOTALS)
