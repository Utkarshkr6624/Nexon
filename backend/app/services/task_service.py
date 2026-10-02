"""Task business logic: the board's rules, its dependencies, and its tags.

A task is a card, a subtask of a card, or a participant in a dependency graph.
This module owns what may happen to one: which status changes are legal, which
dependencies may be declared, how deep a subtask tree may go, and which writes
leave a trace in the activity feed.

Routers translate the exceptions raised here into HTTP responses — this module
never imports FastAPI.

**Audit is not activity.** :class:`~app.services.audit_service.AuditService`
writes ``audit_logs`` and answers *"who did what to their account, from where"*:
sign-ins, credential changes, session revocations. It is a security trail, it is
retained under its own policy, and Phase 6 analytics reads ``activity_events``
instead — never ``audit_logs``. Completing a task is not a security event;
writing it to the audit trail would bury the sign-ins that trail exists to
explain. So every mutation here writes an
:class:`~app.models.enums.ActivityEvent`, and the ``audit`` constructor parameter
is accepted for symmetry with the auth services and deliberately unused. The one
thing left unwritten is a *refused* cross-tenant create: there is no
``ActivityEvent`` member for an attempt that did not happen and the
``audit_logs`` vocabulary is fixed in Phase 2, so :meth:`TaskService.create`
raises and records nothing. Flagged for review — see that method.

Repository contract relied on by this module::

    TaskRepository.create(*, project_id, owner_id, title, description=None,
                          priority="medium", status="todo", parent_id=None,
                          start_date=None, due_date=None,
                          estimated_minutes=None, position=0) -> Task
    TaskRepository.get_by_id_for_user(task_id, owner_id) -> Task | None
    TaskRepository.list_for_user(owner_id, *, limit, offset, project_id=None,
                                 parent_id=None, status=None, priority=None,
                                 due_before=None, due_after=None, search=None,
                                 tag_ids=None, sort="created_at",
                                 order="desc") -> tuple[list[Task], int]
    TaskRepository.list_for_project(project_id, owner_id, *, limit, offset,
                                    status=None) -> tuple[list[Task], int]
    TaskRepository.next_position(project_id, parent_id) -> int
    TaskRepository.update_fields(task, **fields) -> Task
    TaskRepository.delete(task) -> None
    TaskRepository.stats_for_user(owner_id) -> dict[str, int]
    TaskRepository.list_subtasks(parent_id) -> list[Task]
    TaskRepository.add_dependency(*, task_id, depends_on_id) -> TaskDependency
    TaskRepository.remove_dependency(*, task_id, depends_on_id) -> bool
    TaskRepository.dependency_exists(task_id, depends_on_id) -> bool
    TaskRepository.list_dependencies(task_id) -> list[Task]

    ProjectRepository.get_by_id_for_user(project_id, owner_id) -> Project | None
    TagRepository.get_by_id_for_user(tag_id, user_id) -> Tag | None
    TagRepository.list_tags_for_tasks(task_ids) -> dict[uuid.UUID, list[Tag]]
    TagRepository.set_task_tags(task_id, tag_ids) -> None

Tenant isolation
----------------
**Every read is scoped by the caller's id in the query.**
:meth:`~app.repositories.task.TaskRepository.get_by_id_for_user` puts
``owner_id`` in the ``WHERE`` clause, so a task the caller may not see is never
loaded and "not yours" is indistinguishable from "does not exist". That is what
keeps this module's endpoints from being an enumeration oracle.

Two consequences are worth naming. A task's project must belong to the caller —
:attr:`tasks.owner_id` is denormalised, so nothing in the schema stops a row
being filed under another account's project, and :meth:`TaskService.create` is
where that is stopped. And a tag id is *not* authorisation of anything: tags are
per-user rows, so every tag this module acts on is resolved through
``get_by_id_for_user`` before it is written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    ActivityEvent,
    TaskPriority,
    TaskStatus,
    validate_task_priority,
    validate_task_status,
)
from app.models.task import Task
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.schemas.common import Page, PageMeta
from app.schemas.task import TaskCreate, TaskRead, TaskStats, TaskUpdate
from app.services.audit_service import AuditService

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.services.activity_service import ActivityService

__all__ = ["TaskService"]

#: The default page size when a caller does not name one.
DEFAULT_PAGE_SIZE = 50

#: The legal task transitions, written down once.
#:
#: As with a project, a status is a lifecycle and not a field: reaching
#: ``completed`` stamps ``completed_at``, leaving it clears that stamp, and
#: blocking records why. :class:`~app.schemas.task.TaskUpdate` deliberately has
#: no ``status`` field, so :meth:`TaskService.set_status` is the only door, and
#: this table is the whole of what it will open onto.
#:
#: * ``TODO`` may start, be blocked, or be cancelled. It cannot be *completed*
#:   directly: work nobody started does not get to claim it was finished. It has
#:   to go through ``IN_PROGRESS``.
#: * ``IN_PROGRESS`` may return to ``TODO``, be blocked, be completed, or be
#:   cancelled. Returning to ``TODO`` is how a user backs a card out of "I am on
#:   it" without inventing a state for it.
#: * ``BLOCKED`` may go back to ``TODO``, be picked up (``IN_PROGRESS``) or be
#:   cancelled. It may not be completed straight from blocked — the point of
#:   blocking is that the work did not happen.
#: * ``COMPLETED`` may be reopened, either to ``IN_PROGRESS`` (picked back up)
#:   or to ``TODO`` (:meth:`reopen`).
#: * ``CANCELLED`` is **terminal**. There is no "uncancel": work that was
#:   deliberately abandoned is not work that failed, and letting it be re-opened
#:   would quietly fold "dropped" back into the backlog the status was invented
#:   to keep separate. A cancelled task is re-created if it is genuinely needed
#:   again.
#:
#: As on the project service, a transition to the status a task already holds is
#: short-circuited into a no-op before this table is consulted, so a retry is
#: idempotent rather than a 422.
_LEGAL_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset(
        {TaskStatus.TODO, TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.CANCELLED}
    ),
    TaskStatus.BLOCKED: frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS}),
    TaskStatus.CANCELLED: frozenset(),
}

#: The statuses a dependency has to reach before it stops blocking.
#:
#: Only ``COMPLETED`` satisfies a dependency. ``CANCELLED`` deliberately does
#: not: a prerequisite that was dropped is not a prerequisite that was met, and
#: quietly treating it as met would put a completed card on the board resting on
#: work nobody did. The user who knows the prerequisite is off the board removes
#: the edge — which is exactly what :meth:`TaskService.remove_dependency` is
#: for. Flagged for review: it is a strict rule, and the alternative is a task
#: blocked forever by abandoned work.
_SATISFIES_DEPENDENCY = TaskStatus.COMPLETED

#: The columns a plain detail update may write.
#:
#: ``status`` and ``completed_at`` are absent because they belong to the
#: transitions; ``position`` is absent because board ordering is rewritten by a
#: reorder endpoint that validates a whole set at once; ``project_id`` *is*
#: present, because moving a task between the caller's own projects is an
#: ordinary edit — but the destination is ownership-checked like any other
#: project reference, because ownership is a property of the row being written
#: and not of the payload proposing it. ``owner_id``, ``parent_id``'s identity
#: and ``actual_minutes`` are structural.
_UPDATABLE_FIELDS = frozenset(
    {
        "description",
        "due_date",
        "estimated_minutes",
        "parent_id",
        "priority",
        "project_id",
        "start_date",
        "title",
    }
)

#: Sort keys the listing accepts. See the project service for why both this
#: service and the repository validate, and why neither falls back to a default.
_SORT_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "title",
        "status",
        "priority",
        "start_date",
        "due_date",
        "position",
        "completed_at",
    }
)
_SORT_ORDERS = frozenset({"asc", "desc"})

_TASK_NOT_FOUND = "Task not found."
_PROJECT_NOT_FOUND = "Project not found."
_TAG_NOT_FOUND = "Tag not found."
_SELF_DEPENDENCY = "A task cannot depend on itself."

#: How deep a subtask tree may go: exactly one level.
#:
#: A subtask's parent must itself be a root card. Two levels is where the cost
#: starts: the board collapses children by one level, so anything deeper is
#: invisible in the product rather than merely awkward, and every roll-up of a
#: parent's progress would have to decide what a grandchild means. A rule the UI
#: cannot render is a rule enforced here rather than left to the client, and the
#: error names the reason so the caller is not left guessing.
_MAX_SUBTASK_DEPTH = 1


class TaskService:
    """The rules of the task board, its dependencies and its tags."""

    def __init__(
        self,
        repository: TaskRepository,
        project_repository: ProjectRepository,
        tag_repository: TagRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repository: Task persistence for the request-scoped session.
            project_repository: Needed because a task's project must be
                re-checked on every create and on every move, and only the
                project repository knows how to answer that scoped.
            tag_repository: Tags are per-user rows, so a tag is resolved through
                the tag repository rather than by id alone.
            activity: Where domain events are written. Optional so the rules can
                be exercised without a history sink.
            audit: Accepted for symmetry with the auth services and
                deliberately unused — see the module docstring.
            settings: Application settings, resolved from the environment when
                not supplied.
        """
        self.repository = repository
        self.project_repository = project_repository
        self.tag_repository = tag_repository
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()

    # -- Creation ------------------------------------------------------------

    async def create(self, *, owner: User, data: TaskCreate) -> Task:
        """Create a task under a project the caller owns.

        Three things are verified before the row is inserted, and each closes a
        hole that the schema cannot:

        1. **The project belongs to the caller.**
           :attr:`tasks.owner_id` is denormalised from the project, so nothing in
           the database stops a task being filed under another account's
           project id. The lookup is scoped by ``owner_id``, which means the
           refusal is ``NotFoundError`` — the caller cannot tell "not yours"
           from "does not exist", and no row is written. Attaching a task to
           somebody else's project would be the cleanest cross-tenant write in
           the product: the row would appear on their board.
        2. **The parent is the caller's, in the same project, and is itself a
           root card.** See :data:`_MAX_SUBTASK_DEPTH`.
        3. **The board position.** ``next_position`` appends to the column, so
           two cards created in the same second do not land on the same index.

        ``status`` is settable at creation — :class:`~app.schemas.task.TaskCreate`
           is the one payload that carries it, because a backlog imported from
           elsewhere may legitimately arrive already under way or already
           blocked. Creating a task as ``completed`` is the one case that needs a
           second statement, because ``completed_at`` is not a column
           :meth:`TaskRepository.create` accepts and a completed card with a null
           ``completed_at`` is exactly the row this whole design refuses to
           produce.

        Args:
            owner: The authenticated caller.
            data: The creation payload.

        Returns:
            The persisted task.

        Raises:
            NotFoundError: If the caller owns no project with that id, or no
                parent task with that id. No row is written in either case.
            ValidationError: If the parent is in another project or is itself a
                subtask.
            ConflictError: If the unique indexes reject the row, which they
                should not for a service-created task.
        """
        project = await self.project_repository.get_by_id_for_user(data.project_id, owner.id)
        if project is None:
            # Nothing is recorded for the refusal. It is genuinely interesting —
            # naming a project id you do not own is the shape of an IDOR probe —
            # but ``ActivityEvent`` has no member for a refused create and
            # ``audit_logs`` is a security trail whose vocabulary is fixed in
            # Phase 2, so there is no row this may honestly write. Raising the
            # ``NotFoundError`` is the whole of the answer. Flagged for review:
            # a cross-tenant access event is worth a first-class member.
            raise NotFoundError(_PROJECT_NOT_FOUND)
        parent: Task | None = None
        if data.parent_id is not None:
            parent = await self.repository.get_by_id_for_user(data.parent_id, owner.id)
            if parent is None:
                raise NotFoundError(_TASK_NOT_FOUND)
            _check_parent(parent, project_id=project.id)
        position = await self.repository.next_position(project.id, data.parent_id)
        try:
            task = await self.repository.create(
                project_id=project.id,
                owner_id=owner.id,
                title=data.title,
                description=data.description,
                priority=data.priority.value,
                status=data.status.value,
                parent_id=parent.id if parent is not None else None,
                start_date=data.start_date,
                due_date=data.due_date,
                estimated_minutes=data.estimated_minutes,
                position=position,
            )
        except IntegrityError as exc:
            raise ConflictError("This task conflicts with an existing one.") from exc
        if data.status is TaskStatus.COMPLETED:
            task = await self.repository.update_fields(task, completed_at=datetime.now(UTC))
        await self._record(
            ActivityEvent.TASK_CREATED,
            owner=owner,
            task=task,
            project_id=project.id,
            metadata={"title": task.title, "status": task.status},
        )
        return task

    async def get(self, *, task_id: uuid.UUID, owner: User) -> Task:
        """Return one of the caller's tasks.

        Raises:
            NotFoundError: If the caller owns no task with this id. Another
                user's task and a nonexistent one are the same answer, so the
                endpoint cannot be used to find out which task ids are real.
        """
        task = await self.repository.get_by_id_for_user(task_id, owner.id)
        if task is None:
            raise NotFoundError(_TASK_NOT_FOUND)
        return task

    async def list(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        due_before: date | None = None,
        due_after: date | None = None,
        search: str | None = None,
        tag_ids: Sequence[uuid.UUID] | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> Page[TaskRead]:
        """List the caller's tasks as a page.

        Args:
            owner: The authenticated caller. The scope is the caller's own.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            project_id: Restrict to one project. The project's ownership is not
                re-checked here: it would filter to nothing either way, since
                every task returned is already the caller's, and a project that
                is not theirs cannot own a task that is.
            status: Restrict to one status.
            priority: Restrict to one priority.
            due_before: Only tasks due on or before this date.
            due_after: Only tasks due on or after this date.
            search: Case-insensitive substring over title or description.
            tag_ids: Restrict to tasks carrying these tags. Every id is resolved
                through the tag repository first, so a caller cannot learn
                whether a tag id that is not theirs exists by watching the result
                change.
            sort: One of :data:`_SORT_KEYS`.
            order: ``"asc"`` or ``"desc"``.

        Returns:
            The page of tasks and its metadata.

        Raises:
            ValidationError: If the page window, a filter value, a sort key or a
                sort order is not one this service accepts, or a tag is not the
                caller's.
            NotFoundError: If one of ``tag_ids`` is not the caller's.
        """
        _check_window(limit=limit, offset=offset)
        status_value = _task_status_or_none(status)
        priority_value = _task_priority_or_none(priority)
        sort = _check_sort(sort, order)
        tags = await self._owned_tags(tag_ids, owner)
        rows, total = await self.repository.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            project_id=project_id,
            status=status_value,
            priority=priority_value,
            due_before=due_before,
            due_after=due_after,
            search=search,
            tag_ids=list(tags) or None,
            sort=sort,
            order=order,
        )
        return await self._page(rows, total=total, limit=limit, offset=offset, owner=owner)

    async def list_for_project(
        self,
        *,
        project_id: uuid.UUID,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        status: str | None = None,
    ) -> Page[TaskRead]:
        """List one board column — the tasks of one of the caller's projects.

        The project is resolved through the scoped project lookup first, so
        asking for a board belonging to somebody else is a ``NotFoundError``
        rather than an empty column that happens to confirm the project exists.

        Args:
            project_id: The project whose board is wanted.
            owner: The authenticated caller.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            status: Restrict to one status, which is how a column is fetched.

        Returns:
            The page of tasks and its metadata.

        Raises:
            NotFoundError: If the caller does not own the project.
            ValidationError: If the page window or the status filter is invalid.
        """
        _check_window(limit=limit, offset=offset)
        status_value = _task_status_or_none(status)
        project = await self.project_repository.get_by_id_for_user(project_id, owner.id)
        if project is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        rows, total = await self.repository.list_for_project(
            project.id,
            owner.id,
            limit=limit,
            offset=offset,
            status=status_value,
        )
        return await self._page(rows, total=total, limit=limit, offset=offset, owner=owner)

    # -- Detail edits --------------------------------------------------------

    async def update(self, *, task: Task, data: TaskUpdate, owner: User) -> Task:
        """Apply a partial update to a task's details.

        **PATCH semantics: a field is written if the client named it.**
        ``model_dump(exclude_unset=True)`` is what distinguishes "sent as null"
        from "absent", and it matters here: ``description``, ``due_date``,
        ``start_date`` and ``estimated_minutes`` are all genuinely nullable, and a
        guard written as ``if data.due_date is not None`` could never clear one
        again once it had been set.

        Three things are re-checked against the persisted row rather than trusted
        from the payload, because each of them is a property of the row being
        written and not of the request proposing it:

        * **A move to another project** is resolved through the scoped project
          lookup first. ``TaskUpdate`` deliberately carries ``project_id``, so
          without this check a caller could file a task into somebody else's
          project.
        * **A new parent** must be the caller's, in the task's *effective*
          project — the destination one when the same PATCH also moves the task
          — and must itself be a root card.
        * **The window.** The schema only compares two dates sent together; a
          PATCH that moves one of them has to be checked against the stored
          other.

        Status is not writable here and not present on the schema; ``position``
        is likewise absent. See :data:`_UPDATABLE_FIELDS`.

        Args:
            task: The task, already resolved for this owner.
            data: The fields to change.
            owner: The authenticated caller.

        Returns:
            The updated task, or the unchanged one when nothing writable was
            sent.

        Raises:
            NotFoundError: If the row, the destination project or the new parent
                is not the caller's.
            ValidationError: If the resulting window ends before it starts, or
                the new parent is in another project or is itself a subtask.
        """
        self._owned(task, owner)
        sent = data.model_dump(exclude_unset=True)
        fields = {key: value for key, value in sent.items() if key in _UPDATABLE_FIELDS}
        effective_project_id = task.project_id
        if "project_id" in fields:
            destination = await self.project_repository.get_by_id_for_user(
                fields["project_id"], owner.id
            )
            if destination is None:
                raise NotFoundError(_PROJECT_NOT_FOUND)
            if destination.id != task.project_id and await self.repository.count_subtasks(task.id):
                # The repository cannot know this: whether a task has children is
                # a relationship, and moving the parent out from under them would
                # leave rows whose ``parent_id`` points into another project —
                # the exact condition ``_check_parent`` refuses to create and one
                # PATCH must not be able to produce behind its back.
                raise ValidationError(
                    "Move or delete this task's subtasks before moving the task itself."
                )
            effective_project_id = destination.id
        if "parent_id" in fields and fields["parent_id"] is not None:
            parent = await self.repository.get_by_id_for_user(fields["parent_id"], owner.id)
            if parent is None:
                raise NotFoundError(_TASK_NOT_FOUND)
            _check_parent(parent, project_id=effective_project_id, task=task)
        start_date = fields.get("start_date", task.start_date)
        due_date = fields.get("due_date", task.due_date)
        if start_date is not None and due_date is not None and due_date < start_date:
            raise ValidationError("due_date must not be earlier than start_date.")
        if not fields:
            return task
        # Captured before the write, because ``update_fields`` mutates this very
        # instance — comparing against ``task.due_date`` afterwards would always
        # find it unchanged and the event below would never fire.
        previous_due_date = task.due_date
        updated = await self.repository.update_fields(task, **fields)
        await self._record(
            ActivityEvent.TASK_UPDATED,
            owner=owner,
            task=updated,
            metadata={"fields": sorted(fields)},
        )
        if "due_date" in fields and fields["due_date"] != previous_due_date:
            # A deadline changing is its own moment in the feed: it is what the
            # schedule report is built from, and burying it inside a generic
            # update list would make "when did this slip?" unanswerable.
            await self._record(
                ActivityEvent.TASK_DUE_DATE_CHANGED,
                owner=owner,
                task=updated,
                metadata={"due_date": str(updated.due_date) if updated.due_date else None},
            )
        return updated

    # -- Lifecycle -----------------------------------------------------------

    async def set_status(
        self,
        *,
        task: Task,
        status: str | TaskStatus,
        owner: User,
        note: str | None = None,
    ) -> Task:
        """Move a task to another status, or refuse.

        This is the **only** door to a status change; :meth:`update` cannot reach
        the column and the schema cannot carry it. The legal edges are
        :data:`_LEGAL_TRANSITIONS`, and the timestamps follow the status in both
        directions: reaching ``COMPLETED`` stamps ``completed_at`` and leaving
        ``COMPLETED`` clears it, so a reopened task cannot keep reporting the
        moment it was finished.

        Each legal edge writes its own activity event rather than a generic
        update — ``started``, ``completed``, ``reopened``, ``blocked`` are the
        four moments a task history exists to remember. ``note`` rides along on
        the event; it is never written onto the task itself, because "why is
        this blocked?" belongs to the history and not to the card.

        Args:
            task: The task, already resolved for this owner.
            status: The target :class:`~app.models.enums.TaskStatus` value.
            owner: The authenticated caller.
            note: Optional context for the resulting event.

        Returns:
            The task in its new state, or unchanged when it was already there.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the status is unknown, the row's own status has
                drifted, the transition is not in :data:`_LEGAL_TRANSITIONS`, or
                the task is being completed while a dependency is still open
                (see :meth:`complete`).
        """
        self._owned(task, owner)
        target = _task_status_or_raise(status)
        current = _current_status(task)
        if current == target:
            return task
        if target not in _LEGAL_TRANSITIONS[current]:
            raise ValidationError(
                f"A task cannot move from {current.value!r} to {target.value!r}.",
                details={
                    "from": current.value,
                    "to": target.value,
                    "allowed": sorted(member.value for member in _LEGAL_TRANSITIONS[current]),
                },
            )
        if target is TaskStatus.COMPLETED:
            await self._refuse_if_blocked(task, owner)
        fields: dict[str, object] = {"status": target.value}
        if target is TaskStatus.COMPLETED:
            fields["completed_at"] = datetime.now(UTC)
        elif current is TaskStatus.COMPLETED:
            fields["completed_at"] = None
        updated = await self.repository.update_fields(task, **fields)
        metadata: dict[str, object] = {"from": current.value, "to": target.value}
        if note is not None:
            metadata["note"] = note
        await self._record(
            _transition_event(current, target), owner=owner, task=updated, metadata=metadata
        )
        return updated

    async def complete(self, *, task: Task, owner: User) -> Task:
        """Complete a task, refusing while anything it waits on is unfinished.

        A dependency that is not ``COMPLETED`` blocks the completion. This is
        the whole point of declaring one, and it is why ``complete`` is not a
        one-line alias for ``set_status``: the edge exists to be enforced, and an
        enforcement point that a caller can route around by writing the status
        some other way is not one. The refusal names the open prerequisites, so
        the user is told what to finish rather than merely that something is in
        the way.

        See :data:`_SATISFIES_DEPENDENCY` for why ``CANCELLED`` does not satisfy
        a dependency.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The completed task, with ``completed_at`` stamped.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the task is not in a status that may be
            completed, or a dependency is still open.
        """
        return await self.set_status(task=task, status=TaskStatus.COMPLETED, owner=owner)

    async def reopen(self, *, task: Task, owner: User) -> Task:
        """Reopen a completed task, clearing ``completed_at``.

        A task that is not ``COMPLETED`` is returned unchanged: "reopen" on a
        task that is already open is a no-op rather than an error, for the same
        reason the status no-op is one.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The reopened task, with ``completed_at`` cleared.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the row's status has drifted, or the task was
                cancelled — which is terminal, and reopening a cancelled task is
                the one thing :meth:`set_status` will not do for anyone.
        """
        self._owned(task, owner)
        current = _current_status(task)
        if current is TaskStatus.COMPLETED:
            return await self.set_status(task=task, status=TaskStatus.TODO, owner=owner)
        if current is TaskStatus.CANCELLED:
            raise ValidationError(
                "A cancelled task cannot be reopened; create a new task if it is needed again.",
                details={"from": current.value, "to": TaskStatus.TODO.value},
            )
        return task

    async def block(self, *, task: Task, owner: User, reason: str | None = None) -> Task:
        """Mark a task blocked, recording why.

        ``reason`` is attached to the activity event rather than to the task.
        The reason is history — "waiting on the vendor" was true on Tuesday and
        is not true on Friday — and a task row that carried a mutable "why"
        column would be read as a current fact by every client that renders it.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.
            reason: Why the task is blocked, recorded on the event.

        Returns:
            The blocked task.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the task's status cannot move to ``blocked``, or
                its status has drifted.
        """
        return await self.set_status(task=task, status=TaskStatus.BLOCKED, owner=owner, note=reason)

    async def set_priority(self, *, task: Task, priority: str | TaskPriority, owner: User) -> Task:
        """Re-prioritise a task.

        Separate from :meth:`update` because a priority change is its own event
        in the feed — it is how the board gets re-triaged, and a report on that
        has to be able to tell it apart from a retype of the title.

        Args:
            task: The task, already resolved for this owner.
            priority: The new :class:`~app.models.enums.TaskPriority`.
            owner: The authenticated caller.

        Returns:
            The re-prioritised task.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the priority is not a known value.
        """
        self._owned(task, owner)
        value = _task_priority_or_raise(priority)
        previous = task.priority
        updated = await self.repository.update_fields(task, priority=value.value)
        await self._record(
            ActivityEvent.TASK_PRIORITY_CHANGED,
            owner=owner,
            task=updated,
            metadata={"from": previous, "to": value.value},
        )
        return updated

    async def delete(self, *, task: Task, owner: User) -> None:
        """Delete a task and everything that pointed at it.

        A real delete rather than a status change: ``cancelled`` already exists
        for work that was abandoned, so a row only disappears when the user
        asked for it to. Subtasks, dependency edges and tag edges cascade; the
        activity rows do not — ``activity_events.task_id`` is ``ON DELETE SET
        NULL``, so deleting a task does not erase the record of what happened to
        it.

        The id is captured before the delete because the object is no longer
        usable afterwards, and the event is written after it so a failed delete
        leaves no event claiming the task is gone.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.

        Raises:
            NotFoundError: If the row does not belong to the caller.
        """
        self._owned(task, owner)
        task_id = task.id
        project_id = task.project_id
        # Read before the delete: afterwards the instance is expired and every
        # attribute access on it would raise rather than return the row's value.
        title = task.title
        status = task.status
        await self.repository.delete(task)
        # ``task_id`` is deliberately NOT passed. ``ON DELETE SET NULL`` only
        # rewrites rows that already exist — it does not make a NEW row's
        # dangling reference legal — so writing this event with the id it just
        # deleted raises ``ForeignKeyViolation`` inside the best-effort audit
        # write, which then swallows it and the deletion event is silently lost.
        # The id still reaches the row, in ``metadata``, so a reader can still
        # identify what was deleted; what is gone is the join, which no longer
        # has anything to join to.
        await self._record(
            ActivityEvent.TASK_DELETED,
            owner=owner,
            project_id=project_id,
            task_id=None,
            metadata={"task_id": str(task_id), "title": title, "status": status},
        )

    # -- Tags ----------------------------------------------------------------

    async def add_tag(self, *, task: Task, tag_id: uuid.UUID, owner: User) -> list[uuid.UUID]:
        """Attach one of the caller's tags to a task.

        Adding a tag already on the task is a no-op that returns the same set.

        Args:
            task: The task, already resolved for this owner.
            tag_id: The tag to attach. Resolved through the scoped tag lookup, so
                another account's tag id is a ``NotFoundError`` rather than a row
                quietly written against a tag the caller cannot see.
            owner: The authenticated caller.

        Returns:
            The task's tag ids afterwards.

        Raises:
            NotFoundError: If the task is not the caller's, or the tag is not.
        """
        self._owned(task, owner)
        await self._require_tag(tag_id, owner)
        current = await self._tag_ids(task)
        if tag_id not in current:
            current = [*current, tag_id]
            await self.tag_repository.set_task_tags(task.id, current)
        return current

    async def remove_tag(self, *, task: Task, tag_id: uuid.UUID, owner: User) -> list[uuid.UUID]:
        """Detach a tag from a task.

        Removing a tag the task does not carry is a no-op, so a retried removal
        is idempotent.

        Args:
            task: The task, already resolved for this owner.
            tag_id: The tag to detach.
            owner: The authenticated caller.

        Returns:
            The task's tag ids afterwards.

        Raises:
            NotFoundError: If the task is not the caller's.
        """
        self._owned(task, owner)
        current = await self._tag_ids(task)
        if tag_id in current:
            current = [existing for existing in current if existing != tag_id]
            await self.tag_repository.set_task_tags(task.id, current)
        return current

    async def set_tags(
        self, *, task: Task, tag_ids: Sequence[uuid.UUID], owner: User
    ) -> list[uuid.UUID]:
        """Replace a task's tags with exactly this set.

        Every id is resolved through the scoped tag lookup **before** anything is
        written, so a request naming one of another account's tags changes
        nothing at all rather than half-applying.

        An empty sequence clears the task's tags, which is the meaning of "set
        these tags" and not a no-op.

        Args:
            task: The task, already resolved for this owner.
            tag_ids: The tags the task should end up carrying.
            owner: The authenticated caller.

        Returns:
            The task's tag ids afterwards.

        Raises:
            NotFoundError: If the task is not the caller's, or any tag is not.
        """
        self._owned(task, owner)
        wanted = await self._owned_tags(tag_ids, owner)
        await self.tag_repository.set_task_tags(task.id, list(wanted))
        return list(wanted)

    # -- Dependencies --------------------------------------------------------

    async def add_dependency(
        self, *, task: Task, depends_on: uuid.UUID | Task, owner: User
    ) -> None:
        """Declare that ``task`` cannot finish before ``depends_on`` does.

        Four rules, in the order they are checked.

        **Self-reference is refused here even though the database refuses it
        too.** ``CHECK (task_id <> depends_on_id)`` on ``task_dependencies`` makes
        it impossible for *every* writer — an import, a script, a future service
        — and that is the constraint that matters. This check exists so the
        caller gets a ``ValidationError`` naming the problem instead of a driver
        error: one code path that is friendly, and one that is total.

        **Cycles are refused here because nothing else can.** A two-node cycle
        needs no database machinery; a longer one does. Ruling it out with a
        recursive trigger or a deferred constraint would be paid on every insert
        into a table the product writes constantly, to prevent something a
        depth-first walk catches for free — see :meth:`_would_cycle`.

        **Cross-project dependencies are refused**, and that is a choice rather
        than a limitation. A dependency is a statement that two cards belong to
        the same piece of work: the board renders a blocker next to the card it
        blocks, the dependency panel answers "what am I waiting on?" from
        inside one project, and a project is the unit a user archives or deletes.
        An edge from a card in project A to one in project B survives A's
        deletion in name only, shows up on a board that has no column for it, and
        couples two aggregates that are otherwise independent. Refusing it keeps
        every dependency readable from one board.

        Args:
            task: The task being blocked, already resolved for this owner.
            depends_on: The task it waits on, by id or as an already-loaded row.
                A row is still checked against the owner before it is used, so
                neither form of this argument can reach another tenant's task.
            owner: The authenticated caller.

        Raises:
            NotFoundError: If ``depends_on`` is not the caller's.
            ValidationError: If it is the task itself, lives in another project,
                or would close a cycle.
            ConflictError: If the edge already exists.
        """
        self._owned(task, owner)
        blocker = await self._resolve(depends_on, owner)
        if blocker.id == task.id:
            raise ValidationError(_SELF_DEPENDENCY)
        if blocker.project_id != task.project_id:
            raise ValidationError(
                "A dependency must be on a task in the same project.",
                details={
                    "project_id": str(task.project_id),
                    "depends_on_project_id": str(blocker.project_id),
                },
            )
        if await self.repository.dependency_exists(task.id, blocker.id):
            raise ConflictError("This dependency already exists.")
        if await self._would_cycle(task_id=task.id, depends_on_id=blocker.id, owner_id=owner.id):
            raise ValidationError(
                "This dependency would create a cycle.",
                details={"task_id": str(task.id), "depends_on_id": str(blocker.id)},
            )
        try:
            await self.repository.add_dependency(task_id=task.id, depends_on_id=blocker.id)
        except IntegrityError as exc:
            # The CHECK and the unique constraint, both enforced in the database
            # for every writer. Losing either race arrives here, and both are a
            # 409 rather than a 500.
            raise ConflictError("This dependency could not be recorded.") from exc
        await self._record(
            # ``ActivityEvent`` has no TASK_DEPENDENCY_ADDED. Rather than invent
            # a type nothing filters on, the edge rides on the generic update
            # with the ids in the metadata — a Phase 3 vocabulary review should
            # add the member. Flagged for review.
            ActivityEvent.TASK_UPDATED,
            owner=owner,
            task=task,
            metadata={"added_dependency": str(blocker.id), "depends_on_title": blocker.title},
        )

    async def remove_dependency(
        self, *, task: Task, depends_on: uuid.UUID | Task, owner: User
    ) -> None:
        """Drop one dependency edge.

        This is also the documented way out of a blocked completion: a
        prerequisite that was cancelled and is never going to be finished is
        unblocked by removing the edge, which is why this answers
        ``NotFoundError`` rather than succeeding silently on an edge that is not
        there — a client that removed the wrong thing is told so.

        Args:
            task: The blocked task, already resolved for this owner.
            depends_on: The task it was waiting on.
            owner: The authenticated caller.

        Raises:
            NotFoundError: If ``depends_on`` is not the caller's, or the edge
                does not exist.
        """
        self._owned(task, owner)
        blocker = await self._resolve(depends_on, owner)
        if not await self.repository.remove_dependency(task_id=task.id, depends_on_id=blocker.id):
            raise NotFoundError("That dependency does not exist.")
        await self._record(
            ActivityEvent.TASK_UPDATED,
            owner=owner,
            task=task,
            metadata={"removed_dependency": str(blocker.id)},
        )

    async def list_dependencies(self, *, task: Task, owner: User) -> list[Task]:
        """Return the tasks this one is waiting on, in board order.

        The rows are filtered to the caller even though the query reaches them
        through a task that is already the caller's. That is a tripwire rather
        than the authorisation — see :meth:`_owned` — and it costs nothing.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The blocking tasks.

        Raises:
            NotFoundError: If the row does not belong to the caller.
        """
        self._owned(task, owner)
        return [
            row
            for row in await self.repository.list_dependencies(task.id)
            if row.owner_id == owner.id
        ]

    async def list_subtasks(self, *, task: Task, owner: User) -> list[Task]:
        """Return a task's direct subtasks, in board order.

        One level, by construction — see :data:`_MAX_SUBTASK_DEPTH`. The method
        returns the children and nothing more, so the depth rule is not something
        this method has to defend against at read time.

        Args:
            task: The task, already resolved for this owner.
            owner: The authenticated caller.

        Returns:
            The direct children, filtered to the caller.

        Raises:
            NotFoundError: If the row does not belong to the caller.
        """
        self._owned(task, owner)
        return [
            row for row in await self.repository.list_subtasks(task.id) if row.owner_id == owner.id
        ]

    # -- Aggregates ----------------------------------------------------------

    async def stats(self, *, owner: User) -> TaskStats:
        """Return the caller's tasks counted by status, plus the overdue total.

        The status buckets come from one grouped query. ``overdue`` is a
        different question from the breakdown — "how much of this is late?" — and
        it is counted across the three open statuses rather than folded into one,
        so the buckets stay a partition of the caller's tasks.

        It is also three small grouped statements rather than one dedicated
        count, because the repository exposes no ``count_overdue_for_user``. The
        ``total`` each returns is unpaginated, so ``limit=1`` costs the same
        round trip as ``limit=50`` and the number does not depend on a page
        size. A single method on the repository would collapse this to one
        statement; flagged for the next pass.

        Overdue is *strictly* behind today, matching the rule
        :class:`~app.schemas.task.TaskRead` derives client-side, so the badge a
        server-built response shows and the one a client builds from the same row
        cannot disagree.

        Args:
            owner: The authenticated caller.

        Returns:
            The counts.
        """
        counts = await self.repository.stats_for_user(owner.id)
        cutoff = date.today() - timedelta(days=1)
        overdue = 0
        for status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
            _, bucket = await self.repository.list_for_user(
                owner.id, limit=1, offset=0, status=status.value, due_before=cutoff
            )
            overdue += bucket
        return TaskStats(
            total=counts.get("total", 0),
            todo=counts.get(TaskStatus.TODO.value, 0),
            in_progress=counts.get(TaskStatus.IN_PROGRESS.value, 0),
            blocked=counts.get(TaskStatus.BLOCKED.value, 0),
            completed=counts.get(TaskStatus.COMPLETED.value, 0),
            cancelled=counts.get(TaskStatus.CANCELLED.value, 0),
            overdue=overdue,
        )

    # -- Internals -----------------------------------------------------------

    async def _would_cycle(
        self, *, task_id: uuid.UUID, depends_on_id: uuid.UUID, owner_id: uuid.UUID
    ) -> bool:
        """Report whether the new edge ``task -> depends_on`` would close a cycle.

        **The algorithm.** Adding ``task depends_on X`` creates a cycle exactly
        when ``task`` is already reachable from ``X`` by following existing
        dependencies — that is, when X waits (directly or transitively) for
        ``task``. So the check is a reachability walk *from X*, not from
        ``task``, and it never has to consider the edge being added: the graph is
        the one that exists before this call.

        The walk is an explicit-stack depth-first search with a ``visited`` set.
        It terminates because a node already on the stack is not expanded twice,
        which matters even though the graph is supposed to be acyclic — the
        guarantee is only as good as every writer, and an import that bypassed
        this service would otherwise turn a "add one dependency" call into a
        hang. Without the ``visited`` set the walk would be exponential on a
        diamond graph before it hung.

        Cost is bounded by the number of rows reachable from X and one indexed
        query each, which for the handful of prerequisites a card carries is
        cheaper than any schema-level guarantee and is exact.
        """
        stack = [depends_on_id]
        visited: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            # Checked on pop and before the ``visited`` test, so reaching the task
            # itself is a cycle even if it was pushed twice.
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for blocker in await self.repository.list_dependencies(current):
                if blocker.owner_id != owner_id:
                    # Not reached through the owner's own graph; skipping keeps a
                    # cross-tenant edge from steering the walk.
                    continue
                stack.append(blocker.id)
        return False

    async def _refuse_if_blocked(self, task: Task, owner: User) -> None:
        """Raise if anything ``task`` waits on is still unfinished."""
        blocking = await self.list_dependencies(task=task, owner=owner)
        open_tasks = [
            blocker for blocker in blocking if _current_status(blocker) is not _SATISFIES_DEPENDENCY
        ]
        if not open_tasks:
            return
        raise ValidationError(
            "This task is still waiting on unfinished work.",
            details={
                "blocked_by": [
                    {"id": str(blocker.id), "title": blocker.title, "status": blocker.status}
                    for blocker in open_tasks
                ]
            },
        )

    async def _resolve(self, depends_on: uuid.UUID | Task, owner: User) -> Task:
        """Return the task a dependency argument names, or raise.

        An id is resolved through the scoped lookup, which is the normal case and
        the safe one. An already-loaded row is accepted so a caller that has just
        built one does not pay for a second query, and is then checked against
        the owner — the same tripwire as :meth:`_owned`, and it raises the
        identical ``NotFoundError`` so it is not an existence oracle.
        """
        if isinstance(depends_on, Task):
            if depends_on.owner_id != owner.id:
                raise NotFoundError(_TASK_NOT_FOUND)
            return depends_on
        task = await self.repository.get_by_id_for_user(depends_on, owner.id)
        if task is None:
            raise NotFoundError(_TASK_NOT_FOUND)
        return task

    async def _page(
        self,
        rows: Sequence[Task],
        *,
        total: int,
        limit: int,
        offset: int,
        owner: User,
    ) -> Page[TaskRead]:
        """Assemble a page of :class:`~app.schemas.task.TaskRead`.

        Two values on a task are not properties of the row and are filled in
        here: the tag ids, fetched for the whole page in **one** query through
        :meth:`~app.repositories.tag.TagRepository.list_tags_for_tasks` rather
        than one per row, and ``has_blocked_dependencies``.

        The blocked flag is the weaker of the two: it costs one dependency query
        per row, so a fifty-row page is fifty small indexed lookups. They are
        issued concurrently, but they share one request-scoped session and so do
        not truly run in parallel. It is still worth paying, because a board that
        reports a blocked card as ready is wrong rather than slow, and the fix —
        a bulk ``blocked_task_ids(task_ids)`` on the repository — is a
        repository change rather than a service one. Flagged for review.
        """
        task_ids = [row.id for row in rows]
        tags = await self.tag_repository.list_tags_for_tasks(task_ids)
        blocked = await asyncio.gather(
            *(self._has_open_dependencies(row.id, owner) for row in rows)
        )
        return Page[TaskRead](
            items=[
                TaskRead.build(
                    row,
                    tag_ids=[tag.id for tag in tags.get(row.id, [])],
                    has_blocked_dependencies=is_waiting,
                )
                for row, is_waiting in zip(rows, blocked, strict=True)
            ],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def _has_open_dependencies(self, task_id: uuid.UUID, owner: User) -> bool:
        """Report whether any prerequisite of one task is unfinished."""
        return any(
            _current_status(blocker) is not _SATISFIES_DEPENDENCY
            for blocker in await self.repository.list_dependencies(task_id)
            if blocker.owner_id == owner.id
        )

    async def _tag_ids(self, task: Task) -> list[uuid.UUID]:
        """Return one task's tag ids, in the repository's order."""
        tags = await self.tag_repository.list_tags_for_tasks([task.id])
        return [tag.id for tag in tags.get(task.id, [])]

    async def _require_tag(self, tag_id: uuid.UUID, owner: User) -> None:
        """Raise unless the tag exists *and* belongs to the caller."""
        if await self.tag_repository.get_by_id_for_user(tag_id, owner.id) is None:
            raise NotFoundError(_TAG_NOT_FOUND)

    async def _owned_tags(
        self, tag_ids: Sequence[uuid.UUID] | None, owner: User
    ) -> list[uuid.UUID]:
        """Resolve every tag id against the caller, or raise before anything is written."""
        wanted = list(dict.fromkeys(tag_ids or ()))
        for tag_id in wanted:
            await self._require_tag(tag_id, owner)
        return wanted

    def _owned(self, task: Task, owner: User) -> None:
        """Refuse a row that is not the caller's.

        A tripwire, not the authorisation. Every task reaching a mutating method
        was resolved through :meth:`get`, whose query is scoped by ``owner_id``,
        so the row was never loaded without the check. This exists for a caller
        that assembles a ``Task`` some other way, and it raises the same
        ``NotFoundError`` so it cannot tell a forbidden task from a missing one.
        """
        if task.owner_id != owner.id:
            raise NotFoundError(_TASK_NOT_FOUND)

    async def _record(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        task: Task | None = None,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Write one activity event, or do nothing when no feed is configured.

        Never raises: history is observability of the work, not a precondition
        for doing it. See :meth:`app.services.project_service.ProjectService._record`.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value,
            user_id=owner.id,
            project_id=project_id
            if project_id is not None
            else (task.project_id if task else None),
            task_id=task_id if task_id is not None else (task.id if task else None),
            metadata=metadata,
        )


def _check_parent(parent: Task, *, project_id: uuid.UUID, task: Task | None = None) -> None:
    """Enforce the three rules a subtask's parent has to satisfy.

    **Same project.** A subtask is a column on its parent's board; one filed
    under a different project would appear on neither board and would have no
    project that legitimately owns it.

    **The parent is a root card.** One level of nesting, see
    :data:`_MAX_SUBTASK_DEPTH`. A parent that already has a parent is refused by
    name so the caller knows the depth was the problem.

    **Not itself.** ``ck_tasks_parent_not_self`` already refuses this at the
    database level for every writer; this is the friendly version, and it also
    covers a ``parent_id`` the service is *moving* a task onto rather than
    creating it with.

    Args:
        parent: The proposed parent.
        project_id: The project the child will live in.
        task: The child, when it already exists and is being re-parented.
    """
    if task is not None and parent.id == task.id:
        raise ValidationError("A task cannot be its own parent.")
    if parent.project_id != project_id:
        raise ValidationError(
            "A subtask must belong to the same project as its parent.",
            details={"project_id": str(project_id), "parent_project_id": str(parent.project_id)},
        )
    if parent.parent_id is not None:
        raise ValidationError(
            "Subtasks cannot themselves have subtasks; nesting is limited to one level.",
            details={"max_depth": _MAX_SUBTASK_DEPTH},
        )


def _current_status(task: Task) -> TaskStatus:
    """Return the task's status as a member, or raise.

    A drifted row is raised rather than defaulted: substituting ``TODO`` would
    drop a corrupted card into the first column of the board looking like
    ordinary work, where nothing would ever notice.
    """
    try:
        return validate_task_status(task.status)
    except ValueError as exc:
        raise ValidationError(f"This task's status is not a known value: {task.status!r}.") from exc


def _transition_event(current: TaskStatus, target: TaskStatus) -> ActivityEvent:
    """Return the activity event that describes one legal transition.

    The four lifecycle edges get their own events because they are the moments a
    task history exists to remember; everything else is an update.
    """
    if target is TaskStatus.COMPLETED:
        return ActivityEvent.TASK_COMPLETED
    if current is TaskStatus.COMPLETED:
        return ActivityEvent.TASK_REOPENED
    if target is TaskStatus.BLOCKED:
        return ActivityEvent.TASK_BLOCKED
    if target is TaskStatus.IN_PROGRESS and current is TaskStatus.TODO:
        return ActivityEvent.TASK_STARTED
    return ActivityEvent.TASK_UPDATED


def _task_status_or_raise(value: str | TaskStatus) -> TaskStatus:
    """Coerce a status into a member, or raise :class:`ValidationError`."""
    try:
        return validate_task_status(value)
    except ValueError:
        raise ValidationError(f"Unknown status: {value!r}.") from None


def _task_status_or_none(value: str | None) -> str | None:
    """Validate a status filter, or return ``None`` for no filter."""
    if value is None:
        return None
    return _task_status_or_raise(value).value


def _task_priority_or_raise(value: str | TaskPriority) -> TaskPriority:
    """Coerce a priority into a member, or raise :class:`ValidationError`."""
    try:
        return validate_task_priority(value)
    except ValueError:
        raise ValidationError(f"Unknown priority: {value!r}.") from None


def _task_priority_or_none(value: str | None) -> str | None:
    """Validate a priority filter, or return ``None`` for no filter."""
    if value is None:
        return None
    return _task_priority_or_raise(value).value


def _check_window(*, limit: int, offset: int) -> None:
    """Reject a page window that is not one. See the project service."""
    if limit < 1:
        raise ValidationError("limit must be at least 1.")
    if offset < 0:
        raise ValidationError("offset must be zero or greater.")


def _check_sort(sort: str, order: str) -> str:
    """Validate the sort key and direction, returning the key.

    ``ORDER BY`` takes an expression rather than a bound parameter, so an
    unvalidated sort name would be SQL injection behind a query parameter.
    """
    if sort not in _SORT_KEYS:
        raise ValidationError(
            f"Cannot sort tasks by {sort!r}.", details={"allowed": sorted(_SORT_KEYS)}
        )
    if order not in _SORT_ORDERS:
        raise ValidationError(
            f"Cannot sort tasks {order!r}.", details={"allowed": sorted(_SORT_ORDERS)}
        )
    return sort
