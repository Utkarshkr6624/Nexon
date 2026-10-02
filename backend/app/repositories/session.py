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

The consequence of the second idea is that a rotation has to be *guarded*, and
it is guarded in SQL rather than in the caller: a row that holds exactly one
current token is also a row two concurrent requests can both believe they are
holding, so the expectation is part of the ``WHERE`` clause
(:meth:`SessionRepository.rotate_token_if_current`) instead of a SELECT that
precedes the write.
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

    async def list_for_user(
        self, user_id: uuid.UUID, *, include_inactive: bool = False
    ) -> list[Session]:
        """List a user's sessions, newest first.

        **What this returns is live sessions only** — ``revoked_at IS NULL AND
        expires_at > now()``, decided by the database. An expired row is not a
        device that is signed in, and the sessions screen has no field that can
        tell the two apart, so returning one would show the user a laptop they
        signed out of months ago as a live session. The row itself is still kept
        for audit purposes; it just is not part of what "your sessions" means.

        ``include_inactive`` exists for the callers that want the history rather
        than the live set, and is off by default. It is not a second product
        decision: exposing rows the UI cannot classify is a change that belongs
        to whoever adds an ``expired`` marker to the response schema.

        Args:
            user_id: Whose sessions to list. The scope is always one account.
            include_inactive: Return revoked and expired rows too, instead of
                only the live ones.

        Returns:
            The matching rows, newest first.
        """
        stmt = select(Session).where(Session.user_id == user_id)
        if not include_inactive:
            stmt = stmt.where(
                Session.revoked_at.is_(None),
                Session.expires_at > func.now(),
            )
        result = await self.session.execute(stmt.order_by(Session.created_at.desc()))
        return list(result.scalars().all())

    async def list_live_ordered_by_created(self, user_id: uuid.UUID) -> list[Session]:
        """List the user's live sessions, oldest first.

        Oldest-first is the whole point of this method: the service enforces
        ``max_active_sessions`` by walking this list and evicting from the front,
        so the order has to be the eviction order rather than a display order.

        ``id`` breaks ties because ``created_at`` is a one-second
        ``server_default``. Without it, two sign-ins in the same second can come
        back in either order, and "evicting the oldest" would quietly mean
        "evicting whichever of the two the planner happened to return first" —
        which, on a replay attack, is the difference between the attacker's row
        and the owner's being the one that survives.
        """
        result = await self.session.execute(
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > func.now(),
            )
            .order_by(Session.created_at.asc(), Session.id.asc())
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
        """Write a freshly minted token digest and its expiry, unconditionally.

        The row keeps its identity across rotation, so the ``sid`` claim of the
        refresh token that produced this call still points at this same device.

        This is a first-write, not a rotation: it is how :meth:`SessionRepository.create`'s
        placeholder digest is replaced with the real one during sign-in, by the
        process that inserted the row microseconds earlier and is therefore the
        only writer. It carries no "is the caller's token still current?" check,
        so it must never be reached by a request that is *spending* a presented
        token — that path is :meth:`rotate_token_if_current`, where an unguarded
        write is a replay waiting to happen.
        """
        db_session.token_hash = token_hash
        db_session.expires_at = expires_at
        self.session.add(db_session)
        await self.session.commit()
        await self.session.refresh(db_session)
        return db_session

    async def rotate_token_if_current(
        self,
        db_session: Session,
        *,
        current_token_hash: str,
        token_hash: str,
        expires_at: datetime,
    ) -> bool:
        """Swap the digest in, but only while the row still holds the one presented.

        The comparison lives in the ``WHERE`` clause rather than in a preceding
        read, and that placement is the whole point. A read followed by an
        unconditional write is check-then-act: under ``READ COMMITTED`` the
        second client's SELECT still sees the *old* digest, because the first
        client's UPDATE is uncommitted and therefore invisible to it, so both
        pass the check and both write. The last writer wins and both freshly
        minted pairs stay live — one refresh token, two usable sessions. Folding
        the expectation into the UPDATE predicate makes the loser's statement
        match zero rows, which is a conflict the database itself detects, with no
        lock ordering to reason about and nothing for a caller to forget.

        ``revoked_at IS NULL AND expires_at > now()`` rides along for the same
        reason: "current" and "live" are decided together, so a session that was
        revoked or that expired in the last few milliseconds cannot be rotated
        by a check that read it a moment earlier. The instant is the database's
        clock, per this module's rule.

        Args:
            db_session: The row being rotated. Only its ``id`` is trusted; every
                other condition is re-evaluated by the database.
            current_token_hash: The digest the caller presented, i.e. the one
                whose currency earned this rotation.
            token_hash: The digest to install.
            expires_at: The new expiry, already clamped by the caller.

        Returns:
            ``True`` if the row was rotated. ``False`` if it no longer holds
            ``current_token_hash``, or is no longer live — a replayed token, a
            concurrent rotation, or a revocation that landed first. The
            distinction is deliberately not available to the caller.
        """
        stmt = (
            update(Session)
            .where(
                Session.id == db_session.id,
                Session.token_hash == current_token_hash,
                Session.revoked_at.is_(None),
                Session.expires_at > func.now(),
            )
            .values(token_hash=token_hash, expires_at=expires_at)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        if not result.rowcount:
            return False
        # The instance still holds the values the UPDATE replaced.
        await self.session.refresh(db_session)
        return True

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
