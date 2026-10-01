"""Data access for :class:`~app.models.session.Session`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate
it into the API contract.

Two ideas run through the whole file. First, "live" is always decided by the
database (``revoked_at IS NULL AND expires_at > now()``), never by a value the
caller computed and passed in, so a caller with a skewed clock cannot make an
expired session look live. Second, a session row is mutated in place and never
replaced: rotation rewrites ``token_hash`` rather than inserting a new row, which
is what keeps "sign out everywhere" a single UPDATE.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session

__all__ = ["SessionRepository"]


class SessionRepository:
    """Session persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        user_agent: str | None,
        ip_address: str | None,
        expires_at: datetime,
    ) -> Session:
        """Insert a new device sign-in and return it with server defaults populated."""
        db_session = Session(
            user_id=user_id,
            token_hash=token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self.session.add(db_session)
        await self.session.commit()
        # created_at comes from a server default, so re-read it rather than
        # handing back an instance whose timestamp is still unset.
        await self.session.refresh(db_session)
        return db_session

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        """Return the session with this id, or ``None``."""
        result = await self.session.execute(select(Session).where(Session.id == session_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> Session | None:
        """Return the session only when it also belongs to this user.

        Ownership is part of the lookup rather than a check afterwards: a caller
        that resolved "session X" must not be able to act on it just because it
        knows the id.
        """
        result = await self.session.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session whose current token digest matches, or ``None``.

        A rotated-away token no longer matches anything, which is what makes
        replay detectable at this layer rather than by consulting a history.
        """
        result = await self.session.execute(select(Session).where(Session.token_hash == token_hash))
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, *, include_revoked: bool = False
    ) -> list[Session]:
        """List a user's sessions, newest first.

        Expired rows are kept in the listing: the sessions screen shows when a
        device signed out, and that is only readable from the row itself.
        """
        stmt = select(Session).where(Session.user_id == user_id)
        if not include_revoked:
            stmt = stmt.where(Session.revoked_at.is_(None))
        result = await self.session.execute(stmt.order_by(Session.created_at.desc()))
        return list(result.scalars().all())

    async def list_live_ordered_by_created(self, user_id: uuid.UUID) -> list[Session]:
        """List the user's live sessions, oldest first.

        Oldest-first is the whole point of this method: the service enforces
        ``max_active_sessions`` by walking this list and evicting from the front,
        so the order has to be the eviction order rather than a display order.
        """
        result = await self.session.execute(
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > func.now(),
            )
            .order_by(Session.created_at.asc())
        )
        return list(result.scalars().all())

    async def count_live_for_user(self, user_id: uuid.UUID) -> int:
        """Count the user's live sessions.

        Counted in SQL rather than as ``len(await self.list_live_ordered_by_created(...))``
        so the check costs the same whether the user has one session or fifty.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > func.now(),
            )
        )
        return int(result.scalar_one())

    async def touch(self, db_session: Session, *, last_used_at: datetime) -> Session:
        """Stamp the moment the session was last used."""
        db_session.last_used_at = last_used_at
        self.session.add(db_session)
        await self.session.commit()
        await self.session.refresh(db_session)
        return db_session

    async def rotate_token(
        self, db_session: Session, *, token_hash: str, expires_at: datetime
    ) -> Session:
        """Swap in a freshly minted token digest and its new expiry.

        The row keeps its identity across rotation, so the ``sid`` claim of the
        refresh token that produced this call still points at this same device.
        """
        db_session.token_hash = token_hash
        db_session.expires_at = expires_at
        self.session.add(db_session)
        await self.session.commit()
        await self.session.refresh(db_session)
        return db_session

    async def revoke(self, db_session: Session, *, revoked_at: datetime) -> Session:
        """Mark the session revoked, leaving an earlier revocation untouched.

        Idempotent on purpose. A session can be revoked by the caller, by an
        absolute-lifetime sweep, and by a password change; whoever arrives last
        must not rewrite "revoked three days ago" into "revoked now", because
        that is the moment the sessions screen reports to the user.
        """
        if db_session.revoked_at is not None:
            return db_session
        db_session.revoked_at = revoked_at
        self.session.add(db_session)
        await self.session.commit()
        await self.session.refresh(db_session)
        return db_session

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        revoked_at: datetime,
        except_id: uuid.UUID | None = None,
    ) -> int:
        """Revoke every live session for a user and return how many were touched.

        A single set-based UPDATE rather than a read-modify-write loop: "sign
        out everywhere" must not stop at a partially applied revocation just
        because the account happens to have many rows.

        ``except_id`` keeps the caller's own session alive when the caller
        intends to stay signed in. Already-revoked rows are excluded so the
        return value counts sessions this call actually ended.
        """
        stmt = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        if except_id is not None:
            stmt = stmt.where(Session.id != except_id)
        result = await self.session.execute(stmt.values(revoked_at=revoked_at))
        await self.session.commit()
        return int(result.rowcount or 0)

    async def purge_expired(self, *, before: datetime) -> int:
        """Delete sessions whose token expired before this instant, returning the count.

        The retention counterpart to the absolute-lifetime rule: a row outlives
        its token only so the UI can show it, and once that window has passed
        there is nothing left worth keeping.
        """
        result = await self.session.execute(delete(Session).where(Session.expires_at < before))
        await self.session.commit()
        return int(result.rowcount or 0)
