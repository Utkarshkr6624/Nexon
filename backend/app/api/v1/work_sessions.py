"""Work-session endpoints: blocks of time reserved for work, and the clock.

Where the rules live
--------------------
This file is a translation layer and nothing else, exactly as
``app/api/v1/projects.py`` is. *May this timer start twice, is that task yours,
does stopping accumulate minutes in SQL or in Python* — all of it belongs to
:class:`~app.services.planner_service.PlannerService`, which is where the
repository's accumulate-in-SQL rule lives. This router picks a status code and
hands the result back.

Why starting and stopping are their own routes
----------------------------------------------
``POST /work-sessions/{id}/start`` and ``/stop`` are not sugar over PATCH. The
elapsed minutes have to come from the **database** clock, not from this process:
a timer that read its own clock and computed the difference loses or invents
time the moment the host and the server disagree, and two tabs starting the same
session would each write their own answer. Routing them makes the service the
only writer of ``actual_start``/``actual_end``/``actual_minutes``, so a PATCH can
never forge a timing no clock produced.

Tenancy
-------
**No route accepts a user id from the request**, and every ``{session_id}``
resolves through ``get_by_id_for_user`` — another account's session answers
**404, never 403**.

Timezones
---------
No ``tz`` parameter here either: ``scheduled_start``/``actual_start`` are
instants and are returned with their offsets intact. See ``calendar.py`` for the
full statement of that rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, PlannerServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.common import Page
from app.schemas.planner import (
    WorkSessionCreate,
    WorkSessionRead,
    WorkSessionStatus,
    WorkSessionUpdate,
)

router = APIRouter(prefix="/work-sessions", tags=["work-sessions"])

#: A time-tracking window is a handful of rows. Same rationale as the calendar
#: default: a caller that wants more pages.
DEFAULT_PAGE_SIZE = 50

#: Matches every other listing in the API — see ``docs/api-conventions.md``.
MAX_PAGE_SIZE = 100

#: See ``calendar.py``: the authoritative sort set is
#: ``planner_service._SESSION_SORT_KEYS``; the service resolves the name against
#: it and raises ``ValidationError`` before any SQL is built.
_SORT_DESCRIPTION = "Sort key; one of the names the service allowlists."


@router.get(
    "",
    response_model=Page[WorkSessionRead],
    summary="List the caller's work sessions",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def list_sessions(
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    starts_from: Annotated[
        datetime | None,
        Query(alias="from", description="Window start, timezone-aware. Overlap, not containment."),
    ] = None,
    starts_to: Annotated[
        datetime | None,
        Query(alias="to", description="Window end, timezone-aware. Half-open: [from, to)."),
    ] = None,
    task_id: UUID | None = None,
    project_id: UUID | None = None,
    status_filter: Annotated[
        WorkSessionStatus | None,
        Query(alias="status", description="planned/active/completed/cancelled."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "scheduled_start",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "asc",
) -> Page[WorkSessionRead]:
    """List work sessions as one page of a filtered, sorted, owner-scoped sequence.

    **The window is an overlap, not a containment.** A block begun at 23:00 and
    running past midnight belongs to the day it ran into, so the filter is
    ``scheduled_start < to AND scheduled_end > from`` and it is half-open at both
    ends.

    Gated on ``calendar.read`` rather than ``tasks.read``: a session is a block
    of time on the calendar, and reading it is reading the schedule.

    Unknown ``sort``, ``order`` or ``status`` is a 422, raised by the service
    against its allowlist before any SQL is built.

    Errors: 422 for an unknown filter value, an inverted window, or a ``limit``
    outside 1-100.
    """
    return await planner.list_sessions(
        owner=current_user,
        limit=limit,
        offset=offset,
        starts_from=starts_from,
        starts_to=starts_to,
        task_id=task_id,
        project_id=project_id,
        status=status_filter,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=WorkSessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve a work session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def create_session(
    payload: WorkSessionCreate,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> WorkSessionRead:
    """Block out a stretch of time for a task or a project.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token.

    A new session is ``planned`` with ``actual_minutes = 0``. Reserving time is
    not doing it: the figure only becomes real when the timer is started and
    stopped, and a session that arrived already "spent" would make the tracked
    total a function of how carefully the user filled in a form.

    ``task_id`` and ``project_id`` are resolved through the **scoped** lookups
    before the row is written, so another account's id leaves no row behind at
    all and answers 404 rather than 403.

    Errors: 404 for a task or project that is not the caller's; 422 for a window
    that ends before it starts, a naive datetime, or a negative estimate.
    """
    return await planner.create_session(owner=current_user, data=payload)


@router.get(
    "/{session_id}",
    response_model=WorkSessionRead,
    summary="Fetch one work session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_session(
    session_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> WorkSessionRead:
    """Return one of the caller's sessions.

    **404 for another account's session, never 403** — the id is resolved
    through a lookup scoped by ``owner_id``.

    Errors: 404 when the caller owns no session with this id.
    """
    return await planner.get_session(session_id=session_id, owner=current_user)


@router.patch(
    "/{session_id}",
    response_model=WorkSessionRead,
    summary="Edit a work session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def update_session(
    session_id: UUID,
    payload: WorkSessionUpdate,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> WorkSessionRead:
    """Apply a partial update to a session the caller owns.

    **A field is written if the client named it** — cancelling a block is
    ``"status": "cancelled"``, not an absent key.

    The window is re-checked against the **persisted** row: a PATCH that moves
    only ``scheduled_start`` past a stored ``scheduled_end`` carries nothing for
    the schema to compare against, and would otherwise persist an impossible
    block no single request expressed.

    Cancelling is an ordinary edit rather than a routed transition, because a
    session has no lifecycle rules a blanket write could bypass — starting and
    stopping are routed because the clock is involved, not the state machine.

    Errors: 404 for a session that is not the caller's; 422 for an inverted
    window.
    """
    session_row = await planner.get_session(session_id=session_id, owner=current_user)
    return await planner.update_session(session=session_row, data=payload, owner=current_user)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a work session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def delete_session(
    session_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> Response:
    """Delete one of the caller's sessions.

    **A hard delete, and it discards tracked minutes.** Cancelling via PATCH is
    the answer that keeps the history; this is the path for a row that should
    not exist, and it is a separate verb precisely so the choice is explicit
    rather than inferred from an absent key.

    Errors: 404 for a session that is not the caller's, or that does not exist.
    """
    session_row = await planner.get_session(session_id=session_id, owner=current_user)
    await planner.delete_session(session=session_row, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{session_id}/start",
    response_model=WorkSessionRead,
    summary="Start the timer on a session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def start_session(
    session_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> WorkSessionRead:
    """Start the clock on a planned session.

    **The timestamp is the database's, not this process's.** ``actual_start`` is
    read with ``func.now()`` so two hosts with skewed clocks cannot write two
    different starts for the same session, and so the answer does not change
    because the worker that served the request was restarted.

    Starting an already-``active`` session is a no-op rather than an error, so a
    double-clicked timer button does not discard the first start.

    Errors: 422 for a session that is ``completed`` or ``cancelled`` — neither
    can be resumed, and pretending otherwise would produce a session that claims
    time it did not spend; 404 for a session that is not the caller's.
    """
    session_row = await planner.get_session(session_id=session_id, owner=current_user)
    return await planner.start_session(session=session_row, owner=current_user)


@router.post(
    "/{session_id}/stop",
    response_model=WorkSessionRead,
    summary="Stop the timer on a session",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def stop_session(
    session_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> WorkSessionRead:
    """Stop the clock and record what was actually spent.

    **``actual_minutes`` is accumulated in SQL, not in Python.** The service
    writes ``actual_minutes = actual_minutes + <elapsed>`` in one statement so
    the row lock does the serialising; an application-side read-modify-write
    loses time the moment two tabs stop the same session at once, because last
    writer wins. See the note on ``Task.actual_minutes`` in
    :mod:`app.models.task` for why the increment is a single statement and why
    the rounding happens once here rather than per session.

    The elapsed figure is a difference of two **database** timestamps, so it is
    never negative and never depends on the clock of whichever worker happened
    to serve the request.

    Stopping a session that was never started is refused rather than recorded as
    a zero-length session: a stop without a start has no duration to report, and
    inventing one would put a row on the calendar that no clock ever measured.
    Stopping an already-stopped session is a **no-op**, so a retried tap does
    not discard the minutes the first one recorded.

    Errors: 422 for a session that is not ``active`` and not already
    ``completed``; 404 for a session that is not the caller's.
    """
    session_row = await planner.get_session(session_id=session_id, owner=current_user)
    return await planner.stop_session(session=session_row, owner=current_user)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
