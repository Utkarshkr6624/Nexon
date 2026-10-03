"""Phase 4, end to end: what the planner refuses, splits, merges and reports.

Phase 4 had no test file at all. ``planner_service``, ``scheduling_service`` and
``planner.py`` were imported by nothing, so every rule in the calendar module —
the overlap rule, the day bucketing, the availability arithmetic, the truncation
ceiling — was unexercised code that six separate routes depend on. This file is
the one that now pins them.

Every test here corresponds to a defect an audit demonstrated against the running
service, and every expected figure is derived in the test's own docstring from
the fixture, never recorded from a run. The clock is the **database** one
(``func.now()``) throughout, never ``datetime.now()`` and never
``date.today()``: this host is +05:30, so for five and a half hours a day a
server-local "today" is a day ahead of UTC and a fixture anchored to it would
land in the wrong bucket.

House style, from ``tests/test_developer_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test needs the live
  PostgreSQL the suite truncates between tests.
* The planner and scheduling services are hand-wired in a module-level
  ``_service()`` helper mirroring ``app.api.deps``, with the **real** activity
  sink, so no collaborator is quietly ``None``.
* Rows are read back through **explicit column tuples**, never ORM entities.
  This session is the one that wrote them, so an entity read returns whatever
  the identity map is holding rather than what is stored.
* Test names are full English sentences and state the claim, not the mechanism.

The nine defects, and where each is pinned
-----------------------------------------
1. Nothing stopped double-booking. :func:`PlannerService.create_session` now
   refuses a window that overlaps a live session or a calendar event, with a
   409 naming the row in the way. Pinned by
   :func:`test_a_work_session_that_would_overlap_another_is_refused_and_names_it`,
   :func:`test_a_work_session_may_not_be_booked_over_a_calendar_event`, and the
   three guards around them (back-to-back, cancelled, another account).
2. A session crossing midnight was bucketed by its start day alone, so
   ``/planner/day`` and ``/work-sessions`` disagreed about the same session.
   Pinned by :func:`test_a_session_crossing_midnight_is_split_between_the_two_days_it_touches`.
3. ``_fully_covered`` judged every day of a session against the *start* day's
   windows, so every midnight-crossing session was reported
   ``outside_availability``. Pinned by
   :func:`test_a_session_crossing_midnight_is_judged_against_each_days_own_windows`.
4. Overlapping availability windows were summed rather than merged, so a day
   declared 09:00-17:00 with a nested 11:00-13:00 rule reported 600 available
   minutes instead of 480. Pinned by
   :func:`test_overlapping_availability_windows_are_merged_before_they_are_summed`.
5. ``MAX_RANGE_ROWS`` truncated the range reads and the scheduler's busy list in
   silence. Pinned by
   :func:`test_the_month_view_refuses_a_span_it_cannot_read_completely` and
   :func:`test_the_scheduler_will_not_plan_against_a_calendar_it_could_not_read`.
6. ``PATCH {"starts_at": null}`` raised ``AttributeError`` — a 500 where the
   schema promised a 422. Pinned by
   :func:`test_a_null_window_end_is_a_422_rather_than_a_server_error`.
7. ``actual_minutes``/``actual_start``/``actual_end`` were accepted by the
   payload schema and dropped by the service. Pinned by
   :func:`test_a_patch_claiming_measured_minutes_is_refused_rather_than_ignored`.
8. ``/planner/conflicts`` was O(n²) with no cap. Pinned by
   :func:`test_the_conflict_list_is_capped_and_says_so_instead_of_growing_without_end`.
9. ``accept()`` re-checked task ownership but not whether the slot was still
   free. Pinned by
   :func:`test_accepting_a_stale_suggestion_is_refused_because_the_slot_was_taken`.

Three tests pin the judgements those fixes required, where "obvious" was not good
enough on its own: a PATCH that moves a block is checked like a create but must
exclude the row under edit
(:func:`test_moving_a_session_onto_another_one_is_refused_while_moving_it_within_a_free_slot_is_not`),
two simultaneous writes for one slot produce one row
(:func:`test_two_simultaneous_reservations_of_one_slot_produce_one_session`),
and local midnight — which the availability schema cannot express — is not
itself a violation
(:func:`test_local_midnight_is_not_reported_as_work_outside_declared_availability`).

Test 9 is driven through the service rather than HTTP: there is no accept route,
which is itself worth knowing — the engine's only caller of ``accept`` is
future work, and a bug fixed only in that path would have had no test at all.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError
from app.models.planner import CalendarEvent, WorkSession
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.planner import PlannerSuggestion, WorkSessionCreate
from app.services.activity_service import ActivityService
from app.services.planner_service import MAX_RANGE_ROWS, PlannerService
from app.services.scheduling_service import (
    MAX_CANDIDATES,
    MAX_CONFLICTS,
    SchedulingService,
)
from tests.analytics_fixtures import DAY, AnalyticsSeed, seeded_client

pytestmark = pytest.mark.integration

#: The zone every arithmetic test below is written in.
#:
#: UTC, deliberately, and not the configured default: the audit defects are
#: about *day boundaries*, and a zone with a whole-hour offset would let a bug
#: that shifts a session by a few hours hide behind the shift. In UTC a session
#: from 23:30 to 01:00 touches two days and no others, and the expected split is
#: 30 minutes on the first and 60 on the second.
ZONE = "UTC"

#: A Monday, so ``weekday`` arithmetic in the availability fixtures is checkable
#: by hand: 2026-01-05 is a Monday, which is the anchor ``DAY`` names.
MONDAY = DAY

#: The columns every read-back below projects. ``id`` is included because the
#: conflict refusals are asserted against it; nothing else here is generated per
#: row and nothing else is worth checking.
SESSION_COLUMNS = (
    WorkSession.id,
    WorkSession.status,
    WorkSession.scheduled_start,
    WorkSession.scheduled_end,
    WorkSession.actual_minutes,
)
EVENT_COLUMNS = (
    CalendarEvent.id,
    CalendarEvent.title,
    CalendarEvent.starts_at,
    CalendarEvent.ends_at,
)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _planner(session: AsyncSession, *, settings: Settings | None = None) -> PlannerService:
    """A planner service wired the way ``app.api.deps`` wires it.

    Every collaborator is the real one, including the activity sink: the sink is
    what ``CALENDAR_EVENT_CREATED`` and ``WORK_SESSION_COMPLETED`` are written
    through, and passing ``None`` would make every assertion about them vacuous.
    """
    return PlannerService(
        CalendarEventRepository(session),
        WorkSessionRepository(session),
        AvailabilityRuleRepository(session),
        ProjectRepository(session),
        TaskRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


def _scheduling(session: AsyncSession, *, settings: Settings | None = None) -> SchedulingService:
    """A scheduling engine over the planner above — the ``app.api.deps`` shape."""
    return SchedulingService(
        _planner(session, settings=settings),
        TaskRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read ``PlannerService._db_now`` performs. Every fixture anchored to
    "now" is anchored to *this*, so the rows a test writes and the rows the
    service will compute against describe the same instant.
    """
    read = await session.scalar(select(func.now()))
    return read if read.tzinfo is not None else read.replace(tzinfo=UTC)


