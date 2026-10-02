"""Data access for :class:`~app.models.password_reset.PasswordResetToken`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate
it into the API contract.

The single-use guarantee is enforced here, in the database, rather than by the
service checking a flag after the fact: the read filters on ``used_at IS NULL``
to keep the common path cheap, and the write (:meth:`PasswordResetRepository.spend`)
repeats the same condition so the check and the claim are one atomic step.
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
        applied to a fetched row, but that is a convenience, not the guarantee.
        This read runs under READ COMMITTED, where a plain ``SELECT`` does not
        wait behind another transaction's uncommitted write: two redemptions of
        the same link arriving at the same moment both see ``used_at IS NULL``
        and both walk away holding this row. It is :meth:`spend` that decides
        the winner, and a caller that is about to change a password must call
        it before it acts on what it read here.

        ``now`` is supplied by the caller rather than read from the database
        because the service needs the same instant for the rest of the reset
        flow, and two different "now"s within one request are a bug waiting to
        happen.

        Args:
            token_hash: The digest of the presented token.
            now: The instant the token must still be unexpired at.

        Returns:
            The matching token row, or ``None`` when the digest is unknown,
            already spent or expired.
        """
        result = await self.session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def spend(self, token_id: uuid.UUID, *, used_at: datetime) -> bool:
        """Claim the token, and report whether this caller is the one that got it.

        ``used_at IS NULL`` is repeated in the ``WHERE`` clause of the write, so
        the check and the claim are a single statement rather than a read
        followed by a decision. Under READ COMMITTED a losing redemption blocks
        on the row lock until the winner commits, then re-evaluates the
        predicate against the updated row, matches nothing, and is told so by
        its rowcount. A blind write by primary key cannot do that: it would
        overwrite ``used_at`` and report success to a second caller, which is
        exactly the window in which a stolen reset link could overwrite the
        password the legitimate owner had just set.

        The rowcount is read before the commit so the answer describes the
        statement rather than the connection state.

        Args:
            token_id: The row to claim.
            used_at: The instant the token is spent.

        Returns:
            ``True`` if this caller claimed the token; ``False`` if it was
            already spent, in which case the caller must abandon whatever it
            was about to do with it.
        """
        result = await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.id == token_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=used_at)
        )
        claimed = bool(result.rowcount)
        await self.session.commit()
        return claimed

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
