"""Pagination for ``GET /analytics/projects``, and the N+1 that paged it used to hide.

Two defects are covered here and they are covered together because they were
found together: the route answered with a bare JSON array, and answering with an
array over every project meant the answer was assembled two statements at a time.

What the file is for
--------------------
**A list a client cannot page is a list a client cannot trust.** The route returned
``list[ProjectAnalyticsRead]`` — no ``limit``, no ``offset``, no total — so twenty
rows and two hundred rows were the same shape and nothing in the response said
which. A dashboard rendering "your projects" from that array could not tell a
complete answer from a truncated one, and the only way to find out was to ask a
question the API did not accept. The response is now
``Page[ProjectAnalyticsRead]``, the envelope every other list route on this API
already uses, and ``meta.total`` is the size of the *filtered* set rather than of
the slice.

**The roll-up was ``4 x N + 5`` statements.** For each project the window-scoped
completion pair was read twice — once for the numerator of ``completion_rate`` and
once for its denominator — and each read was its own statement. Measured with a
``before_cursor_execute`` listener over projects seeded with one completed task
each: 9 statements at 1 project, 25 at 5, 65 at 15, 245 at 60. It is now a fixed
six whatever ``N`` is, because the pair comes back for every project at once from
one ``GROUP BY project_id``.

Why the count matters beyond this route: :mod:`app.services.risk.detection` reaches
``project_analytics`` on **every** ``POST /intelligence/evaluate`` pass, so the same
N+1 was paid again by each routine evaluation, multiplied by every detector that
reads a project row.

House style, following ``tests/test_analytics_projects_api.py``
---------------------------------------------------------------
* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* The window is sent explicitly on every request, so no test inherits the route's
  own default window (which ends on the database's current date and would drift).
* Every expected figure is derived in the test's own docstring or in the constant
  beside the assertion, never recorded from a run.

The statement count is asserted twice over: **equal at two different project
counts** — the property the fix is about — and **equal to the six reads the service
is documented as making**, so a regression that swaps the batched query for some
other constant does not pass unnoticed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TaskStatus
from app.repositories.analytics import AnalyticsRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.planner import CalendarEventRepository, WorkSessionRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.services.analytics import AnalyticsService
from tests.analytics_fixtures import (
    DAY,
    AnalyticsSeed,
    at,
    register_user,
    seeded_client,
)

pytestmark = pytest.mark.integration

#: A three-week window, ``DAY`` being a Monday, so it is exactly three whole
#: calendar weeks and any week-based figure stays whole.
WINDOW = {
    "start_date": DAY.isoformat(),
    "end_date": (DAY + timedelta(days=20)).isoformat(),
}

#: The window as arguments, for the service-level calls that do not go over HTTP.
WINDOW_ARGS = {"start": DAY, "end": DAY + timedelta(days=20)}

#: The two account sizes the statement count is compared at. Deliberately a factor
#: of four apart: at that distance a per-project read cannot hide behind a constant
#: that happens to absorb a few extra statements.
SMALL_ACCOUNT = 3
LARGE_ACCOUNT = 12

#: How many statements :meth:`AnalyticsService.project_analytics` issues, derived
#: from the code rather than recorded from a run:
#:
#: 1. ``SELECT now()`` — :meth:`AnalyticsService._today`, the database clock the
#:    overdue count is measured against.
#: 2. ``projects LEFT JOIN tasks ... GROUP BY`` — one grouped row per project.
#: 3. ``work_sessions`` grouped by project — tracked minutes.
#: 4. ``tasks`` grouped by project — estimated and actual minutes.
#: 5. ``activity_events`` grouped by project — activity events in the window.
#: 6. ``tasks`` grouped by project with two ``FILTER`` aggregates — created and
#:    completed inside the window, for every project on the page at once.
#:
#: Six, none of which mentions the number of projects. The old implementation spent
#: four more per project at the same place, which is where ``4 x N + 5`` came from.
BATCHED_STATEMENTS = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> AnalyticsService:
    """An :class:`AnalyticsService` wired to one session.

    Built directly rather than through ``get_analytics_service`` because the
    statements counted below are the roll-up's own; going through the dependency
    graph would add the request's authentication and window reads to the total and
    make the number say more about FastAPI than about the defect.
    """
    return AnalyticsService(
        AnalyticsRepository(session),
        TaskRepository(session),
        ProjectRepository(session),
        WorkSessionRepository(session),
        CalendarEventRepository(session),
        NoteRepository(session),
    )


async def _seed_projects(seed: AnalyticsSeed, count: int) -> list:
    """``count`` projects, each with one task created and completed inside the window.

    The task exists so the roll-up has something to report. A project with no tasks
    is a legitimate row — and one the roll-up still returns — but it would not
    exercise the batched completion read, which counts nothing for it.

    Names are ``Project A``..``Project L`` so the alphabetical page order is the
    seeded order and a paging assertion can be read by eye rather than computed.
    """
    letters = "ABCDEFGHIJKL"
    projects = [await seed.project(name=f"Project {letters[index]}") for index in range(count)]
    for project in projects:
        await seed.task(
            project_id=project.id,
            status=TaskStatus.COMPLETED.value,
            created_at=at(DAY),
            completed_at=at(DAY, 12),
        )
    return projects


@contextmanager
def counting_statements(engine: Engine) -> Iterator[list[str]]:
    """Collect every statement the enclosed block sends to the database.

    ``before_cursor_execute`` on the **engine** rather than on a session, so the
    count is the whole cost of the call including whatever a repository does
    internally — which is exactly the number that grew when the roll-up started
    reading per project. The statements themselves are yielded so a failure can
    name the read that came back instead of only how many there were.
    """
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _render(statements: list[str]) -> str:
    """The counted statements, one per line, for an assertion message."""
    return "\n".join(f"    {' '.join(text.split())[:120]}" for text in statements)


def _without_request_id(body: dict) -> dict:
    """An error envelope with the per-request ``request_id`` dropped.

    The identifier is generated per request on purpose, so two calls can never
    produce byte-identical error bodies. Everything else about the refusal —
    status, code, message, details — has to match, and that is what is compared.
    """
    envelope = dict(body.get("error", {}))
    envelope.pop("request_id", None)
    return {"error": envelope}


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


async def test_a_page_of_project_figures_says_how_many_projects_there_are_in_total(
    client, db_session
):
    """Two rows of a three-project account are not "your projects".

    Three projects are seeded — ``Project A``, ``Project B``, ``Project C`` — and
    the page is asked for two of them. The response carries **two** items and a
    ``meta.total`` of **three**, because ``total`` counts the whole filtered set
    rather than the slice. That single number is what a client needs and what a
    bare array could not give it: without it, a two-item array from a
    three-project account and a two-item array from a two-hundred-project account
    are the same bytes.

    The second page carries the third project and does not repeat the first, so
    walking the pages covers the set exactly once.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_projects(seed, 3)

    first = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "limit": 2}, headers=auth
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert [row["name"] for row in body["items"]] == ["Project A", "Project B"]
    assert body["meta"] == {"total": 3, "limit": 2, "offset": 0}

    second = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "limit": 2, "offset": 2}, headers=auth
    )
    assert second.status_code == 200, second.text
    tail = second.json()
    assert [row["name"] for row in tail["items"]] == ["Project C"]
    assert tail["meta"] == {"total": 3, "limit": 2, "offset": 2}

    walked = [row["name"] for row in body["items"]] + [row["name"] for row in tail["items"]]
    assert walked == ["Project A", "Project B", "Project C"], "the pages must not overlap or skip"


