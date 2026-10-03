"""Project endpoints: the working set, its lifecycle, and what hangs off it.

Where the rules live
--------------------
This file is a translation layer and nothing else. Every question it could
answer — *may this project move to that status, is this date window possible, is
this sub-task list mine* — is answered by
:class:`~app.services.project_service.ProjectService`,
:class:`~app.services.task_service.TaskService` or
:class:`~app.services.activity_service.ActivityService`, and this router picks a
status code and hands the result back. It imports no repository, and it raises no
domain error: :func:`app.core.exceptions.install_exception_handlers` turns the
services' ``NotFoundError`` / ``ConflictError`` / ``ValidationError`` into the
shared envelope, and a router that built its own error body would be a second,
drifting implementation of the same contract.

Tenancy, in particular, is the service's. **No route here accepts a user id from
the request.** The caller comes from the bearer token, is passed to the service
as ``owner=``, and every route taking a ``{project_id}`` resolves that id through
a lookup scoped by ``owner_id`` before it is used. That is what makes another
account's id answer **404 and not 403** — identical to an id that never existed
— so the route cannot be used to find out which project ids are real. A ``403``
here would mean "this id exists and is not yours", which is the one answer this
surface must never give.

The lifecycle has its own endpoints rather than a status field
------------------------------------------------------------
``status``, ``completed_at`` and ``archived_at`` are not on
:class:`~app.schemas.project.ProjectUpdate`, and ``extra="forbid"`` means
posting one is a 422 naming the field rather than a silent drop. That is not
fussiness: reaching ``completed`` stamps a timestamp and writes an event, and a
blanket PATCH able to set the column would let a client create a project that
claims to be finished and carries no evidence of finishing. ``/activate``,
``/complete``, ``/hold``, ``/resume``, ``/archive`` and ``/restore`` are the only
doors, and they are also the only places the legal transitions are enforced — a
``planned`` project goes through ``active``, and ``archived`` is terminal
except through ``/restore``.

**Every edge of the legality table has a route here.** That is not a detail: an
edge no endpoint can walk is a description rather than a rule. ``ON_HOLD`` was
listed in the service since the first commit and was reached by nothing, so
``ProjectStats.on_hold`` read a hard zero for every account — a number
indistinguishable from a user who never pauses a project. And ``COMPLETED`` is
only legal *from* ``ACTIVE``, so without ``/activate`` the completion endpoint
was unreachable for every project that had not been archived and restored first.
A state nothing can enter is not a feature; it is a metric that always reads
zero.

Pagination
----------
Every listing is a :class:`~app.schemas.common.Page`, never a bare array, and
``limit`` is capped at :data:`MAX_PAGE_SIZE`. The cap is a **rejection, not a
silent truncation**: ``?limit=500`` is a 422 rather than a 100-row page, because
a client that asked for 500 and received 100 cannot tell a truncated page from a
page that was always 100 rows long, and would paginate off the end of a sequence
it believes it has seen.

One filter this surface deliberately does not advertise
-------------------------------------------------------
**``GET /projects`` has no ``priority`` filter, and neither does the service.**
:meth:`~app.services.project_service.ProjectService.list` accepts ``status``,
``search``, ``sort`` and ``order`` only, and
:meth:`~app.repositories.project.ProjectRepository.list_for_user` builds no
predicate on ``projects.priority`` to hand it. A router that accepted the
parameter and dropped it would answer a filtered request with an unfiltered list
under the name of a filtered one — the precise quiet disagreement that
``ProjectUpdate.extra = "forbid"`` exists to prevent, and a worse one, because
nothing in the response would indicate it had happened. Priority is meaningful on
this model (it is the same four-grade scale as a task's) and the filter is worth
having; adding it is one keyword in the repository and one in the service, and it
belongs to whoever owns those files rather than to this one.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    ActivityServiceDep,
    AuthenticatedUser,
    ProjectServiceDep,
    TaskServiceDep,
)
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.models.enums import ProjectStatus, TaskStatus
from app.models.project import Project
from app.schemas.activity import ActivityFeed
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectRead, ProjectSummary, ProjectUpdate
from app.schemas.task import TaskRead

router = APIRouter(prefix="/projects", tags=["projects"])

#: The page size a caller gets when it does not ask for one. Smaller than
#: :data:`~app.services.project_service.ProjectService.DEFAULT_PAGE_SIZE` because
#: a project list is a picker the user scans, not a feed they read end to end,
#: and the client pages through the rest.
DEFAULT_PAGE_SIZE = 20

#: The largest page any caller may ask for. 100 is a ceiling, not a default: it
#: is more than any screen in the product renders, so a caller reaching it is a
#: script exporting data, and such a caller is served by walking ``offset``.
MAX_PAGE_SIZE = 100

#: The sort names this endpoint advertises.
#:
#: The list is deliberately *not* enforced here even though it looks like a
#: validation concern. ``ORDER BY`` takes an expression rather than a bound
#: parameter, so an unvalidated sort name would be SQL injection behind a query
#: parameter — but the service already resolves the name against an allowlist
#: and raises ``ValidationError`` before a statement is built, and the repository
#: resolves it a second time before it is interpolated. Duplicating the set here
#: would add a third place to keep in step with no security gained.
#:
#: The authoritative set is ``ProjectService._SORT_KEYS``: ``created_at``,
#: ``updated_at``, ``name``, ``status``, ``priority``, ``start_date``,
#: ``target_date``. Anything else — including ``description`` or ``owner_id`` —
#: is a 422 that never reaches the database.
_SORT_DESCRIPTION = "Sort key; one of the names the service allowlists."


@router.get(
    "",
    response_model=Page[ProjectRead],
    summary="List the caller's projects",
    dependencies=[Depends(require_permission(Permission.PROJECTS_READ))],
)
async def list_projects(
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    project_status: Annotated[
        ProjectStatus | None,
        Query(alias="status", description="Restrict to one lifecycle state."),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the name or description."),
    ] = None,
    sort: Annotated[str, Query(description=_SORT_DESCRIPTION)] = "created_at",
    order: Annotated[str, Query(description="'asc' or 'desc'.")] = "desc",
) -> Page[ProjectRead]:
    """List projects as one page of a filtered, sorted, owner-scoped sequence.

    **The scope is the caller's and only the caller's.** There is no ``owner_id``
    parameter and there is no way to ask for somebody else's page: a filter
    narrows the caller's own projects, it cannot widen them. ``meta.total`` is the
    size of the filtered set rather than the size of the slice, because it comes
    from a ``COUNT`` over the same statement the rows were drawn from.

    **Unknown ``sort``, ``order`` or ``status`` is a 422**, raised by the service
    against its allowlist before any SQL is built. A sort name is not sanitised,
    it is resolved against a fixed set of columns — which is what stops
    ``?sort=`` from being an injection point into ``ORDER BY``.

    See this module's docstring for why there is deliberately no ``priority``
    filter here.

    Errors: 422 for an unknown status, sort key or sort direction, or a
    ``limit`` outside 1-100.
    """
    return await projects.list(
        owner=current_user,
        limit=limit,
        offset=offset,
        status=project_status,
        search=search,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def create_project(
    payload: ProjectCreate,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Open a new project, always in its initial state.

    **The owner is the caller and cannot be named.** ``owner_id`` comes from the
    bearer token; a create endpoint that let the body choose its owner would hand
    any authenticated user the ability to file work into another account.

    A project is always created ``planned``. There is no path — API, import or
    fixture — through which a project comes into existence already finished, so
    the ``completed_at`` stamp a completion needs can never be missing.

    Errors: 422 for a window whose target date precedes its start date.
    """
    return await projects.create(owner=current_user, data=payload)


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Fetch one project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_READ))],
)
async def get_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Return one of the caller's projects.

    **404 for another account's project, never 403** — the id is resolved through
    a lookup scoped by ``owner_id``, so the row is never loaded and the refusal is
    the one a nonexistent id gets. See this module's docstring for why that
    distinction is load-bearing.

    Errors: 404 when the caller owns no project with this id.
    """
    return await projects.get(project_id=project_id, owner=current_user)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Edit a project's details",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Apply a partial update to a project the caller owns.

    **A field is written if the client named it** — clearing a description or a
    date is ``"field": null``, not an absent key.

    ``status`` is not on this payload and ``extra="forbid"`` makes sending it a
    422 naming the field. The lifecycle has its own endpoints because every edge
    of it carries a rule: completing stamps ``completed_at`` and leaving
    ``completed`` clears it, and a PATCH that could set the column would be a way
    around both.

    The date window is re-checked against the **persisted** row, not just against
    the payload. A PATCH that moves only ``start_date`` past a stored
    ``target_date`` carries nothing for the schema to compare against, and would
    otherwise persist a schedule no single request ever expressed.

    Errors: 404 for a project that is not the caller's; 422 for an inverted date
    window.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.update(project=project, data=payload, owner=current_user)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def delete_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Response:
    """Delete one of the caller's projects, and everything filed under it.

    **A hard delete, and the intended product answer is still ``/archive``.**
    Archiving is the terminal state a project is meant to reach; this is the
    explicit, destructive, user-initiated path for one that should not have been
    created. The two coexist because a local-first product that cannot delete its
    own data is lying to its user.

    Tasks cascade with the project — a task without a project has no meaning.
    Activity rows do **not**: ``activity_events.project_id`` is ``ON DELETE SET
    NULL``, so the record of what happened inside a deleted project outlives it
    with a null reference, which is more useful than a hole in the feed.

    **But tracked time does go, and this route does not say so.** The tasks that
    cascade carry the Phase 4 rows with them (``work_sessions`` and
    ``calendar_events`` both point at ``tasks.id`` with ``ON DELETE CASCADE``,
    ``app/models/planner.py:149-151`` and ``:272-280``), and both tables also
    cascade on ``project_id`` directly. So this one call removes **every minute
    the user actually spent in the project and every calendar entry reserved for
    it** — silently, and irreversibly. The analytics that read those rows then
    report fewer hours, which looks exactly like working less.

    The advice, which is the reason this paragraph exists: **archive rather than
    delete.** A held or archived project keeps its sessions, its bookings and its
    history, and analytics stop counting it as active work while its minutes stay
    on the record. Recommend to the owner that the destructive path be made
    opt-in — a confirmation naming the task count and the minutes about to be
    destroyed — rather than that the cascade be removed, which would leave
    sessions pointing at a project nobody can name.

    Errors: 404 for a project that is not the caller's, or that does not exist.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    await projects.delete(project=project, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/complete",
    response_model=ProjectRead,
    summary="Complete a project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def complete_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Finish a project, stamping ``completed_at``.

    **The project has to have been started.** A ``planned`` project may move to
    ``active``, ``on_hold`` or ``archived`` but not straight to ``completed``:
    work nobody started does not get to claim it finished. An ``on_hold`` project
    resumes first. Both refusals are 422 and both name the states the project
    could have gone to instead.

    Completing an already-completed project is a no-op rather than an error, so a
    retried click or a double-submitted form gets the project it asked for.

    Errors: 422 for an illegal transition; 404 for a project that is not the
    caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.set_status(
        project=project, status=ProjectStatus.COMPLETED.value, owner=current_user
    )


