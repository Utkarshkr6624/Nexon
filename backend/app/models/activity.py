"""The activity feed: an append-only record of what happened to the work.

Separate from :class:`app.models.audit.AuditLog`, which records *security*
events. This table records what was done to a project or a task, and it is the
history behind every "when was this last touched" and "who completed this"
question the product asks.

Why the foreign keys are SET NULL and not CASCADE
-------------------------------------------------
Every other Phase 3 foreign key cascades: a project takes its tasks, a task takes
its dependencies, a user takes their projects. This table is the exception, and
deliberately so.

The work these rows describe is *deletable*. A user can delete a task that was
scratch work, and an archived project can be removed outright. If these
references cascaded, deleting one row would take its own history with it — the
activity feed would silently develop holes exactly where the record matters most,
and the first thing anyone would notice is that the feed is incomplete for no
reason a user can see.

With SET NULL the row survives and simply becomes unattributed: a feed entry
reading "task_completed" with a NULL ``task_id`` still says that *this user, in
this project, on this date, completed something* — which is most of the signal,
and vastly better than the alternative. The row is also explicitly nullable for
the same reason ``AuditLog.user_id`` is: an event may be attributable to a
project and a task but not to an account (an import, a system action), and the
absence of a link must mean "not attributable" rather than "no event".

The same choice, and the same trade, as ``AuditLog``: the trail outlives the
thing it describes. The event is the record; the row it points at is only the
address.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

__all__ = ["ActivityLog"]

#: Sized for the longest member of :class:`app.models.enums.ActivityEvent` with
#: room to spare — the same width ``audit_logs.event_type`` uses.
_MAX_EVENT_TYPE_LENGTH = 64


class ActivityLog(UUIDPrimaryKeyMixin, Base):
    """One recorded change to a project or a task.

    Immutable, exactly like :class:`app.models.audit.AuditLog`: rows are appended
    and never rewritten, so this model deliberately does **not** use
    :class:`~app.db.base.TimestampMixin`. That mixin's ``updated_at`` column and
    the ``onupdate`` rule behind it would both be lies for a row whose entire
    value is that it has not changed — and an activity feed is read far more
    often than it is written, so an ``onupdate`` that fired on an accidental
    edit would corrupt precisely the thing readers trust.
    """

    __tablename__ = "activity_events"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Nullable and SET NULL — see the module docstring. Every one of the three
    # references can legitimately be absent: an event can be about work nobody
    # claimed (an import), about no particular project (a global setting), or
    # about a task that has since been deleted.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(_MAX_EVENT_TYPE_LENGTH),
        index=True,
        nullable=False,
    )
    # This attribute is ``metadata_`` while the column is ``metadata``: the bare
    # name is reserved on the declarative class, where it already means "the
    # collection of mapped columns". Callers use the attribute; raw SQL and
    # Alembic see the column. The idiom is copied from
    # :class:`app.models.audit.AuditLog` so both tables read the same way.
    #
    # The column holds the *change*, not the row: for
    # ``TASK_PRIORITY_CHANGED`` the old and new values, for
    # ``TASK_DUE_DATE_CHANGED`` the old and new dates. It must only ever receive
    # non-sensitive, already-sanitised values — a task's description or a
    # project's notes are copied into feeds that are rendered in places the
    # original was never meant to reach, so anything private written here becomes
    # private in a second context.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        name="metadata",
        server_default="{}",
        nullable=False,
    )

    # Index notes:
    #
    # * `ix_activity_events_project_id` and `ix_activity_events_task_id` are the
    #   two the UI actually asks for: "the history of this project" and "the
    #   history of this task". Both are read newest-first and paginated, and both
    #   are served by scanning that project's or task's events and taking a
    #   top-N — the event count for one project is bounded by the work done in
    #   it, and a top-N heapsort over that is cheaper than maintaining a second
    #   index. So no `(project_id, created_at)` composite here, unlike the
    #   deliberately-added one on `tasks`: the difference is that a task backlog
    #   grows without bound and a dashboard query must *exclude* most of it,
    #   whereas this table is only ever read by narrowing to a project that the
    #   caller already knows.
    # * `ix_activity_events_event_type` mirrors `audit_logs.event_type` and is
    #   kept for the same reason: it is the only way to answer "show me every
    #   completion ever recorded" without a scan. It is the weakest of the four —
    #   fifteen distinct values is low selectivity — but this is an append-only
    #   table, so the maintenance cost is one index entry per insert and nothing
    #   else, and the query it serves (an admin/analytics sweep) is the one that
    #   would otherwise be a full scan of a table that only grows.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ActivityLog id={self.id} type={self.event_type!r}>"
