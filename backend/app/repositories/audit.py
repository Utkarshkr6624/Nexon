"""Data access for :class:`~app.models.audit.AuditLog`.

The repository owns SQL only. It never raises domain errors.

Audit rows are append-only, so there is no ``update_fields`` here by design: the
only way a row's contents can change is if the repository offers a way to change
them, and it should not.

.. warning::
   ``metadata`` must never receive a password, a token, or a hash of either. The
   value is written verbatim into JSONB, is included in log exports, and is
   retained for ``audit_log_retention_days`` — far longer than the sessions and
   reset links it describes. Nothing is filtered on the way in, because a
   redaction list would eventually miss the one field that matters; sanitising is
   the caller's job, at the point where the value is still known to be safe.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

__all__ = ["AuditRepository"]


class AuditRepository:
    """Audit trail persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID | None,
        event_type: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditLog:
        """Append one event to the trail.

        ``user_id`` is nullable because the most valuable event to record is
        often the one with no account behind it — a failed sign-in against an
        address that was never registered. Recording it as a row with no
        principal is strictly better than dropping it or attributing it to a
        real user.
        """
        entry = AuditLog(
            user_id=user_id,
            event_type=event_type,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_=dict(metadata) if metadata else {},
        )
        self.session.add(entry)
        await self.session.commit()
        # created_at is a server default; re-read so the caller can sort on it
        # without a second query.
        await self.session.refresh(entry)
        return entry

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[AuditLog]:
        """List a user's audit trail, newest first, optionally ending before ``before``.

        ``before`` gives the caller keyset pagination without an offset: an audit
        trail grows without bound, and ``OFFSET`` degrades on exactly the table
        that is largest.
        """
        stmt = select(AuditLog).where(AuditLog.user_id == user_id)
        if before is not None:
            stmt = stmt.where(AuditLog.created_at < before)
        result = await self.session.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def list_recent(self, *, limit: int = 50) -> list[AuditLog]:
        """List the newest events across all accounts, for an admin view.

        Not scoped to a user on purpose: this is the surface where a successful
        sign-in against an address nobody owns becomes visible.
        """
        result = await self.session.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def purge_older_than(self, *, before: datetime) -> int:
        """Delete trail entries recorded before this instant, returning the count.

        Retention is enforced by deleting whole rows rather than by trimming
        fields: an audit row that has lost its details is no longer evidence of
        anything, and a half-deleted row invites the reader to over-trust it.
        """
        result = await self.session.execute(delete(AuditLog).where(AuditLog.created_at < before))
        await self.session.commit()
        return int(result.rowcount or 0)