async def _session_rows(session: AsyncSession, owner_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every session the owner holds, read through explicit columns.

    The projection is deliberate: this session also writes the rows, so an ORM
    read would return the identity map's objects and an "unchanged" assertion
    would compare stale state with itself.
    """
    result = await session.execute(
        select(*SESSION_COLUMNS)
        .where(WorkSession.owner_id == owner_id)
        .order_by(WorkSession.scheduled_start.asc(), WorkSession.id.asc())
    )
    return list(result.all())


async def _event_rows(session: AsyncSession, owner_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every calendar event the owner holds, through explicit columns."""
    result = await session.execute(
        select(*EVENT_COLUMNS)
        .where(CalendarEvent.owner_id == owner_id)
        .order_by(CalendarEvent.starts_at.asc(), CalendarEvent.id.asc())
    )
    return list(result.all())


def _session_payload(
    start: datetime, end: datetime, *, task_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """The JSON body of a session reservation, instants as ISO strings."""
    return {
        "scheduled_start": start.isoformat(),
        "scheduled_end": end.isoformat(),
        "task_id": str(task_id) if task_id else None,
    }


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    """An aware UTC instant on ``day`` — the form every payload below sends."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _wall(day: date, hour: int, minute: int = 0) -> str:
    """An availability rule's wall-clock time, which is never an instant."""
    return time(hour, minute).isoformat()


async def _declare(
    client: Any, auth: dict[str, str], rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replace the caller's whole week and return the response body."""
    response = await client.put("/api/v1/availability", json={"rules": rules}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def _put_events(
    session: AsyncSession, owner_id: uuid.UUID, rows: list[tuple[datetime, datetime]]
) -> None:
    """Write calendar events straight into the table, in one statement.

    Bypasses the service on purpose. The rows here exist to be *counted* —
    1,001 of them is the input to the truncation rule — and creating them
    through ``POST /calendar`` would spend a thousand round trips proving a
    ceiling on a read. ``id`` is supplied because it is generated by the
    application, not by the database.
    """
    await session.execute(
        insert(CalendarEvent),
        [
            {
                "id": uuid.uuid4(),
                "owner_id": owner_id,
                "title": f"bulk {index}",
                "starts_at": start,
                "ends_at": end,
                "event_type": "other",
                "all_day": False,
                "created_at": start,
                "updated_at": start,
            }
            for index, (start, end) in enumerate(rows)
        ],
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 1. Nothing prevented double-booking
# ---------------------------------------------------------------------------


async def test_a_work_session_that_would_overlap_another_is_refused_and_names_it(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """A second session over the first is a 409 naming the first, and stores nothing.

    **The fixture.** Monday 2026-01-05, 09:00-10:00 UTC reserved, then a second
    reservation for 09:30-10:30.

    **Why this was broken.** ``create_session`` checked the references in the
    payload and the direction of the window, and nothing else. The audit created
    two overlapping sessions and both answered **201**: the second row was
    written and no surface complained until ``GET /planner/conflicts`` reported
    it, which is a diagnostic rather than a rule.

    **What is asserted.**

    * The second write is **409** with the shared envelope's ``conflict`` code.
    * ``details.conflicting_id`` is the id of the *first* session, so a client
      can offer to move that block rather than guess which one is in the way.
    * ``details.conflicting_start``/``conflicting_end`` are the first session's
      own window, 09:00 and 10:00 — the overlap named is the row that blocked
      it, not the row that was refused.
    * Exactly **one** session exists afterwards, read back through an explicit
      column projection: a refusal that wrote a row and then reported a conflict
      would satisfy the first three assertions and none of the user's.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    first = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    clash = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9, 30), _utc(MONDAY, 10, 30)),
        headers=auth,
    )
    error = assert_error_envelope(clash, status_code=409, code="conflict")
    assert error["details"]["conflict"] == "work_session"
    assert error["details"]["conflicting_id"] == first_id
    assert error["details"]["conflicting_start"].endswith("T09:00:00+00:00")
    assert error["details"]["conflicting_end"].endswith("T10:00:00+00:00")

    rows = await _session_rows(db_session, owner.id)
    assert len(rows) == 1
    assert str(rows[0][0]) == first_id


async def test_a_work_session_may_not_be_booked_over_a_calendar_event(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """A session inside an event's window is a 409 naming the event.

    **The fixture.** Monday 2026-01-05, a calendar event 09:00-10:00, then a
    work session for 09:30-10:30.

    **Why this is a double-booking.** Every other surface already treats that
    interval as spoken for: the scheduler's busy list skips events, and
    ``detect_conflicts`` reports a session placed over one as a conflict the
    user is asked to resolve. The write path was the one place the rule was not
    applied, so the conflict was discovered after the fact rather than prevented.

    **What is asserted.** The refusal is 409, ``details.conflict`` is
    ``calendar_event``, ``details.conflicting_id`` is the event's id and
    ``details.conflicting_title`` its title, and **zero** work sessions exist —
    the meeting was not booked over by anything.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    event = await client.post(
        "/api/v1/calendar",
        json={
            "title": "Design review",
            "starts_at": _utc(MONDAY, 9).isoformat(),
            "ends_at": _utc(MONDAY, 10).isoformat(),
        },
        headers=auth,
    )
    assert event.status_code == 201, event.text
    event_id = event.json()["id"]

    clash = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9, 30), _utc(MONDAY, 10, 30)),
        headers=auth,
    )
    error = assert_error_envelope(clash, status_code=409, code="conflict")
    assert error["details"]["conflict"] == "calendar_event"
    assert error["details"]["conflicting_id"] == event_id
    assert error["details"]["conflicting_title"] == "Design review"
    assert await _session_rows(db_session, owner.id) == []


async def test_back_to_back_sessions_are_not_treated_as_a_collision(
    client, db_session: AsyncSession
) -> None:
    """09:00-10:00 and 10:00-11:00 are both stored: overlap is half-open.

    **The fixture.** Two one-hour reservations on Monday 2026-01-05 that touch
    at exactly 10:00.

    **Why the boundary matters.** ``BETWEEN`` semantics would call this a
    collision, and a calendar built out of blocks would then be impossible to
    fill: two meetings, a lunch and a work session — every one of them adjacent
    to the next. The repositories have always compared ``starts_at < end AND
    ends_at > start``; this test pins that the new **write-side** check uses the
    same predicate rather than a stricter one of its own.

    **What is asserted.** Both writes are 201 and both rows are stored, with the
    second starting exactly where the first ends.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    morning = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    afternoon = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 10), _utc(MONDAY, 11)),
        headers=auth,
    )
    assert morning.status_code == 201, morning.text
    assert afternoon.status_code == 201, afternoon.text

    rows = await _session_rows(db_session, owner.id)
    assert len(rows) == 2
    assert rows[0][3] == rows[1][2]


async def test_a_cancelled_session_releases_the_slot_it_was_holding(
    client, db_session: AsyncSession
) -> None:
    """Cancelling a block frees it: the replacement reservation is accepted.

    **The fixture.** 09:00-10:00 reserved, cancelled through the PATCH that
    cancellation is documented to use, then 09:00-10:00 reserved again.

    **Why.** A cancelled block holds no time — the repository's own listing
    excludes it from every capacity figure — so treating it as reserved would
    make releasing a booking impossible through the API that documents releasing
    it. The overlap check excludes cancelled rows for exactly that reason.

    **What is asserted.** The second reservation is 201 and the owner's live
    rows are exactly one: the replacement. (The cancelled row is still stored;
    cancellation is not deletion, which is why the count is taken from the
    response rather than from a total of two.)
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    first = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    assert first.status_code == 201, first.text

    cancelled = await client.patch(
        f"/api/v1/work-sessions/{first.json()['id']}",
        json={"status": "cancelled"},
        headers=auth,
    )
    assert cancelled.status_code == 200, cancelled.text

    replacement = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    assert replacement.status_code == 201, replacement.text

    rows = await _session_rows(db_session, owner.id)
    assert [row[1] for row in rows].count("cancelled") == 1
    assert len([row for row in rows if row[1] != "cancelled"]) == 1


async def test_another_accounts_session_does_not_block_a_write_and_reads_as_not_found(
    client, db_session: AsyncSession
) -> None:
    """Two accounts may hold the same hour; neither can read the other's row.

    **The fixture.** Account A reserves Monday 09:00-10:00. Account B reserves
    the same instant, then tries to fetch, edit and delete A's session.

    **Why both halves are in one test.** The overlap check is scoped by
    ``owner_id`` in the query, exactly like every read, so it cannot refuse a
    write because of somebody else's calendar — that would leak the existence of
    another account's booking as a 409. And the rule that another account's row
    is **404, never 403**, identical to an id nobody issued, still holds: the
    same ids that must not block a write must not be readable either.

    **What is asserted.** B's reservation is 201 at the identical window; A's id
    answers 404 on GET, PATCH and DELETE; and an id that was never issued
    answers 404 with the same code and message shape, so the two are
    indistinguishable.
    """
    _seed_a, auth_a = await seeded_client(client, db_session, username="ada")
    _seed_b, auth_b = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )

    created_a = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth_a,
    )
    assert created_a.status_code == 201, created_a.text
    session_a = created_a.json()["id"]

    created_b = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth_b,
    )
    assert created_b.status_code == 201, created_b.text

    for method, url, body in (
        ("get", f"/api/v1/work-sessions/{session_a}", None),
        ("patch", f"/api/v1/work-sessions/{session_a}", {"status": "cancelled"}),
        ("delete", f"/api/v1/work-sessions/{session_a}", None),
    ):
        response = await client.request(method, url, json=body, headers=auth_b)
        assert response.status_code == 404, (method, response.text)

    unknown = uuid.uuid4()
    cross = await client.get(f"/api/v1/work-sessions/{unknown}", headers=auth_b)
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "not_found"