@router.post(
    "/{project_id}/activate",
    response_model=ProjectRead,
    summary="Move a planned project into the working set",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def activate_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Start a project: ``planned`` to ``active``.

    **The door that makes ``/complete`` reachable at all.** A project may only
    be completed *from* ``active`` — work nobody started does not get to claim
    it finished — so until a planned project had a way to start, a user who had
    not archived and restored it first could never finish it, and the completion
    endpoint answered 422 on every call. The edge was always in the service's
    legality table; no route walked it.

    Activating an already-active project is a no-op rather than an error, so a
    retried click or a double-submitted form gets the project it asked for.

    Errors: 422 for an illegal transition; 404 for a project that is not the
    caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.activate(project=project, owner=current_user)


@router.post(
    "/{project_id}/hold",
    response_model=ProjectRead,
    summary="Shelve a project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def hold_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Pause a project without archiving it.

    **Not an archive.** The project stays in the working set with its tasks and
    its window, and comes back through ``/resume``. Archiving hides it and
    stamps ``archived_at``; holding does neither, which is the whole difference
    and the reason the state is not redundant.

    Completing a held project straight away is refused, because the point of
    shelving is that the work paused.

    Errors: 422 for an illegal transition (a completed or archived project
    cannot be shelved); 404 for a project that is not the caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.hold(project=project, owner=current_user)


@router.post(
    "/{project_id}/resume",
    response_model=ProjectRead,
    summary="Take a held project back into the working set",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def resume_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Return a project on hold to ``active``.

    The one way out of a hold that is not an archive. Resuming anything that is
    not on hold is a no-op, for the same reason every other idempotent write
    here is one.

    It lands on ``active`` rather than on whatever the project held before it
    was shelved: there is no column recording that, the same lossy answer
    ``/restore`` gives and the same service-level trade behind it.

    Errors: 404 for a project that is not the caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.resume(project=project, owner=current_user)


@router.post(
    "/{project_id}/archive",
    response_model=ProjectRead,
    summary="Archive a project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def archive_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Hide a project from the working set, keeping it and its history.

    ``status`` and ``archived_at`` are stamped together, because both describe
    the same moment and a project that is ``archived`` with a null ``archived_at``
    cannot answer "archived when?". A completed project loses its
    ``completed_at`` on the way out — the stamp describes the status, and after
    this write the status is no longer ``completed``.

    Archiving an archived project is a no-op rather than an error, for the same
    reason every other idempotent write here is one.

    Errors: 404 for a project that is not the caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.archive(project=project, owner=current_user)


@router.post(
    "/{project_id}/restore",
    response_model=ProjectRead,
    summary="Restore an archived project",
    dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))],
)
async def restore_project(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> Project:
    """Return an archived project to the working set.

    The inverse of ``/archive``: ``archived_at`` is cleared, because the project
    is no longer archived and a lingering stamp would make "archived when?"
    answer with a moment it is not in.

    **It comes back ``active``, which is a lossy answer.** There is no column
    recording the status a project held before it was archived, so restoring a
    *completed* project returns it to ``active``. That is the service's own
    documented trade — see ``ProjectService._RESTORED_STATUS`` — and it is named
    here because it is observable from the wire rather than being an internal
    detail. Restoring anything that is not archived is a no-op.

    Errors: 404 for a project that is not the caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.restore(project=project, owner=current_user)


@router.get(
    "/{project_id}/summary",
    response_model=ProjectSummary,
    summary="A project's progress at a glance",
    dependencies=[Depends(require_permission(Permission.PROJECTS_READ))],
)
async def get_project_summary(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
) -> ProjectSummary:
    """Return the project plus its task counts and progress percentage.

    **The percentage is derived from the counts beside it**, inside the response
    model, so the number on screen can never disagree with the two numbers it is
    a percentage of. A project with no tasks reports ``0.0`` rather than null:
    an empty board is not "unknown", it is not started.

    Subtasks count toward ``task_count``. Excluding them would make a project's
    progress depend on how deeply somebody nested their checklist, which is a
    presentation decision dressed up as a metric.

    Errors: 404 for a project that is not the caller's.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await projects.summary(project=project, owner=current_user)


@router.get(
    "/{project_id}/activity",
    response_model=ActivityFeed,
    summary="A project's history",
    dependencies=[Depends(require_permission(Permission.PROJECTS_READ))],
)
async def list_project_activity(
    project_id: UUID,
    current_user: AuthenticatedUser,
    projects: ProjectServiceDep,
    activity: ActivityServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: Annotated[
        str | None,
        Query(description="Restrict to one event; an unknown name is a 422."),
    ] = None,
) -> ActivityFeed:
    """Return what happened inside one project, newest first.

    **The project is resolved before the history is read**, so asking for the
    timeline of a project that is not the caller's is a 404 rather than an empty
    feed that quietly confirms it exists.

    **This is the activity feed, not the audit trail.** It answers "what happened
    to this work"; ``audit_logs`` answers "was this account accessed
    suspiciously?" and is not reachable from here. A project completing is not a
    security event, and putting it on the audit trail would bury the sign-ins that
    trail exists to explain.

    ``event_type`` is validated against the vocabulary rather than passed through:
    a typo would otherwise be a filter that matches nothing and looks like "no
    such event happened".

    Errors: 404 for a project that is not the caller's; 422 for an unknown event
    name or a ``limit`` outside 1-100.
    """
    project = await projects.get(project_id=project_id, owner=current_user)
    return await activity.project_feed(
        project=project,
        owner=current_user,
        limit=limit,
        offset=offset,
        event_type=event_type,
    )


@router.get(
    "/{project_id}/tasks",
    response_model=Page[TaskRead],
    summary="List a project's tasks",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def list_project_tasks(
    project_id: UUID,
    current_user: AuthenticatedUser,
    tasks: TaskServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[
        TaskStatus | None,
        Query(description="Restrict to one column of the board."),
    ] = None,
) -> Page[TaskRead]:
    """Return one board column — the tasks of one of the caller's projects.

    Gated on ``tasks.read`` rather than ``projects.read``: it is the task board
    that is being served, and a role holding one capability is not thereby
    holding the other.

    **The project is resolved through the scoped lookup first**, so asking for
    the board of a project that is not the caller's is a 404 rather than an empty
    column that happens to confirm the project exists.

    Only ``status`` is offered as a filter, because that is what
    :meth:`~app.services.task_service.TaskService.list_for_project` accepts.
    ``priority``, ``search`` and the tag filter live on ``GET /tasks``, which is
    the route that can serve them — adding parameters here that the service drops
    would return an unfiltered column under the name of a filtered one.

    ``tag_ids`` and ``has_blocked_dependencies`` *are* correct on these rows: the
    service assembles list responses through the same page builder as
    ``GET /tasks``, so the tag join runs for the whole page in one query.

    Errors: 404 for a project that is not the caller's; 422 for an unknown status
    or a ``limit`` outside 1-100.
    """
    return await tasks.list_for_project(
        project_id=project_id,
        owner=current_user,
        limit=limit,
        offset=offset,
        status=status,
    )


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