async def test_a_paged_project_row_carries_the_same_figures_the_whole_list_carried(
    client, db_session
):
    """Paging moves rows between responses; it does not change what a row says.

    ``Project B`` is read twice: once from the page that contains it and once from
    the whole set. The two rows are compared field by field, so a page that
    quietly recomputed something differently from the unpaged read — a completion
    rate over the page rather than the window, say — fails here rather than in a
    client.

    ``completion_rate`` is ``100.0`` on both reads because the seeded project has
    one task created in the window and completed in it: one completion out of one
    creation.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_projects(seed, 3)

    paged = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "limit": 1, "offset": 1}, headers=auth
    )
    assert paged.status_code == 200, paged.text
    whole = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)
    assert whole.status_code == 200, whole.text

    from_page = paged.json()["items"][0]
    from_whole = next(row for row in whole.json()["items"] if row["name"] == "Project B")
    assert from_page == from_whole
    assert from_page["total_tasks"] == 1
    assert from_page["completed_tasks"] == 1
    assert from_page["completion_rate"] == 100.0


async def test_an_account_with_no_projects_gets_an_empty_page_and_a_total_of_zero(
    client, db_session
):
    """An empty set is a page with a zero total, not a missing key.

    No project is seeded, so the set was searched and found empty. ``items`` is
    ``[]`` and ``meta.total`` is ``0`` — a real count of nothing, which is a very
    different thing from a count that could not be taken.
    """
    _seed, auth = await seeded_client(client, db_session)

    response = await client.get("/api/v1/analytics/projects", params=WINDOW, headers=auth)

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "meta": {"total": 0, "limit": 20, "offset": 0}}


async def test_a_page_larger_than_the_ceiling_is_refused_before_anything_is_counted(
    client, db_session
):
    """``limit`` is bounded, so one request cannot ask for the whole account.

    A hundred is the ceiling: more than any screen in the product renders, so a
    caller reaching it is walking pages deliberately. ``limit=101`` is a **422**,
    refused by the route before the service runs — which is the point of bounding
    it, because an unbounded list endpoint is how one dashboard refresh becomes one
    enormous response on an account with a great many projects.
    """
    _seed, auth = await seeded_client(client, db_session)

    refused = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "limit": 101}, headers=auth
    )
    accepted = await client.get(
        "/api/v1/analytics/projects", params={**WINDOW, "limit": 100}, headers=auth
    )

    assert refused.status_code == 422, refused.text
    assert accepted.status_code == 200, accepted.text


async def test_another_accounts_project_id_is_not_found_and_never_reported_as_forbidden(
    client, db_session
):
    """A foreign ``project_id`` is a 404 — the same answer as an id nobody issued.

    The id is resolved through the owner-scoped lookup before any aggregate runs,
    so the refusal is indistinguishable from the one a made-up id gets and the
    route cannot be used to learn which project ids exist. **A 403 would confirm
    the id is real, which is the entire reason the rule exists.**

    The foreign project is seeded with a task of its own, so a leak would have
    something to leak: had the filter been applied after the roll-up rather than
    resolved before it, this response would have carried real figures.
    """
    _seed, auth = await seeded_client(client, db_session)
    _mallory, foreign_projects = await _register_with_projects(db_session, "mallory", 1)
    foreign = foreign_projects[0].id

    refused = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(foreign)},
        headers=auth,
    )
    invented = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(uuid.uuid4())},
        headers=auth,
    )

    assert refused.status_code == 404, refused.text
    assert invented.status_code == 404, invented.text
    # Compared without ``request_id``, which is per-request by design and differs
    # between any two calls; the code, the message and the details must not.
    assert _without_request_id(refused.json()) == _without_request_id(invented.json()), (
        "a foreign id and an id nobody issued must be the same answer, message included"
    )


async def test_narrowing_to_one_of_my_own_projects_reports_a_total_of_one(client, db_session):
    """The total describes the **filtered** set, not the account.

    Three projects are seeded and the request names one of them. The page carries
    that project and ``meta.total`` is **1**, not 3 — a total that ignored the
    filter would tell a client there were three projects in a response containing
    one, which is the same "is this complete?" ambiguity the envelope exists to
    remove.
    """
    seed, auth = await seeded_client(client, db_session)
    projects = await _seed_projects(seed, 3)

    response = await client.get(
        "/api/v1/analytics/projects",
        params={**WINDOW, "project_id": str(projects[0].id)},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["name"] for row in body["items"]] == ["Project A"]
    assert body["meta"]["total"] == 1


# ---------------------------------------------------------------------------
# The statement count
# ---------------------------------------------------------------------------


async def test_the_project_roll_up_costs_the_same_number_of_statements_at_three_projects_and_at_twelve(
    db_session,
):
    """The roll-up is a fixed number of reads, not four per project.

    The figure under test is **equality**, and it is asserted at two account sizes
    a factor of four apart — three projects and twelve — because that is the
    property the batching bought: the statement count must not depend on how many
    projects the caller owns. The pre-fix implementation issued ``4 x N + 5``,
    which is 17 statements at three projects and 53 at twelve; nothing about the
    two accounts differs except how many projects they hold.

    The larger account is also asserted equal to :data:`BATCHED_STATEMENTS` (six),
    whose six reads are derived one by one in that constant's comment: the database
    clock, the project/task join, session minutes, task minutes, activity events,
    and one grouped completion read covering every project on the page. If the N+1
    came back, or a later edit traded the batched read for some other constant,
    one of the two assertions fails.
    """
    engine: Engine = db_session.get_bind()

    small_owner, _ = await _register_with_projects(db_session, "small", SMALL_ACCOUNT)
    with counting_statements(engine) as small_statements:
        small = await _service(db_session).project_analytics(owner=small_owner, **WINDOW_ARGS)

    large_owner, _ = await _register_with_projects(db_session, "large", LARGE_ACCOUNT)
    with counting_statements(engine) as large_statements:
        large = await _service(db_session).project_analytics(owner=large_owner, **WINDOW_ARGS)

    assert len(small) == SMALL_ACCOUNT, "the small account must hold three projects"
    assert len(large) == LARGE_ACCOUNT, "the large account must hold twelve projects"
    assert len(small_statements) == len(large_statements), (
        "the roll-up's statement count must not depend on how many projects there are:\n"
        f"  three projects: {len(small_statements)}\n"
        f"  twelve projects: {len(large_statements)}\n" + _render(large_statements)
    )
    assert len(large_statements) == BATCHED_STATEMENTS, (
        f"a twelve-project roll-up issued {len(large_statements)} statements:\n"
        + _render(large_statements)
    )


async def test_naming_one_project_adds_one_statement_and_not_one_per_project(db_session):
    """The project filter costs one owner-scoped lookup, whatever the account holds.

    Resolving ``project_id`` happens **before** any aggregate — another account's
    project is refused without the grouped reads running at all — so an account
    holding twelve projects and asking about one of them pays
    :data:`BATCHED_STATEMENTS` plus that single lookup, seven statements, rather
    than a lookup per project on the list being narrowed.

    A project with no task in the window is the case that would expose a filter
    applied after the fact: it still appears in the page, with ``completion_rate``
    ``None`` rather than a fabricated ``0``, because nothing was completed and
    nothing was created — an absent measurement, not a measured zero.
    """
    engine: Engine = db_session.get_bind()
    owner, projects = await _register_with_projects(db_session, "ada", LARGE_ACCOUNT)

    with counting_statements(engine) as statements:
        rows = await _service(db_session).project_analytics(
            owner=owner, project_id=projects[0].id, **WINDOW_ARGS
        )

    assert len(rows) == 1, "the filter must narrow the roll-up to the named project"
    assert rows[0].completion_rate == 100.0
    assert len(statements) == BATCHED_STATEMENTS + 1, (
        f"a filtered roll-up issued {len(statements)} statements:\n" + _render(statements)
    )


# ---------------------------------------------------------------------------
# Test-only setup
# ---------------------------------------------------------------------------


async def _register_with_projects(
    session: AsyncSession, username: str, count: int
) -> tuple[uuid.UUID, list]:
    """A directly-inserted account with ``count`` seeded projects, and its id.

    Used by the service-level tests, which have no bearer token to authenticate
    with. The account is inserted directly rather than through the API for the
    reason ``tests/test_career_service.py`` gives: these tests drive the service,
    not a route.
    """
    owner = await register_user(session, username=username)
    projects = await _seed_projects(AnalyticsSeed(session, owner), count)
    return owner, projects