async def test_moving_a_session_onto_another_one_is_refused_while_moving_it_within_a_free_slot_is_not(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """A PATCH that re-books a block is checked like a create, excluding the row itself.

    **The fixture.** Monday 2026-01-05 holds 09:00-10:00 and 13:00-14:00. The
    afternoon block is then PATCHed twice: once onto 09:30-10:30, and once to
    13:30-14:30.

    **Why the exclusion matters as much as the check.** A session overlaps
    *itself* on every instant of its own window, so a collision check that
    cannot exclude the row under edit refuses every reschedule — including a
    no-op PATCH that names the times the row already has. The exclusion is what
    makes "move" possible at all, and it is why the first PATCH names a *different*
    block and the second names none.

    **What is asserted.** The move onto 09:30-10:30 is a 409 naming the 09:00
    block; the move to 13:30-14:30 is a 200 with both stored windows reflecting
    it; and a PATCH that re-sends the window the row already has is a 200,
    because it conflicts with nothing but itself.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    morning = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    afternoon = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 13), _utc(MONDAY, 14)),
        headers=auth,
    )
    assert morning.status_code == 201, morning.text
    assert afternoon.status_code == 201, afternoon.text
    url = f"/api/v1/work-sessions/{afternoon.json()['id']}"

    clash = await client.patch(
        url,
        json={
            "scheduled_start": _utc(MONDAY, 9, 30).isoformat(),
            "scheduled_end": _utc(MONDAY, 10, 30).isoformat(),
        },
        headers=auth,
    )
    error = assert_error_envelope(clash, status_code=409, code="conflict")
    assert error["details"]["conflicting_id"] == morning.json()["id"]

    moved = await client.patch(
        url,
        json={
            "scheduled_start": _utc(MONDAY, 13, 30).isoformat(),
            "scheduled_end": _utc(MONDAY, 14, 30).isoformat(),
        },
        headers=auth,
    )
    assert moved.status_code == 200, moved.text

    unchanged = await client.patch(
        url,
        json={
            "scheduled_start": _utc(MONDAY, 13, 30).isoformat(),
            "scheduled_end": _utc(MONDAY, 14, 30).isoformat(),
        },
        headers=auth,
    )
    assert unchanged.status_code == 200, unchanged.text

    stored = {str(row[0]): row for row in await _session_rows(db_session, owner.id)}
    assert stored[afternoon.json()["id"]][2] == _utc(MONDAY, 13, 30)
    assert stored[afternoon.json()["id"]][3] == _utc(MONDAY, 14, 30)


async def test_two_simultaneous_reservations_of_one_slot_produce_one_session(
    client, db_session: AsyncSession
) -> None:
    """Two concurrent POSTs for the same hour yield one 201, one 409 and one row.

    **The fixture.** Monday 2026-01-05, 09:00-10:00 UTC, reserved by two requests
    issued together with :func:`asyncio.gather`.

    **Why this is a separate test.** Checking for an overlap and writing the row
    are two statements, so two requests that overlap in time can both read an
    empty calendar and both write — the double-booking the rule exists to
    prevent, reached by a different road than the sequential one. The service
    serialises the check per owner with a transaction-scoped advisory lock, so
    the second writer reads the first's committed row and is refused.

    **What is asserted.** Exactly one response is 201 and exactly one is 409,
    and the owner holds exactly **one** session. The statuses are counted rather
    than matched to a particular request because which of the two wins is a
    scheduling accident; the invariant is that only one can.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/work-sessions",
                json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
                headers=auth,
            )
            for _ in range(2)
        )
    )
    codes = sorted(response.status_code for response in responses)
    assert codes == [201, 409], [response.text for response in responses]
    assert len(await _session_rows(db_session, owner.id)) == 1


