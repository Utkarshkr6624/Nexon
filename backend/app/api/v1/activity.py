"""The signed-in user's activity feed, and the counters the dashboard opens on.

Two routes, and they answer different questions
-----------------------------------------------
``GET /activity`` is history — a paginated, filterable, newest-first list of what
happened to the caller's work. ``GET /activity/stats`` is not history at all; it
is the aggregate a dashboard's first paint needs before it can render anything.

**Why the aggregate is one endpoint and not two.** The alternative — one route
for project counts and one for task counts — buys a granularity the consumer
cannot use and costs the thing it can. A dashboard renders both tiles in the same
frame from the same set of counters, so splitting them forces two round trips for
one paint, and the two numbers then come from two *different instants*: a task
completed between the requests produces a "3 done" next to a "4 open" that no
single moment ever held. One route, one response, one read of each aggregate. The
cost is honest and small — a client that genuinely wants only the project counts
pays for two small grouped queries it did not ask for — and it is dwarfed by
having the dashboard be wrong by one for a frame. If a caller ever needs one
aggregate *at scale* (a polling dashboard of ten thousand accounts, say), the
split is a two-line change and the shapes are already separate objects inside the
body.

The routes live together because they are the two halves of the same screen and
share a scope: both are the caller's, both are denied by the same two
permissions, and both are the product's answer to "what is mine and what has been
happening to it".

Permissions
-----------
Both routes require ``projects.read`` **and** ``tasks.read``, because the feed
and the aggregate both span projects and tasks. Requiring only one would let a
role read half the surface it is actually being shown; requiring both is the
narrower of the two answers and the honest one. ``analytics.read`` is deliberately
**not** used: that grant belongs to Phase 6 reporting over the same table, and
borrowing it here would make the permission map describe a capability this
endpoint does not implement.

Tenancy, as everywhere else in this layer, is the service's: the caller is
resolved from the bearer token and handed on as ``owner=``, and
:meth:`~app.services.activity_service.ActivityService.feed` puts that id into the
query rather than filtering afterwards.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import ActivityServiceDep, AuthenticatedUser, ProjectServiceDep, TaskServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.activity import ActivityFeed
from app.schemas.project import ProjectStats
from app.schemas.task import TaskStats

router = APIRouter(prefix="/activity", tags=["activity"])

#: The page size a caller gets when it does not ask for one.
DEFAULT_PAGE_SIZE = 20

#: The largest page any caller may ask for. 100 is a ceiling, not a default — see
#: ``app/api/v1/projects.py`` for why an over-large request is refused rather
#: than truncated.
MAX_PAGE_SIZE = 100

_BOTH_READ = [
    Depends(require_permission(Permission.PROJECTS_READ)),
    Depends(require_permission(Permission.TASKS_READ)),
]


class ActivityStats(BaseModel):
    """The opening counters for the dashboard.

    **This is the only response model declared inside a router in this API**, and
    it is here rather than in :mod:`app.schemas.activity` for a reason worth
    stating: it is not a shape any Phase 3 service owns. Each half is a schema
    that already exists — :class:`~app.schemas.project.ProjectStats` and
    :class:`~app.schemas.task.TaskStats` — and this model is only the envelope
    that keeps them in one response. Declaring it next to the route that serves it
    keeps the "one owner per request shape" rule intact: when a dashboard needs a
    third aggregate, this becomes the class that grows, and the migration to
    ``app/schemas/activity.py`` is a cut-and-paste plus one import.

    Neither field is optional and neither has a default. A dashboard that renders
    "—" for a counter it could not fetch is showing the client a hole it will
    paper over; requiring both means a failure is a 500 on this route rather than
    a quietly half-populated screen.
    """

    projects: ProjectStats = Field(description="The caller's projects counted by status.")
    tasks: TaskStats = Field(description="The caller's tasks counted by status, plus overdue.")


@router.get(
    "",
    response_model=ActivityFeed,
    summary="The caller's activity, newest first",
    dependencies=_BOTH_READ,
)
async def list_activity(
    current_user: AuthenticatedUser,
    activity: ActivityServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    project_id: UUID | None = None,
    task_id: UUID | None = None,
    event_type: Annotated[
        str | None,
        Query(description="Restrict to one event; an unknown name is a 422."),
    ] = None,
) -> ActivityFeed:
    """Return everything that happened across the caller's work, newest first.

    **The scope is the caller's own history**, and it is a predicate in the query
    rather than a filter applied afterwards — a row the caller may not see is
    never loaded, so knowing an event id buys nothing.

    **Every filter ANDs onto the same statement**, which is what makes
    ``meta.total`` the size of the set the page was drawn from rather than the
    size of the slice. Newest first is a product decision: a feed is read from the
    top and the response carries no field that could tell a client which end is
    newer.

    **``project_id`` is not re-checked for ownership and does not need to be.**
    Every row returned is already the caller's, so a project that is not theirs
    can only match nothing — an empty page, not an oracle. The one route where
    that is *not* true is ``GET /projects/{id}/activity``, which is a project's own
    history rather than the caller's; there the project is resolved first and a
    foreign id is a 404.

    ``event_type`` is validated against
    :class:`~app.models.enums.ActivityEvent` rather than passed through, so a typo
    is a 422 naming the field instead of a filter that silently matches nothing.

    Errors: 422 for an unknown event name or a ``limit`` outside 1-100.
    """
    return await activity.feed(
        owner=current_user,
        limit=limit,
        offset=offset,
        project_id=project_id,
        task_id=task_id,
        event_type=event_type,
    )


@router.get(
    "/stats",
    response_model=ActivityStats,
    summary="Dashboard counters for the caller",
    dependencies=_BOTH_READ,
)
async def get_activity_stats(
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
    tasks: TaskServiceDep,
) -> ActivityStats:
    """Return the caller's project and task counters in one response.

    **Every bucket is present even when it is empty**, so a dashboard does not
    lose a column when the first project in it is deleted. The status buckets are
    partitions of the caller's rows, so on a complete vocabulary they sum to
    ``total``; they are not asserted to do so, because a new status would make a
    strict check reject correct counts rather than catch a wrong one.

    ``tasks.overdue`` is a different question from the breakdown and is counted
    across the three open statuses rather than folded into one, so the buckets
    stay a partition. A task due *today* is not overdue — "overdue" means the due
    date is behind today, which is the same date-based rule
    :class:`~app.schemas.task.TaskRead` applies client-side, so a badge the server
    builds and one the client builds from the same row cannot disagree.

    The two aggregates are read in sequence rather than concurrently. They touch
    different tables and share the request-scoped session, so concurrency would
    not run them in parallel anyway, and the honest framing is that this is two
    grouped reads forming one response — see this module's docstring for why they
    are not two routes.

    Errors: none beyond the permission gate — an account with no work at all gets
    a fully-populated body of zeroes.
    """
    return ActivityStats(
        projects=await projects.stats(owner=current_user),
        tasks=await tasks.stats(owner=current_user),
    )


__all__ = ["ActivityStats", "router"]
