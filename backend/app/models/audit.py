"""Audit trail for security-relevant events.

Unlike every other table, an audit row is immutable: it is written once and
never updated. That is why this model deliberately does *not* use
:class:`~app.db.base.TimestampMixin` — the mixin's ``updated_at`` column, and
the ``onupdate`` rule behind it, would both be lies for a row whose whole value
is that it has not changed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

__all__ = ["AuditEvent", "AuditLog", "validate_audit_event"]

#: Maximum textual length of an IPv6 address, plus slack.
_MAX_IP_LENGTH = 45
_MAX_USER_AGENT_LENGTH = 512
_MAX_EVENT_TYPE_LENGTH = 64


class AuditEvent(StrEnum):
    """The security-relevant events worth keeping a record of.

    A :class:`~enum.StrEnum` so each member's value is the stable string that is
    actually persisted. Consumers filter on these values, so the spelling is
    part of the contract: they are lowercase snake_case and never renamed once
    rows exist.
    """

    USER_REGISTERED = "user_registered"
    USER_LOGIN = "user_login"
    USER_LOGIN_FAILED = "user_login_failed"
    USER_LOGOUT = "user_logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    SESSION_CREATED = "session_created"
    SESSION_REVOKED = "session_revoked"
    SESSIONS_REVOKED_ALL = "sessions_revoked_all"
    ACCOUNT_UPDATED = "account_updated"
    ACCOUNT_DELETED = "account_deleted"


def validate_audit_event(value: AuditEvent | str) -> AuditEvent:
    """Coerce a stored or user-supplied value into an :class:`AuditEvent`.

    Raises:
        ValueError: If the value is not a known event. A mistyped event name
            would otherwise be written as an unmatchable string, and a
            ``user_loginn`` row is invisible to every filter that reports on
            sign-ins — the failure mode that matters most here is a silently
            missing event, not a loud one.
    """
    if isinstance(value, AuditEvent):
        return value
    try:
        return AuditEvent(value)
    except ValueError:
        raise ValueError(f"Unknown audit event: {value!r}") from None


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """One recorded security event.

    Rows are append-only and have no ``updated_at``: the mixin's ``onupdate`` hook
    would rewrite the timestamp of the one row that must not change. A write-only
    guarantee is also a retention guarantee — purging old audit data is a
    deliberate, explicit operation rather than a side effect of editing a row.
    """

    __tablename__ = "audit_logs"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Nullable on purpose. A failed sign-in against an address with no account is
    # exactly the event worth recording, and it has no user row to point at; a
    # NOT NULL column would force that signal to be dropped or faked. The
    # absence of a row here means "not attributable to an account", not "no
    # account was involved". The cascade is SET NULL rather than CASCADE for the
    # same reason: deleting an account must not take its own audit trail with it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(_MAX_EVENT_TYPE_LENGTH), index=True, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(_MAX_IP_LENGTH), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(_MAX_USER_AGENT_LENGTH), nullable=True)
    # This attribute is ``metadata_`` while the column is ``metadata``: the bare
    # name is reserved on the declarative class, where it already means "the
    # collection of mapped columns". Callers use the attribute; raw SQL and
    # Alembic see the column.
    #
    # This column must only ever receive non-sensitive, already-sanitised values
    # — never a password, a token, or a hash of either. It is written verbatim,
    # rendered into log exports and retained for longer than the sessions it
    # describes, so anything secret that lands here is a secret that cannot be
    # un-leaked.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        name="metadata",
        server_default="{}",
        nullable=False,
    )