# ---------------------------------------------------------------------------
# 2. A session crossing midnight was bucketed by its start day alone
# ---------------------------------------------------------------------------


async def test_a_session_crossing_midnight_is_split_between_the_two_days_it_touches(
    client, db_session: AsyncSession
) -> None:
    """23:30-01:00 reports 30 minutes on Monday and 60 on Tuesday, in both views.

    **The fixture.** One work session, Monday 2026-01-05 23:30 UTC to Tuesday
    2026-01-06 01:00 UTC. Ninety minutes long.

    **The arithmetic.** 23:30 to 00:00 is **30** minutes and falls on Monday;
    00:00 to 01:00 is **60** minutes and falls on Tuesday; 30 + 60 = 90, which is
    the session's own length and is the check that makes the split a split rather
    than two independent numbers.

    **Why this was broken.** The planner bucketed a row under its **local start
    day**. So Monday's day view credited 30 minutes, and the same session's
    remaining 60 minutes appeared on no day at all — while
    ``GET /work-sessions`` filters on *overlap* and listed it against both days.
    The two surfaces of the same module disagreed about the same row, and any
    total built from the day view was 60 minutes short of the truth.

    **What is asserted.** Monday's ``scheduled_minutes`` is 30 and Tuesday's is
    60; the session appears in both days' ``sessions`` lists (which is what makes
    Monday's number 30 rather than 90); and ``GET /work-sessions`` lists it in
    both windows, so the two surfaces now agree.
    """
    _seed, auth = await seeded_client(client, db_session)
    tuesday = MONDAY + timedelta(days=1)

    created = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 23, 30), _utc(tuesday, 1)),
        headers=auth,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    monday = await client.get(
        f"/api/v1/planner/day?date={MONDAY.isoformat()}&tz={ZONE}", headers=auth
    )
    tues = await client.get(
        f"/api/v1/planner/day?date={tuesday.isoformat()}&tz={ZONE}", headers=auth
    )
    assert monday.status_code == 200, monday.text
    assert tues.status_code == 200, tues.text

    monday_day, tuesday_day = monday.json(), tues.json()
    assert monday_day["scheduled_minutes"] == 30
    assert tuesday_day["scheduled_minutes"] == 60
    assert monday_day["scheduled_minutes"] + tuesday_day["scheduled_minutes"] == 90
    assert [row["id"] for row in monday_day["sessions"]] == [session_id]
    assert [row["id"] for row in tuesday_day["sessions"]] == [session_id]

    for day in (MONDAY, tuesday):
        listing = await client.get(
            "/api/v1/work-sessions",
            params={
                "from": _utc(day, 0).isoformat(),
                "to": _utc(day + timedelta(days=1), 0).isoformat(),
            },
            headers=auth,
        )
        assert listing.status_code == 200, listing.text
        assert session_id in [row["id"] for row in listing.json()["items"]]


# ---------------------------------------------------------------------------
# 3. A midnight-crossing session was always reported outside availability
# ---------------------------------------------------------------------------


