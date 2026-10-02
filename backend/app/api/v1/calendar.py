"""Calendar event endpoints: the events a user's day is built from.

Where the rules live
--------------------
This file is a translation layer and nothing else, exactly as
``app/api/v1/projects.py`` is. Every question it could answer — *may this
window be stored, is that project yours, is this PATCH an impossible schedule*
— is answered by :class:`~app.services.planner_service.PlannerService`, and this
router picks a status code and hands the result back. It imports no repository
and raises no domain error:
:func:`app.core.exceptions.install_exception_handlers` turns the service's
``NotFoundError`` / ``ValidationError`` / ``ConflictError`` into the shared
envelope.

Tenancy
-------
**No route here accepts a user id from the request.** The caller comes from the
bearer token, is passed to the service as ``owner=``, and every ``{event_id}``
is resolved through ``get_by_id_for_user`` before it is used. Another account's
event therefore answers **404 and not 403** — identical to an id that never
existed — so this surface cannot be used to find out which event ids are real.

Timezones
---------
There is deliberately **no ``tz`` parameter on this router.** An event's
``starts_at``/``ends_at`` are instants, and they come back as the instants that
were stored, offset intact. Converting them into the reader's zone is a client
decision made with the zone the *user* is in; a server that re-expressed them
would make the stored value unobservable and would silently shift an event
across a day boundary for half the planet. The planner routes are the ones that
are asked *in a zone*, because a day is a local notion; see
``app/api/v1/planner.py``.

Pagination
----------
Every listing is a :class:`~app.schemas.common.Page` and ``limit`` is capped at
:data:`MAX_PAGE_SIZE` — a **422, not a silent clamp**, for the reason given in
``app/api/v1/projects.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, PlannerServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.models.enums import CalendarEventType
from app.schemas.common import Page
from app.schemas.planner import CalendarEventCreate, CalendarEventRead, CalendarEventUpdate

router = APIRouter(prefix="/calendar", tags=["calendar"])

#: The page size a caller gets when it does not ask for one. A day view is a
#: handful of rows; anything longer is the client paging through its own weeks.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for. Matches every other listing in the
#: API — see ``docs/api-conventions.md`` §Pagination.
MAX_PAGE_SIZE = 100

#: The sort names this endpoint advertises.
#:
#: The set is *not* duplicated here. ``ORDER BY`` takes an expression rather than
#: a bound parameter, so an unvalidated name would be SQL injection behind a
#: query parameter — but ``PlannerService`` resolves it against an allowlist and
#: raises ``ValidationError`` before a statement is built. The authoritative set
#: is ``planner_service._EVENT_SORT_KEYS``; anything else is a 422 that never
#: reaches the database.
_SORT_DESCRIPTION = "Sort key; one of the names the service allowlists."


@router.get(
    "",
    response_model=Page[CalendarEventRead],
    summary="List the caller's calendar events",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def list_events(
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
    project_id: UUID | None = None,
    task_id: UUID | None = None,
    event_type: Annotated[
        CalendarEventType | None, Query(description="Restrict to one kind of event.")
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "starts_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "asc",
) -> Page[CalendarEventRead]:
    """List events as one page of a filtered, sorted, owner-scoped sequence.

    **The window is an overlap, not a containment.** An event that began
    yesterday and runs into this afternoon is on this afternoon's calendar, so
    the filter is ``starts_at < to AND ends_at > from`` rather than a range on
    ``starts_at``. Half-open at both ends: an event ending exactly at ``to`` is
    the next window's, not this one's.

    **With no window named, a bounded default around now is used.** The
    repository deliberately takes no unbounded range, so "no filter" has to mean
    *some* window rather than *every row* — a client that wants everything walks
    ``offset``.

    Every filter ANDs, so ``meta.total`` counts the rows the page was drawn
    from. ``project_id``/``task_id`` are not re-checked for ownership here and
    do not need to be: every row returned is already the caller's, so another
    account's project can only ever match nothing.

    **Unknown ``sort``, ``order`` or ``event_type`` is a 422**, raised by the
    service against its allowlist before any SQL is built.

    Errors: 422 for an unknown filter value, an inverted window, or a ``limit``
    outside 1-100.
    """
    return await planner.list_events(
        owner=current_user,
        limit=limit,
        offset=offset,
        starts_from=starts_from,
        starts_to=starts_to,
        project_id=project_id,
        task_id=task_id,
        event_type=event_type,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=CalendarEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a calendar event",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def create_event(
    payload: CalendarEventCreate,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> CalendarEventRead:
    """Book an event on the caller's own calendar.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token; a create endpoint that let the body choose its owner would
    hand any authenticated user the ability to write into another account's
    calendar.

    ``project_id`` and ``task_id`` are resolved through the **scoped** lookups
    before the row is written, so an id belonging to somebody else leaves no row
    behind at all rather than a half-applied one, and the refusal is a 404 the
    caller cannot distinguish from "no such id".

    ``event_type`` defaults to ``other`` and is validated against
    :class:`~app.models.enums.CalendarEventType`: a typo would otherwise be an
    event no filter can ever match again.

    Errors: 404 for a project or task that is not the caller's; 422 for a window
    that ends before it starts, a naive datetime, or an unknown event type.
    """
    return await planner.create_event(owner=current_user, data=payload)


@router.get(
    "/{event_id}",
    response_model=CalendarEventRead,
    summary="Fetch one calendar event",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_event(
    event_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> CalendarEventRead:
    """Return one of the caller's events.

    **404 for another account's event, never 403** — the id is resolved through
    a lookup scoped by ``owner_id``, so the row is never loaded and the refusal
    is the one a nonexistent id gets. See this module's docstring for why that
    distinction is load-bearing.

    Errors: 404 when the caller owns no event with this id.
    """
    return await planner.get_event(event_id=event_id, owner=current_user)


@router.patch(
    "/{event_id}",
    response_model=CalendarEventRead,
    summary="Edit a calendar event",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def update_event(
    event_id: UUID,
    payload: CalendarEventUpdate,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> CalendarEventRead:
    """Apply a partial update to an event the caller owns.

    **A field is written if the client named it** — clearing a location or a
    description is ``"location": null``, not an absent key.

    The window is re-checked against the **persisted** row, not only against the
    payload. A PATCH that moves only ``starts_at`` past a stored ``ends_at``
    carries nothing for the schema to compare against, and would otherwise
    persist an impossible schedule that no single request ever expressed.

    ``extra="forbid"`` means an unknown field is a 422 naming it, rather than a
    silent drop that reads as a successful write.

    Errors: 404 for an event that is not the caller's; 422 for a window that
    ends before it starts.
    """
    event = await planner.get_event(event_id=event_id, owner=current_user)
    return await planner.update_event(event=event, data=payload, owner=current_user)


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a calendar event",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def delete_event(
    event_id: UUID,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
) -> Response:
    """Delete one of the caller's events.

    **A hard delete.** ``completed_at`` marks an event that was *attended*;
    an event the user no longer wants in their calendar is a different thing,
    and ``/api/v1/calendar/events/{id}`` is where that is asked for.

    Work sessions filed against the event cascade with it — a session pointing
    at a row that no longer exists is not a session. Activity rows do **not**:
    they are set to ``NULL`` by their own ``ON DELETE SET NULL``, so the record
    of the event outlives it.

    Errors: 404 for an event that is not the caller's, or that does not exist.
    """
    event = await planner.get_event(event_id=event_id, owner=current_user)
    await planner.delete_event(event=event, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
