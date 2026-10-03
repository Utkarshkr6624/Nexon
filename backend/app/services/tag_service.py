"""Tag business logic: the rules of a per-user label.

A tag is a word a user applies to their own projects and tasks, and the only
thing genuinely hard about it is that **it belongs to exactly one account**.
:meth:`TagService.create` and :meth:`TagService.rename` treat a repeated name as
a conflict rather than quietly reusing the row, and every method that touches one
resolves it through a lookup scoped by ``user_id``. Routers translate the
exceptions raised here into HTTP responses — this module never imports FastAPI.

Where the rules are, and are not
--------------------------------
:class:`~app.repositories.tag.TagRepository` already owns the SQL and the two
association tables; what is left is the part SQL cannot express: *whose* tag this
is, whether a name is already taken, whether a whole requested set belongs to the
caller before any of it is written, and what the two usage counts on
:class:`~app.schemas.tag.TagRead` are. Those are below.

**Uniqueness is enforced twice, and both checks are needed.** The pre-check
through :meth:`~app.repositories.tag.TagRepository.get_by_name` answers the
common case — a user typing a word they already have — with a clean 409 and a
message that names the field. The ``IntegrityError`` guard around the commit is
what covers the window between the two: two tabs open on the same "new tag" form,
or a retry that lands after the first one committed. Without it the loser of that
race escapes as a driver error and becomes a 500. This is the same pair of
checks :meth:`app.services.auth_service.AuthService.register` runs for a duplicate
email, and for the same reason: the pre-check is an optimisation and an error
message, the constraint is the truth.

**Audit is not activity, and this module does not conflate them.**
:class:`~app.services.audit_service.AuditService` writes ``audit_logs`` and
answers *"who tried to do what to their account, and from where"*; Phase 6
analytics reads ``activity_events`` and must never read ``audit_logs``. Coining
or dropping a label is not a security event, so the ``audit`` parameter is
accepted for symmetry with the other work-management services and deliberately
unused, exactly as :class:`app.services.project_service.ProjectService` leaves
its own. What *is* recorded on the activity feed is set out under "the
vocabulary gap" below.

The vocabulary gap
------------------
:class:`~app.models.enums.ActivityEvent` has no member for a tag being coined,
renamed or deleted, so none of those three writes is recorded. Borrowing
``TASK_UPDATED`` or ``PROJECT_UPDATED`` for them would file a tag's creation
under a project or task the tag is not attached to, and put two different events
under one name, so the lifecycle writes nothing and this paragraph says so —
the same rule :meth:`app.services.task_service.TaskService.create` applies to a
refused cross-tenant create. Flagged for review: a Phase 3 vocabulary review
should add ``TAG_CREATED`` / ``TAG_RENAMED`` / ``TAG_DELETED``, and this module
should then call :meth:`TagService._record` from those three methods.

**Applying tags *is* recorded**, because it is a change to something that has an
event of its own. :meth:`set_task_tags` writes ``TASK_UPDATED`` on the task and
:meth:`set_project_tags` writes ``PROJECT_UPDATED`` on the project, carrying the
tag names in the metadata — the same shape
:meth:`app.services.task_service.TaskService.update` uses for a PATCH.

One asymmetry is worth naming rather than leaving for a reader to trip over.
``PUT /tasks/{id}/tags`` is served by
:meth:`app.services.task_service.TaskService.set_tags`, which does the identical
two steps and records nothing; the methods below are the same rule stated from
the tag side, for a caller that already holds the task or the project, and they
do record. Two services can reach one write, so exactly one of the two doors is
used per request. Flagged for review: routing ``PUT /tasks/{id}/tags`` through
:meth:`set_task_tags` instead would leave one door rather than two, and is a
one-line change in ``app/api/v1/tasks.py``.

Repository contract relied on by this module::

    TagRepository.create(*, user_id, name) -> Tag
    TagRepository.update_name(tag, name) -> Tag
    TagRepository.get_by_id_for_user(tag_id, user_id) -> Tag | None
    TagRepository.get_by_name(user_id, name) -> Tag | None
    TagRepository.list_for_user(user_id, *, limit, offset, search=None)
        -> tuple[list[Tag], int]
    TagRepository.delete(tag) -> None
    TagRepository.set_task_tags(task_id, tag_ids) -> None
    TagRepository.set_project_tags(project_id, tag_ids) -> None
    TagRepository.count_usage_by_kind(user_id) -> dict[UUID, tuple[int, int]]

Tenant isolation
----------------
**Every read is scoped by the caller's id in the query.**
:meth:`~app.repositories.tag.TagRepository.get_by_id_for_user` puts
``user_id`` in the ``WHERE`` clause, so a tag the caller may not see is never
loaded and "not yours" is indistinguishable from "does not exist" — the same rule
that keeps ``GET /tasks/{id}`` answering 404 rather than 403. The :meth:`_owned`
helper is a cheap second tripwire for the paths that take an already-loaded row,
and it raises the identical ``NotFoundError`` so it cannot become an existence
oracle of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import ActivityEvent
from app.models.project import Project
from app.models.tag import Tag
from app.models.task import Task
from app.models.user import User
from app.repositories.tag import TagRepository
from app.schemas.common import Page, PageMeta
from app.schemas.tag import TagCreate, TagRead

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.services.activity_service import ActivityService
    from app.services.audit_service import AuditService

__all__ = ["TagService"]

#: The default page size when a caller does not name one. Larger than the
#: project and task defaults because a tag list is a *control* the user scans for
#: one word out of, not a feed they page through: a person has tens of tags, not
#: thousands, and a sidebar whose labels are not all present is a sidebar they
#: cannot use as a palette.
DEFAULT_PAGE_SIZE = 100

_TAG_NOT_FOUND = "Tag not found."
_PROJECT_NOT_FOUND = "Project not found."
_TASK_NOT_FOUND = "Task not found."
_TAG_NAME_TAKEN = "You already have a tag with that name."


class TagService:
    """The rules of a per-user tag, and nothing else."""

    def __init__(
        self,
        repository: TagRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repository: Tag persistence for the request-scoped session.
            activity: Where domain events are written. Optional so the rules can
                be exercised without a history sink; see "the vocabulary gap" in
                the module docstring for what is and is not recorded through it.
            audit: Accepted for symmetry with the auth services and
                deliberately unused — see the module docstring for why coining a
                label is not a security event.
            settings: Application settings, resolved from the environment when
                not supplied.
        """
        self.repository = repository
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()

    # -- Creation ------------------------------------------------------------

    async def create(self, *, owner: User, data: TagCreate) -> Tag:
        """Create a tag owned by the caller.

        ``owner.id`` is taken from the caller's session and never from the
        payload. A create endpoint that let the body name its owner would let any
        authenticated user write into another account's palette.

        **The name is stripped, not folded.** ``tags`` carries
        ``UNIQUE (user_id, name)``, and the constraint compares bytes, so ``Bug``
        and ``bug`` are two tags a user may deliberately want — they are shown back
        to the person who coined them. Deciding to fold case is a decision about
        the tag input, and making it here as well as in the schema would be a
        second, invisible normalisation.

        A duplicate name is a :class:`ConflictError` — 409, not 500 — and is
        detected twice: once by the lookup, so the common case gets a clean
        message, and once by the ``IntegrityError`` guard, so losing the race
        against a concurrent create produces the same 409 instead of escaping as
        a driver error. See the module docstring for why both are here.

        Two users may both create a tag called ``urgent``. Uniqueness is scoped
        to the owner precisely so the same word is not a global namespace.

        Args:
            owner: The authenticated caller.
            data: The creation payload.

        Returns:
            The persisted tag.

        Raises:
            ConflictError: If this user already has a tag with that exact name.
        """
        name = data.name.strip()
        if await self.repository.get_by_name(owner.id, name) is not None:
            raise ConflictError(_TAG_NAME_TAKEN)
        try:
            return await self.repository.create(user_id=owner.id, name=name)
        except IntegrityError as exc:
            raise ConflictError(_TAG_NAME_TAKEN) from exc

    # -- Reads ---------------------------------------------------------------

    async def get(self, *, tag_id: uuid.UUID, owner: User) -> Tag:
        """Return one of the caller's tags, carrying its two usage counts.

        **The counts are filled in here, not left to the schema's defaults.**
        ``TagRead.task_count`` and ``project_count`` are not properties of the
        ``tags`` row — they are joins through ``task_tags`` and ``project_tags``
        — and :meth:`list` has always filled them from
        :meth:`~app.repositories.tag.TagRepository.count_usage_by_kind`. The
        single-tag read did not, so ``GET /tags/{id}`` answered ``0``/``0`` for
        a tag applied to one task and three projects while ``GET /tags``
        answered ``1``/``3`` **for the same row in the same request**. A
        confident zero is the worst of the two answers available here: the
        client cannot tell it from "this tag is unused", so a palette entry
        reads as dead and gets deleted.

        The counts ride on the returned instance rather than being handed back
        as a separate object because the router serves this method's return
        value directly through ``response_model=TagRead`` and also passes it to
        :meth:`rename` and :meth:`delete`, which want the ORM row. Setting
        ``task_count``/``project_count`` on the instance satisfies the first and
        is invisible to the second: they are not mapped columns, so nothing is
        ever flushed back, and every later reader of the tag — service or
        response model — sees the same numbers the listing reports.

        The counts are the user's own and only the user's own, so a tag's reach
        can never reveal that somebody else applied it.

        Args:
            tag_id: The tag to fetch.
            owner: The authenticated caller.

        Returns:
            The tag, with ``task_count`` and ``project_count`` set to the
            caller's measured uses of it.

        Raises:
            NotFoundError: If the caller owns no tag with this id. Another
                user's tag and a nonexistent one are the same answer, on purpose:
                a different error would turn this endpoint into a probe for which
                tag ids are real.
        """
        tag = await self.repository.get_by_id_for_user(tag_id, owner.id)
        if tag is None:
            raise NotFoundError(_TAG_NOT_FOUND)
        counts = await self.repository.count_usage_by_kind(owner.id)
        task_count, project_count = counts.get(tag.id, (0, 0))
        tag.task_count = task_count
        tag.project_count = project_count
        return tag

    async def list(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
    ) -> Page[TagRead]:
        """List the caller's tags as a page, each carrying its two usage counts.

        ``task_count`` and ``project_count`` are not properties of the ``tags``
        row — they are joins through ``task_tags`` and ``project_tags`` — so
        :meth:`~app.repositories.tag.TagRepository.count_usage_by_kind` fills
        them for **the whole page in one query**, and a tag absent from that
        result carries zero on both. Zero is a fact here rather than a missing
        lookup, which is why the aggregate fills rather than leaving
        :class:`~app.schemas.tag.TagRead`'s defaults: those defaults exist for a
        tag read *without* the join, and returning them here would report every
        tagged task and project as carrying no uses at all.

        The counts are the user's own and only the user's own. Each side of the
        aggregate is scoped to ``owner.id``, so a tag's reach can never reveal
        that somebody else used it.

        Args:
            owner: The authenticated caller. The scope is the caller's own.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            search: Case-insensitive substring over the name.

        Returns:
            The page of tags and its metadata.

        Raises:
            ValidationError: If the page window is not one.
        """
        _check_window(limit=limit, offset=offset)
        rows, total = await self.repository.list_for_user(
            owner.id, limit=limit, offset=offset, search=search
        )
        counts = await self.repository.count_usage_by_kind(owner.id)
        return Page[TagRead](
            items=[_read(row, counts.get(row.id, (0, 0))) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    # -- Detail edits --------------------------------------------------------

    async def rename(self, *, tag: Tag, name: str, owner: User) -> Tag:
        """Rename one of the caller's tags, refusing a name they already hold.

        A tag's identity is its name as far as the user is concerned, so a rename
        is the moment a word that was free becomes taken — the same conflict
        :meth:`create` answers with, enforced the same twice. Stripping mirrors
        creation, so the value stored is the value the collision check compared
        against.

        Renaming a tag to the name it already holds is a no-op rather than a 409.
        It would collide with *itself*, and answering a retried rename with a
        conflict would make the idempotent path the one that fails. This is the
        same short-circuit :meth:`app.services.project_service.ProjectService.set_status`
        applies to a transition to the current status.

        Args:
            tag: The tag, already resolved for this owner.
            name: The new name. Stripped, not case-folded.
            owner: The authenticated caller.

        Returns:
            The renamed tag, or the unchanged one when it already held the name.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ConflictError: If this user already has a *different* tag with that
                exact name.
            ValidationError: If the new name is blank.
        """
        self._owned(tag, owner)
        stripped = name.strip()
        if not stripped:
            raise ValidationError("Tag name must not be blank.")
        if stripped == tag.name:
            return tag
        existing = await self.repository.get_by_name(owner.id, stripped)
        if existing is not None and existing.id != tag.id:
            raise ConflictError(_TAG_NAME_TAKEN)
        try:
            return await self.repository.update_name(tag, stripped)
        except IntegrityError as exc:
            raise ConflictError(_TAG_NAME_TAKEN) from exc

    async def delete(self, *, tag: Tag, owner: User) -> None:
        """Delete one of the caller's tags and every edge pointing at it.

        **The two association rows cascade with it**, which is the point: a tag
        nobody can name any more is an edge that would otherwise sit in every
        tagged task and project forever with nothing to display it from. The
        reverse is not true — deleting a *task* takes its tag edges and leaves the
        tag itself alone.

        Args:
            tag: The tag, already resolved for this owner.
            owner: The authenticated caller.

        Raises:
            NotFoundError: If the row does not belong to the caller.
        """
        self._owned(tag, owner)
        await self.repository.delete(tag)

    # -- Application ---------------------------------------------------------

    async def set_task_tags(
        self, *, task: Task, tag_ids: Sequence[uuid.UUID], owner: User
    ) -> list[uuid.UUID]:
        """Replace a task's tags with exactly this set.

        **Every id is resolved against the caller before anything is written.**
        That ordering is the whole point of the method: the repository's
        replace-the-whole-set is one transaction, so a caller naming one of
        another account's tags alongside their own would otherwise have the good
        ids applied and the bad one rejected — a half-applied write the client
        cannot detect, on a request that named a tag id it guessed. Resolving
        first turns the refusal into one that changes nothing at all, and into a
        :class:`NotFoundError`, which is the answer a tag id that is not yours
        gets whether or not it exists.

        An empty sequence clears the task's tags. That is the meaning of "set
        these tags" and not a no-op.

        :meth:`app.services.task_service.TaskService.set_tags` performs the same
        two steps on behalf of ``PUT /tasks/{id}/tags``, which is the route a
        client reaches; this is the same rule stated from the tag side, for a
        caller that already holds the task.

        Args:
            task: The task, already resolved for this owner.
            tag_ids: The tags the task should end up carrying.
            owner: The authenticated caller.

        Returns:
            The task's tag ids afterwards, deduplicated and order-preserving.

        Raises:
            NotFoundError: If the task is not the caller's, or any tag is not.
        """
        if task.owner_id != owner.id:
            raise NotFoundError(_TASK_NOT_FOUND)
        wanted = await self._owned_tags(tag_ids, owner)
        await self.repository.set_task_tags(task.id, list(wanted))
        await self._record(
            ActivityEvent.TASK_UPDATED,
            owner=owner,
            task_id=task.id,
            metadata={"tags": [str(tag_id) for tag_id in wanted]},
        )
        return list(wanted)

    async def set_project_tags(
        self, *, project: Project, tag_ids: Sequence[uuid.UUID], owner: User
    ) -> list[uuid.UUID]:
        """Replace a project's tags with exactly this set.

        See :meth:`set_task_tags` — same ordering, same reason, same answers. The
        project side is verified against the caller rather than assumed because
        ``ProjectService`` resolves the project through a scoped lookup, but a
        caller assembling a ``Project`` some other way should not be able to
        write an edge against somebody else's row.

        Args:
            project: The project, already resolved for this owner.
            tag_ids: The tags the project should end up carrying.
            owner: The authenticated caller.

        Returns:
            The project's tag ids afterwards, deduplicated and order-preserving.

        Raises:
            NotFoundError: If the project is not the caller's, or any tag is not.
        """
        if project.owner_id != owner.id:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        wanted = await self._owned_tags(tag_ids, owner)
        await self.repository.set_project_tags(project.id, list(wanted))
        await self._record(
            ActivityEvent.PROJECT_UPDATED,
            owner=owner,
            project_id=project.id,
            metadata={"tags": [str(tag_id) for tag_id in wanted]},
        )
        return list(wanted)

    # -- Internals -----------------------------------------------------------

    async def _owned_tags(self, tag_ids: Sequence[uuid.UUID], owner: User) -> list[uuid.UUID]:
        """Resolve every tag id against the caller, or raise before anything is written.

        Deduplicated on the way in, order-preserving: the association tables are
        keyed on the ``(object, tag)`` pair, so a repeat would otherwise reach the
        database as a duplicate-key violation, and a client assembling the list
        from two filter chips can legitimately produce one.
        """
        wanted = list(dict.fromkeys(tag_ids))
        for tag_id in wanted:
            if await self.repository.get_by_id_for_user(tag_id, owner.id) is None:
                raise NotFoundError(_TAG_NOT_FOUND)
        return wanted

    def _owned(self, tag: Tag, owner: User) -> None:
        """Refuse a tag that is not the caller's.

        A tripwire, not the authorisation. Every tag reaching a mutating method
        was resolved through :meth:`get`, whose query is scoped by ``user_id``, so
        the row was never loaded without the check. This exists for a caller that
        assembles a ``Tag`` some other way, and it raises the same
        ``NotFoundError`` so it cannot tell a forbidden tag from a missing one.
        """
        if tag.user_id != owner.id:
            raise NotFoundError(_TAG_NOT_FOUND)

    async def _record(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Write one activity event, or do nothing when no feed is configured.

        Never raises: history is observability of the work, not a precondition
        for doing it. See
        :meth:`app.services.project_service.ProjectService._record`; the reason
        this exists at all is the vocabulary gap in the module docstring.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value,
            user_id=owner.id,
            project_id=project_id,
            task_id=task_id,
            metadata=metadata,
        )


def _read(row: Tag, counts: tuple[int, int]) -> TagRead:
    """Build a :class:`~app.schemas.tag.TagRead` with its two usage counts filled in.

    ``counts`` is the pair from
    :meth:`~app.repositories.tag.TagRepository.count_usage_by_kind`, or ``(0, 0)``
    for a tag used on nothing — which is a fact, not a missing lookup, and is why
    the default is applied here rather than being left to the schema.
    """
    return TagRead.model_validate(row).model_copy(
        update={"task_count": counts[0], "project_count": counts[1]}
    )


def _check_window(*, limit: int, offset: int) -> None:
    """Reject a page window that is not one. See the project service.

    ``PageMeta`` declares ``limit`` as at least one and ``offset`` as at least
    zero, so a window outside that range cannot be serialised into the envelope
    at all. Catching it here turns the failure into the 422 the caller deserves
    instead of a response-model error after the query has already run.
    """
    if limit < 1:
        raise ValidationError("limit must be at least 1.")
    if offset < 0:
        raise ValidationError("offset must be zero or greater.")