async def test_a_session_crossing_midnight_is_judged_against_each_days_own_windows(
    client, db_session: AsyncSession
) -> None:
    """Availability is checked per day, so a covered Friday-to-Saturday block is clean.

    **The fixture.** Friday 2026-01-09 22:00-23:59 declared, Saturday
    2026-01-10 00:00-02:00 declared, and a session from Friday 23:30 to
    Saturday 01:00.

    **Why this was broken.** ``_fully_covered`` was handed the windows of the
    session's **start** day and then tested every day of the session against
    them. The Saturday minutes were therefore measured against Friday's
    22:00-23:59, which they are not inside, and *every* session crossing local
    midnight was reported ``outside_availability`` — a false alarm on all of
    them, which is a rule nobody can act on.

    **What is asserted.** With each day covered by its own rule the span over
    Friday 2026-01-09 to Saturday 2026-01-10 reports **no** conflict of kind
    ``outside_availability``. The kind is checked rather than the whole list
    because a conflict list may legitimately hold others.
    """
    _seed, auth = await seeded_client(client, db_session)
    friday = date(2026, 1, 9)
    saturday = date(2026, 1, 10)

    await _declare(
        client,
        auth,
        [
            {
                "weekday": friday.weekday(),
                "starts_at": _wall(friday, 22),
                "ends_at": _wall(friday, 23, 59),
            },
            {
                "weekday": saturday.weekday(),
                "starts_at": _wall(saturday, 0),
                "ends_at": _wall(saturday, 2),
            },
        ],
    )

    created = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(friday, 23, 30), _utc(saturday, 1)),
        headers=auth,
    )
    assert created.status_code == 201, created.text

    scan = await client.get(
        "/api/v1/planner/conflicts",
        params={"start": friday.isoformat(), "end": saturday.isoformat(), "tz": ZONE},
        headers=auth,
    )
    assert scan.status_code == 200, scan.text
    body = scan.json()
    assert [c for c in body["conflicts"] if c["kind"] == "outside_availability"] == []
    assert body["truncated"] is False


async def test_a_session_beyond_its_own_days_windows_is_still_reported_with_both_days_named(
    client, db_session: AsyncSession
) -> None:
    """A block that overruns Saturday's rule is a conflict whose evidence names Saturday.

    **The fixture.** Friday 2026-01-09 22:00-23:59 and Saturday
    2026-01-10 02:00-03:00 declared — so Saturday's 00:00-01:00 is *not* covered
    — and a session from Friday 23:30 to Saturday 01:00.

    **Why this test exists as well as the previous one.** Fixing a false alarm by
    ignoring the second day would be just as wrong as the original. The rule has
    to hold in both directions: each day is judged against its own windows, so a
    genuine overrun is still caught, and the evidence names **which** day was
    uncovered — the sentence a user needs to know whether to move the block or
    to extend Saturday.

    **What is asserted.** Exactly one ``outside_availability`` conflict, whose
    ``evidence.declared`` names both declared days (Friday's 22:00-23:59 and
    Saturday's 02:00-03:00) and whose ``evidence.local_dates`` is Friday and
    Saturday.
    """
    _seed, auth = await seeded_client(client, db_session)
    friday = date(2026, 1, 9)
    saturday = date(2026, 1, 10)

    await _declare(
        client,
        auth,
        [
            {
                "weekday": friday.weekday(),
                "starts_at": _wall(friday, 22),
                "ends_at": _wall(friday, 23, 59),
            },
            {
                "weekday": saturday.weekday(),
                "starts_at": _wall(saturday, 2),
                "ends_at": _wall(saturday, 3),
            },
        ],
    )

    created = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(friday, 23, 30), _utc(saturday, 1)),
        headers=auth,
    )
    assert created.status_code == 201, created.text

    scan = await client.get(
        "/api/v1/planner/conflicts",
        params={"start": friday.isoformat(), "end": saturday.isoformat(), "tz": ZONE},
        headers=auth,
    )
    assert scan.status_code == 200, scan.text
    outside = [c for c in scan.json()["conflicts"] if c["kind"] == "outside_availability"]
    assert len(outside) == 1
    evidence = outside[0]["evidence"]
    assert evidence["local_dates"] == [friday.isoformat(), saturday.isoformat()]
    assert {(row["date"], row["starts_at"]) for row in evidence["declared"]} == {
        (friday.isoformat(), "22:00-23:59"),
        (saturday.isoformat(), "02:00-03:00"),
    }


async def test_local_midnight_is_not_reported_as_work_outside_declared_availability(
    client, db_session: AsyncSession
) -> None:
    """A block running to midnight is clean if it began in hours; one that began after is not.

    **The fixture.** Monday 2026-01-05 declares 09:00-17:00 and nothing else.
    First a session from Monday 16:00 to Tuesday 00:00, checked; then — after
    cancelling it, so the two do not overlap each other — one from Monday 20:00
    to Tuesday 00:00.

    **Why this judgement is needed at all.** ``availability_rules`` refuses a
    window whose ``ends_at`` is not after its ``starts_at``, so "09:00 to
    midnight" cannot be written: the best any user can express is 09:00-23:59,
    and a session that runs to the day boundary is therefore outside the last
    declared minute by construction. Judged literally, **every** midnight-crossing
    session is flagged — the defect this file exists to fix, reappearing through a
    different door. Midnight is a calendar artefact, not a working-hour statement,
    so a tail that runs into it is forgiven.

    **What is asserted.** The 16:00 block, which began inside the declared
    window, reports no ``outside_availability``. The 20:00 block, which began
    three hours after availability ended, still does. The second assertion is
    what keeps the first from being "ignore everything after 17:00": the
    exemption is for the boundary alone, not for the hours a user did not declare.
    """
    seed, auth = await seeded_client(client, db_session)
    del seed
    tuesday = MONDAY + timedelta(days=1)

    await _declare(
        client,
        auth,
        [
            {
                "weekday": MONDAY.weekday(),
                "starts_at": _wall(MONDAY, 9),
                "ends_at": _wall(MONDAY, 17),
            }
        ],
    )

    async def _outside_availability_conflicts() -> list[dict[str, Any]]:
        scan = await client.get(
            "/api/v1/planner/conflicts",
            params={"start": MONDAY.isoformat(), "end": tuesday.isoformat(), "tz": ZONE},
            headers=auth,
        )
        assert scan.status_code == 200, scan.text
        return [c for c in scan.json()["conflicts"] if c["kind"] == "outside_availability"]

    in_hours = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 16), _utc(tuesday, 0)),
        headers=auth,
    )
    assert in_hours.status_code == 201, in_hours.text
    assert await _outside_availability_conflicts() == []

    released = await client.patch(
        f"/api/v1/work-sessions/{in_hours.json()['id']}",
        json={"status": "cancelled"},
        headers=auth,
    )
    assert released.status_code == 200, released.text

    after_hours = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 20), _utc(tuesday, 0)),
        headers=auth,
    )
    assert after_hours.status_code == 201, after_hours.text
    assert len(await _outside_availability_conflicts()) == 1


