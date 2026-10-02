"""Projects: the containers work is filed under.

A project is the thing a user navigates to. It carries its own status and
priority so a list of projects can be triaged without joining to its tasks, and
it is never deleted in Phase 3 — archiving (:attr:`Project.ARCHIVED` with
:attr:`Project.archived_at` set) is the terminal state, so a project keeps its
task history and its activity feed instead of vanishing under it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProjectPriority, ProjectStatus

__all__ = ["DEFAULT_PROJECT_PRIORITY", "DEFAULT_PROJECT_STATUS", "Project"]

#: What a new project gets when the caller does not choose.
DEFAULT_PROJECT_STATUS = ProjectStatus.PLANNED.value
DEFAULT_PROJECT_PRIORITY = ProjectPriority.MEDIUM.value

#: Sized for a project name a human types; anything longer is a description,
#: which is what :attr:`Project.description` is for.
_MAX_NAME_LENGTH = 200
#: Sized for the longest member of :class:`ProjectStatus` (``on_hold``) and
#: :class:`ProjectPriority` (``critical``), with room to spare.
_MAX_ENUM_LENGTH = 16


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's body of work."""

    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(_MAX_NAME_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Plain strings rather than native Postgres enums; see the module docstring
    # of :mod:`app.models.enums` for why, and `app.models.user.UserRole` for the
    # same decision taken once already. The server defaults make a bare INSERT
    # — a fixture, an import, a future service — land on a sensible state
    # instead of on NULL.
    status: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_PROJECT_STATUS,
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_PROJECT_PRIORITY,
        nullable=False,
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Stamped when the project reaches `completed`, and the column the
    # "delivered this month" report groups on. Kept separate from `updated_at`
    # because a project can be renamed for years afterwards and still be a
    # project that finished in March.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # The archive marker. Both `status = archived` and this timestamp describe
    # the same transition; the timestamp exists so "archived when" is answerable
    # without reading a history that may itself have been trimmed.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Index note: `ix_projects_owner_id` alone. The two real queries are "this
    # user's projects" and "this project's tasks, via tasks.project_id" — the
    # second does not touch this table's index at all. `status` and `priority`
    # have three and four distinct values across every project in the
    # installation, so a standalone index on either is one PostgreSQL will
    # never choose over a scan, while costing an entry on every insert.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Project id={self.id} name={self.name!r} status={self.status!r}>"

    @property
    def status_enum(self) -> ProjectStatus | None:
        """The project's status as a member, or ``None`` if the row drifted.

        Deliberately ``None`` rather than a fallback default. A drifted value
        must be visible to whoever reads it — a project silently reported as
        ``PLANNED`` when its row says something else is a lie the dashboard
        cannot detect. Validate on the way in with
        :func:`app.models.enums.validate_project_status` so this stays ``None``
        never.
        """
        try:
            return ProjectStatus(self.status)
        except ValueError:
            return None

    @property
    def priority_enum(self) -> ProjectPriority | None:
        """The project's priority as a member, or ``None`` if the row drifted."""
        try:
            return ProjectPriority(self.priority)
        except ValueError:
            return None
