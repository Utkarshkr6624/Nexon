"""Data access for :class:`~app.models.password_reset.PasswordResetToken`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate
it into the API contract.

The single-use guarantee is enforced here, in the query, rather than by the
service checking a flag after the fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset import PasswordResetToken

__all__ = ["PasswordResetRepository"]


class PasswordResetRepository:
    """Password reset token persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> PasswordResetToken:
        """Record an outstanding reset request and return it with defaults populated."""
        token = PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return token

    async def get_valid_by_token_hash(
        self, token_hash: str, *, now: datetime
    ) -> PasswordResetToken | None:
        """Return the token only if it is unused and unexpired at ``now``.

        Both conditions are part of the ``WHERE`` clause rather than a check
        applied to a fetched row. Reading the row first and deciding afterwards
        leaves a window in which two concurrent redemptions of the same link
        both see ``used_at IS NULL`` and both go on to change the password;
        filtering in the database means only one of them matches at all.

        ``now`` is supplied by the caller rather than read from the database
        because the service needs the same instant for the rest of the reset
        flow, and two different "now"s within one request are a bug waiting to
        happen.
        """
        result = await self.session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def mark_used(
        self, token: PasswordResetToken, *, used_at: datetime
    ) -> PasswordResetToken:
        """Spend the token, after which the same link can never be redeemed again.

        Single-use is really enforced by :meth:`get_valid_by_token_hash`, which
        stops matching a row the moment this writes ``used_at``; this method is
        the write that makes that true.
        """
        token.used_at = used_at
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return token

    async def invalidate_all_for_user(self, user_id: uuid.UUID, *, used_at: datetime) -> int:
        """Spend every outstanding reset token for a user and return the count.

        Used after a password change so that a reset link issued before it — and
        possibly already sitting in someone else's inbox — cannot set the new
        password afterwards.
        """
        result = await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=used_at)
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def purge_expired(self, *, before: datetime) -> int:
        """Delete tokens that expired before this instant, returning the count.

        The default ``password_reset_expire_minutes`` window is short, so without
        this the table would accumulate rows faster than any account makes
        resets. Rows are not deleted on use: an audit of a redeemed link is
        worth more than the few bytes it costs.
        """
        result = await self.session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.expires_at < before)
        )
        await self.session.commit()
        return int(result.rowcount or 0)