# ---------------------------------------------------------------------------
# 4. Overlapping availability windows were summed, not merged
# ---------------------------------------------------------------------------


async def test_overlapping_availability_windows_are_merged_before_they_are_summed(
    client, db_session: AsyncSession
) -> None:
    """A nested rule does not double-count: 09:00-17:00 plus 11:00-13:00 is 480 minutes.

    **The fixture.** Monday 2026-01-05 declares 09:00-17:00 and, inside it,
    11:00-13:00. A 240-minute work session is booked on Monday.

    **The arithmetic.** 09:00-17:00 is **480** minutes; 11:00-13:00 is **120**,
    wholly contained in the first. Added naively the day reports **600** minutes
    of availability — an eleven-hour day that was declared as an eight-hour one.
    The union is **480**.

    **Why it matters beyond the number.** Every downstream figure is a ratio of
    scheduled time to available time, so 600 instead of 480 halves ``ratio``
    (0.4167 becomes 0.5) and turns an ordinary day into one that looks half
    empty. The audit measured ``available_minutes`` of 960 for an eleven-hour
    day, and the same defect sits underneath every overload badge in the UI.

    **What is asserted.** ``available_minutes`` is 480, not 600; ``ratio`` is
    240 / 480 = 0.5; and the session is reported on that day, so the ratio is a
    measurement rather than a consequence of an empty calendar.
    """
    _seed, auth = await seeded_client(client, db_session)

    await _declare(
        client,
        auth,
        [
            {
                "weekday": MONDAY.weekday(),
                "starts_at": _wall(MONDAY, 9),
                "ends_at": _wall(MONDAY, 17),
            },
            {
                "weekday": MONDAY.weekday(),
                "starts_at": _wall(MONDAY, 11),
                "ends_at": _wall(MONDAY, 13),
            },
        ],
    )

    booked = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 13)),
        headers=auth,
    )
    assert booked.status_code == 201, booked.text

    day = await client.get(f"/api/v1/planner/day?date={MONDAY.isoformat()}&tz={ZONE}", headers=auth)
    assert day.status_code == 200, day.text
    body = day.json()
    assert body["available_minutes"] == 480
    assert body["scheduled_minutes"] == 240
    assert body["ratio"] == 0.5
    assert body["overloaded"] is False


# ---------------------------------------------------------------------------
# 5. Truncation was silent
# ---------------------------------------------------------------------------


