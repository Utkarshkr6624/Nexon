"""Phase 3 activity history: recording domain events, and reading them back.

:class:`~app.repositories.project.ProjectRepository` and
:class:`~app.repositories.task.TaskRepository` own SQL; this module owns the two
rules the routers and the other services need and cannot get from a repository.

**Recording is best-effort, and must stay that way.** :meth:`ActivityService.record`
never raises. History is observability of the work, not a precondition for doing
it: a user completing a task does not get a 500 because the feed was unavailable,
and a user who cannot record work still has to be able to do it. The repository's
:meth:`~app.repositories.activity.ActivityRepository.record` commits on the
request-scoped session every other repository in the request also holds, so a
failed write leaves that session needing a ``rollback()`` before anyone else can
use it — restoring it here, rather than letting the exception escape, is what makes
the "never raises" promise true for the *rest* of the request and not only for the
call that made it.

**Reading is scoped by the caller.** :meth:`feed` delegates to
:meth:`~app.repositories.activity.ActivityRepository.list_for_user`, whose
``user_id`` predicate is in the ``WHERE`` clause rather than applied afterwards, so
knowing an event id buys nothing. :meth:`project_feed` narrows to a project whose
ownership the caller has already resolved; it still filters by the owner, because
the free cost of scoping a lookup by the subject it belongs to is worth more than
the single round trip it saves.

**This is not the audit trail.** :class:`~app.services.audit_service.AuditService`
writes ``audit_logs`` and answers "was this account accessed suspiciously?";
this writes ``activity_events`` and answers "what happened to my work?". Phase 6
analytics reads this table and must not read the other one.

Repository contract relied on by this module::

    ActivityRepository.record(*, user_id, event_type, project_id=None,
                              task_id=None, metadata=None) -> ActivityLog
    ActivityRepository.list_for_user(user_id, *, limit, offset, project_id=None,
                                     task_id=None, event_type=None)
        -> tuple[list[ActivityLog], int]
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent, validate_activity_event
from app.models.project import Project
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.schemas.activity import ActivityEventRead, ActivityFeed
from app.schemas.common import PageMeta

__all__ = ["ActivityService"]

logger = get_logger("app.services.activity")

#: The default page size when a caller does not name one.
DEFAULT_PAGE_SIZE = 50

_PROJECT_NOT_FOUND = "Project not found."


class ActivityService:
    """The rules of the activity feed: append, page, and never block the work."""

    def __init__(self, repository: ActivityRepository) -> None:
        """Wire the service.

        Args:
            repository: Activity persistence for the request-scoped session.
        """
        self.repository = repository

    # -- Writing -------------------------------------------------------------

    async def record(
        self,
        event: str,
        *,
        user_id: uuid.UUID | None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ActivityLog | None:
        """Append one event, swallowing any failure.

        **Never raises.** Three things can go wrong and none of them may reach the
        caller:

        * **An unknown event name.** ``activity_events.event_type`` is a plain
          ``String`` filtered on by consumers, so a typo is storable and produces
          a row no filter can ever find — the feed silently develops a hole where
          an event should be. The name is validated here and a rejected one is
          logged rather than written.
        * **The commit failing.** The repository commits on the request-scoped
          session, so a failure leaves that session in a failed transaction. The
          ``rollback()`` below is what stops one broken history entry from turning
          every later write in the same request into an error.
        * **The insert failing** for any other reason. Same reasoning.

        ``metadata`` is written verbatim, and the caller is responsible for it:
        it must never receive a password, a token, or a hash of either. Nothing is
        filtered on the way in because a redaction list would eventually miss the
        one field that matters.

        Args:
            event: One of the :class:`~app.models.enums.ActivityEvent` values.
            user_id: The actor, or ``None`` for work with no signed-in principal
                behind it (an import, a sweep).
            project_id: The project, or ``None`` when the event is not about one.
            task_id: The task, or ``None`` when the event is not about one.
            metadata: Non-sensitive context for the event.

        Returns:
            The persisted event, or ``None`` when nothing was written.
        """
        try:
            validate_activity_event(event)
        except ValueError:
            logger.error(
                "activity_event_rejected",
                extra={"event_type": str(event), "user_id": str(user_id)},
            )
            return None
        try:
            return await self.repository.record(
                user_id=user_id,
                event_type=event,
                project_id=project_id,
                task_id=task_id,
                metadata=metadata,
            )
        except Exception:
            # ``rollback()`` first: the session is shared with every other
            # repository in this request, and leaving it in a failed transaction
            # would make the *next* write fail rather than this one.
            await self._rollback()
            logger.exception(
                "activity_record_failed",
                extra={
                    "event_type": str(event),
                    "user_id": str(user_id),
                    "project_id": str(project_id),
                },
            )
            return None

    # -- Reading -------------------------------------------------------------

    async def feed(
        self,
        *,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        event_type: str | None = None,
    ) -> ActivityFeed:
        """Return the caller's cross-project history, newest first, as a page.

        The scope is the caller's own: ``owner.id`` goes into the query, so an
        event belonging to another account is never loaded and the endpoint cannot
        be used to discover that it exists.

        Every filter ANDs onto the same statement, so the ``total`` in the metadata
        is the size of the set the page was drawn from rather than the size of the
        slice.

        Args:
            owner: The authenticated caller. The scope is their own history.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            project_id: Restrict to one project.
            task_id: Restrict to one task.
            event_type: Restrict to one event name. Validated rather than passed
                through, so a typo is a 422 naming the field instead of a filter
                that silently matches nothing.

        Returns:
            The page of events and its metadata.

        Raises:
            ValidationError: If the page window is not one, or ``event_type`` is
                not a known :class:`~app.models.enums.ActivityEvent`.
        """
        _check_window(limit=limit, offset=offset)
        event_value = _event_type_or_none(event_type)
        rows, total = await self.repository.list_for_user(
            owner.id,
            limit=limit,
            offset=offset,
            project_id=project_id,
            task_id=task_id,
            event_type=event_value,
        )
        return ActivityFeed(
            items=[ActivityEventRead.from_row(row) for row in rows],
            meta=PageMeta(total=total, limit=limit, offset=offset),
        )

    async def project_feed(
        self,
        *,
        project: Project,
        owner: User,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        event_type: str | None = None,
    ) -> ActivityFeed:
        """Return the history of one project the caller owns, as a page.

        **The project is re-checked against the caller here** even though the
        router reached this from a project it had already resolved: a tripwire is
        free, and it raises the identical ``NotFoundError`` so it cannot become an
        existence oracle of its own.

        **This reads through :meth:`feed` rather than through the repository's
        dedicated ``recent_for_project``.** That method is the right shape for a
        "last N things" strip — it takes no ``user_id`` because the caller arrived
        from a project it had already authorised — but it reports no ``total`` and
        takes no ``offset``, so a ``Page`` built from it would have to claim a
        total it did not have. Paging the caller's own feed narrowed by
        ``project_id`` answers the same question with a real total, and on this
        data model (a project has exactly one owner) it returns the same rows.

        Args:
            project: The project, already resolved for this owner.
            owner: The authenticated caller.
            limit: Maximum rows in the page.
            offset: Rows to skip.
            event_type: Restrict to one event name.

        Returns:
            The page of events and its metadata.

        Raises:
            NotFoundError: If the row does not belong to the caller.
            ValidationError: If the page window is not one, or ``event_type`` is
                not a known event.
        """
        if project.owner_id != owner.id:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return await self.feed(
            owner=owner,
            limit=limit,
            offset=offset,
            project_id=project.id,
            event_type=event_type,
        )

    # -- Internals -----------------------------------------------------------

    async def _rollback(self) -> None:
        """Restore the shared session after a failed history write.

        Best-effort in both directions: a rollback that itself fails cannot be
        reported to the caller (there is no caller to report to — that is the
        whole point) and must not become the error that escapes
        :meth:`record`, so it is logged and swallowed here.
        """
        try:
            await self.repository.session.rollback()
        except Exception:
            logger.exception("activity_rollback_failed")


def _event_type_or_none(value: str | None) -> str | None:
    """Validate an event-type filter, or return ``None`` for no filter."""
    if value is None:
        return None
    try:
        return validate_activity_event(value).value
    except ValueError:
        raise ValidationError(
            f"Unknown event_type: {value!r}.",
            details={"allowed": sorted(event.value for event in ActivityEvent)},
        ) from None


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