async def test_the_month_view_refuses_a_span_it_cannot_read_completely(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """A month holding 1,001 events answers 422 with the count, not 1,000 rows.

    **The fixture.** March 2026, with :data:`MAX_RANGE_ROWS` + 1 = 1,001
    one-minute calendar events on the first day of the month. That is one row
    more than the internal ceiling, and the audit used 1,100 to demonstrate the
    same thing.

    **Why a refusal and not a flag.** The read is used to *decide* something —
    a month grid shows which days are free — and a day that is missing rows
    looks exactly like a free day. There is no honest way to render a truncated
    month: the client cannot tell which days were affected without the same
    total the server has. A 422 naming the count tells it to ask for a smaller
    span, which is the only thing that actually helps.

    **What is asserted.** The response is 422 with the ``validation_error``
    code; ``details.rows_matched`` is 1001, ``details.rows_read`` is 1000 and
    ``details.max_range_rows`` is :data:`MAX_RANGE_ROWS` — so the refusal states
    the arithmetic rather than merely refusing.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    march_first = date(2026, 3, 1)
    rows = [
        (
            _utc(march_first, 0) + timedelta(minutes=2 * index),
            _utc(march_first, 0) + timedelta(minutes=2 * index + 1),
        )
        for index in range(MAX_RANGE_ROWS + 1)
    ]
    await _put_events(db_session, owner.id, rows)
    assert len(await _event_rows(db_session, owner.id)) == MAX_RANGE_ROWS + 1

    month = await client.get(
        "/api/v1/planner/month", params={"month": "2026-03", "tz": ZONE}, headers=auth
    )
    error = assert_error_envelope(month, status_code=422, code="validation_error")
    assert error["details"]["rows_matched"] == MAX_RANGE_ROWS + 1
    assert error["details"]["rows_read"] == MAX_RANGE_ROWS
    assert error["details"]["max_range_rows"] == MAX_RANGE_ROWS


async def test_the_scheduler_will_not_plan_against_a_calendar_it_could_not_read(
    client, db_session: AsyncSession
) -> None:
    """A horizon too dense to read returns no suggestions and says why.

    **The fixture.** A project, one task due tomorrow with a 60-minute estimate,
    and :data:`MAX_CANDIDATES` + 1 = 1,001 back-to-back calendar events starting
    at the database's current instant. The engine's busy list is read with the
    same ceiling the planner uses, so the horizon no longer fits in one read.

    **Why silence here would be dangerous.** The engine walks the busy list to
    find a free slot. Rows it did not read are minutes it cannot see, and every
    one of them is a slot it would offer as free — the audit's 1,100 events
    produced exactly that. Proposing into occupied time is worse than proposing
    nothing, so the engine returns an **empty list with a stated reason**, which
    is the answer this module already gives for every other missing
    precondition.

    **What is asserted.** ``suggestions`` is empty, ``reason_if_empty`` is
    populated and names the row count, and the task's slot was not written:
    the owner still has zero work sessions.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    project = await seed.project()
    await seed.task(
        project_id=project.id,
        due_date=(await _db_now(db_session)).date() + timedelta(days=1),
        estimated_minutes=60,
    )

    now = await _db_now(db_session)
    rows = [
        (now + timedelta(minutes=index), now + timedelta(minutes=index + 1))
        for index in range(MAX_CANDIDATES + 1)
    ]
    await _put_events(db_session, owner.id, rows)

    response = await client.post("/api/v1/planner/suggestions", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestions"] == []
    reason = body["reason_if_empty"]
    assert reason is not None
    assert str(MAX_CANDIDATES + 1) in reason
    assert "partial calendar" in reason
    assert await _session_rows(db_session, owner.id) == []


# ---------------------------------------------------------------------------
# 6. A null window end was a 500
# ---------------------------------------------------------------------------


async def test_a_null_window_end_is_a_422_rather_than_a_server_error(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """``PATCH {"starts_at": null}`` is a 422 naming the field, on both resources.

    **The fixture.** One calendar event at 09:00-10:00 and one work session at
    11:00-12:00 on Monday 2026-01-05 — deliberately disjoint, because a session
    laid over the event would be refused by the overlap rule before it reached
    the PATCH under test. Each is then PATCHed with a single ``null`` window end.

    **Why this was a 500.** The payload schema types both ends as optional, so
    ``null`` passed validation, and ``update_event`` then called
    ``_require_forward_window(fields.get("starts_at", ...), ...)`` with a
    ``None``. ``_aware(None)`` raises ``AttributeError``, which no exception
    handler is registered for. A request the schema had already called
    well-formed left as a server fault.

    **What is asserted.** Each PATCH is a 422 whose ``error.details.field`` names
    the offending key, and neither row moved — read back through explicit columns,
    the event still ends at 10:00 and the session still ends at 12:00.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    event = await client.post(
        "/api/v1/calendar",
        json={
            "title": "Review",
            "starts_at": _utc(MONDAY, 9).isoformat(),
            "ends_at": _utc(MONDAY, 10).isoformat(),
        },
        headers=auth,
    )
    session = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 11), _utc(MONDAY, 12)),
        headers=auth,
    )
    assert event.status_code == 201, event.text
    assert session.status_code == 201, session.text

    for url, field in (
        (f"/api/v1/calendar/{event.json()['id']}", "starts_at"),
        (f"/api/v1/work-sessions/{session.json()['id']}", "scheduled_start"),
    ):
        response = await client.patch(url, json={field: None}, headers=auth)
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        assert error["details"]["field"] == field

    events = await _event_rows(db_session, owner.id)
    sessions = await _session_rows(db_session, owner.id)
    assert events[0][3] == _utc(MONDAY, 10)
    assert sessions[0][3] == _utc(MONDAY, 12)


# ---------------------------------------------------------------------------
# 7. Accepted fields were silently dropped
# ---------------------------------------------------------------------------


async def test_a_patch_claiming_measured_minutes_is_refused_rather_than_ignored(
    client, db_session: AsyncSession, assert_error_envelope
) -> None:
    """``actual_minutes``/``actual_start``/``actual_end`` are a 422, and store nothing.

    **The fixture.** A plain planned session, then a PATCH claiming 600 minutes
    actually worked.

    **Why removal rather than persistence.** These three columns are written by
    the routed ``/start`` and ``/stop`` pair from the **database** clock, so what
    they hold is a measurement. Every surface a user reads — the session card,
    the day totals, and the analytics Phase 10 will train on — reads those same
    columns. Persisting a client-supplied figure would put a number on the
    time-tracking page that no clock produced. The alternative the audit found,
    accepting the field and dropping it, answered **200** and changed nothing:
    a write the caller was told had happened.

    **What is asserted.** The PATCH is a 422 (the schema forbids unknown fields,
    so the refusal arrives at the edge and names it), and the stored
    ``actual_minutes`` is still **0** — read through an explicit column
    projection rather than through the response, so a row that had been written
    and rolled back could not pass this.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    session = await client.post(
        "/api/v1/work-sessions",
        json=_session_payload(_utc(MONDAY, 9), _utc(MONDAY, 10)),
        headers=auth,
    )
    assert session.status_code == 201, session.text
    url = f"/api/v1/work-sessions/{session.json()['id']}"

    for payload in (
        {"actual_minutes": 600},
        {"actual_start": _utc(MONDAY, 9).isoformat()},
        {"actual_end": _utc(MONDAY, 10).isoformat()},
    ):
        (field,) = payload
        response = await client.patch(url, json=payload, headers=auth)
        error = assert_error_envelope(response, status_code=422, code="validation_error")
        # The field is named in the validation errors, not in the envelope's
        # generic message: `field` is the dotted path inside the body.
        named = {entry["field"] for entry in error["details"]["errors"]}
        assert field in named, error

    rows = await _session_rows(db_session, owner.id)
    assert len(rows) == 1
    assert rows[0][4] == 0


# ---------------------------------------------------------------------------
# 8. The conflict list was unbounded
# ---------------------------------------------------------------------------


async def test_the_conflict_list_is_capped_and_says_so_instead_of_growing_without_end(
    client, db_session: AsyncSession
) -> None:
    """528 possible overlaps answer 500 conflicts, ``truncated: true``, and a reason.

    **The fixture.** :data:`MAX_CONFLICTS` and 33 mutually overlapping sessions,
    all 09:00-10:00 on Monday 2026-01-05, written straight into the table.

    **The arithmetic.** 33 rows, every pair overlapping, give 33 x 32 / 2 =
    **528** conflicts. The cap is 500, so the scan must stop 28 short and say so.
    (The audit's figure was worse: 500 such sessions are 500 x 499 / 2 = 124,750
    conflicts, about 63.5 MB of JSON, built in memory before serialisation.)

    **Why the rows are inserted directly.** The write-side rule now refuses an
    overlapping session, which is the point of the previous tests — so a
    conflict list can only be over-full for rows that predate the rule or were
    written outside the service. Producing them through the API would test the
    refusal 33 times over instead of the cap.

    **What is asserted.** Exactly :data:`MAX_CONFLICTS` conflicts come back,
    ``truncated`` is ``true``, ``truncated_reasons`` is non-empty, ``meta.total``
    is the returned count, and ``meta.limit`` is the cap that was actually
    applied.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner

    rows = 33  # 33 * 32 / 2 = 528 overlaps, which is 28 more than the cap of 500.
    start, end = _utc(MONDAY, 9), _utc(MONDAY, 10)
    await db_session.execute(
        insert(WorkSession),
        [
            {
                "id": uuid.uuid4(),
                "owner_id": owner.id,
                "scheduled_start": start,
                "scheduled_end": end,
                "status": "planned",
                "actual_minutes": 0,
                "created_at": start,
                "updated_at": start,
            }
            for _ in range(rows)
        ],
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/planner/conflicts",
        params={"start": MONDAY.isoformat(), "end": MONDAY.isoformat(), "tz": ZONE},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["conflicts"]) == MAX_CONFLICTS == 500
    assert body["truncated"] is True
    assert body["truncated_reasons"]
    assert body["meta"]["total"] == MAX_CONFLICTS
    assert body["meta"]["limit"] == MAX_CONFLICTS


# ---------------------------------------------------------------------------
# 9. accept() did not re-check that the slot was still free
# ---------------------------------------------------------------------------


async def test_accepting_a_stale_suggestion_is_refused_because_the_slot_was_taken(
    db_session: AsyncSession,
) -> None:
    """A suggestion for a slot taken since it was made raises 409 and stores nothing.

    **The fixture.** One task, a hand-built suggestion for Monday 09:00-10:00,
    and a session that already occupies exactly that window — written first, as
    it would be by a colleague's booking or a second tab.

    **Why this was broken.** ``accept`` checked that the task was the caller's
    and then wrote the row. Nothing re-read the calendar, so a suggestion that
    had been accurate when it was proposed and stale by the time it was accepted
    booked straight over whatever took the slot. This is the only test here that
    drives the service rather than HTTP: **there is no accept route**, so the
    method's behaviour is reachable only from here.

    **What is asserted.** ``ConflictError`` is raised, its details name the
    session that holds the slot, and the owner still has exactly the **one**
    session — the accepted proposal added nothing.
    """
    seed = AnalyticsSeed(db_session, await _fresh_user(db_session, "ada"))
    owner = seed.owner

    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=MONDAY + timedelta(days=3),
        estimated_minutes=60,
    )

    scheduling = _scheduling(db_session)
    taken = await scheduling.planner.create_session(
        owner=owner,
        data=_session_create(_utc(MONDAY, 9), _utc(MONDAY, 10)),
    )

    stale = PlannerSuggestion(
        task_id=task.id,
        task_title=task.title,
        suggested_start=_utc(MONDAY, 9),
        suggested_end=_utc(MONDAY, 10),
        reason="60 min on Monday inside your availability.",
    )

    with pytest.raises(ConflictError) as refusal:
        await scheduling.accept(owner=owner, suggestion=stale)
    assert refusal.value.details["conflicting_id"] == str(taken.id)

    stored = await _session_rows(db_session, owner.id)
    assert len(stored) == 1
    assert stored[0][0] == taken.id


async def test_accepting_a_fresh_suggestion_writes_the_session_it_proposed(
    db_session: AsyncSession,
) -> None:
    """The other half of the previous test: an unbooked slot is still accepted.

    **The fixture.** One task and a suggestion for Monday 09:00-10:00, with
    nothing else on the calendar.

    **Why it is here.** A staleness check that refuses everything would pass the
    previous test perfectly. This one pins the other direction — the check is
    about the world having moved, not about ``accept`` being closed — and it
    asserts the row carries the task the suggestion named, so "accepted" still
    means the proposal that was accepted.
    """
    seed = AnalyticsSeed(db_session, await _fresh_user(db_session, "ada"))
    owner = seed.owner

    project = await seed.project()
    task = await seed.task(
        project_id=project.id,
        due_date=MONDAY + timedelta(days=3),
        estimated_minutes=60,
    )

    suggestion = PlannerSuggestion(
        task_id=task.id,
        task_title=task.title,
        suggested_start=_utc(MONDAY, 9),
        suggested_end=_utc(MONDAY, 10),
        reason="60 min on Monday inside your availability.",
    )

    row = await _scheduling(db_session).accept(owner=owner, suggestion=suggestion)
    stored = await _session_rows(db_session, owner.id)
    assert len(stored) == 1
    assert stored[0][0] == row.id
    assert row.task_id == task.id
    assert row.scheduled_start == _utc(MONDAY, 9)


# ---------------------------------------------------------------------------
# Helpers used only by the service-level tests above
# ---------------------------------------------------------------------------


async def _fresh_user(session: AsyncSession, username: str) -> User:
    """Insert an account directly, as ``test_developer_service`` does.

    These two tests drive services rather than routes, and ``register_user``
    writes no activity events — which matters here because the real activity
    sink is wired and a registration row would appear in the feed.
    """
    user = User(
        username=username,
        email=f"{username}@nexus.test",
        hashed_password="not-a-real-hash",  # noqa: S106 — a fixture, never logged in
        role="user",
    )
    session.add(user)
    await session.commit()
    return user


def _session_create(start: datetime, end: datetime) -> WorkSessionCreate:
    """A creation payload, used only where no HTTP body exists."""
    return WorkSessionCreate(scheduled_start=start, scheduled_end=end)
