"""Refresh-token sessions: the rows behind every device sign-in.

One row is one device. Rotation rewrites that row rather than adding another,
which is what keeps "sign out everywhere" a single UPDATE and what lets the
sessions screen show a device as "signed out on ..." rather than as gone. The
model docstring on :class:`app.models.session.Session` states the rule; this
module is where it is enforced.

The repository owns every query. The surface relied on here is::

    SessionRepository.create(*, user_id, token_hash, user_agent, ip_address,
                             expires_at) -> Session
    SessionRepository.get_by_id(session_id) -> Session | None
    SessionRepository.get_by_id_for_user(session_id, user_id) -> Session | None
    SessionRepository.get_by_token_hash(token_hash) -> Session | None
    SessionRepository.list_for_user(user_id, *, include_revoked=False) -> list[Session]
    SessionRepository.list_live_ordered_by_created(user_id) -> list[Session]
    SessionRepository.touch(db_session, *, last_used_at) -> Session
    SessionRepository.rotate_token(db_session, *, token_hash, expires_at) -> Session
    SessionRepository.revoke(db_session, *, revoked_at) -> Session
    SessionRepository.revoke_all_for_user(user_id, *, revoked_at, except_id=None) -> int

The account itself is read through :class:`~app.repositories.user.UserRepository`,
built from the same request-scoped :class:`~sqlalchemy.ext.asyncio.AsyncSession`
the session repository holds, so a rotation checks the account and the row on a
single connection instead of opening a second one.

The two request-derived values written here — the client address and the user
agent — are clamped by :mod:`app.services.audit_service`, which is where the API
layer is told to read them from and which therefore owns the one bound those two
columns impose.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    token_fingerprint_matches,
)
from app.models.session import Session
from app.models.user import User
from app.repositories.session import SessionRepository
from app.repositories.user import UserRepository
from app.schemas.user import TokenPair
from app.services.audit_service import truncate_ip_address, truncate_user_agent

__all__ = ["SessionService"]

_INVALID_SESSION = "This session is no longer valid."


class SessionService:
    """Issues, rotates, lists and revokes device sessions."""

    def __init__(self, repository: SessionRepository, settings: Settings | None = None) -> None:
        self.repository = repository
        self.settings = settings or get_settings()

    # -- Issuance ------------------------------------------------------------

    async def issue(
        self,
        *,
        user: User,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Session, TokenPair]:
        """Mint a session row and the token pair that belongs to it.

        The refresh token carries the row's id in its ``sid`` claim and a fresh
        ``jti``, and the access token carries the *same* ``sid`` with its own
        ``jti`` — so the API can answer "which device presented this bearer?"
        without a lookup table, and so a logout can revoke exactly one device.

        The row has to exist before its id can travel in a token, and the id is
        minted by the repository at insert — ``SessionRepository.create`` does not
        take one — so the row is written once carrying the digest of a discarded
        random value and immediately rewritten with the real digest. No token
        exists between those two writes, so the transient digest cannot be
        replayed against anything; it exists only because ``token_hash`` is NOT
        NULL. If the repository is ever given an explicit ``session_id``, this
        becomes a single write and :func:`~app.core.security.new_session_id`
        moves back here.

        ``expires_at`` comes from ``session_absolute_lifetime_days`` rather than
        from the refresh token's own lifetime: a rotating token would otherwise
        let a session that is never signed out of live forever, outliving any
        actual sign-in event.

        Returns:
            The stored row and the pair handed to the client.

        Raises:
            Nothing by contract. The session cap is enforced by evicting the
            oldest live sessions, never by refusing to issue.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=self.settings.session_absolute_lifetime_days)

        db_session = await self.repository.create(
            user_id=user.id,
            token_hash=hash_token(secrets.token_urlsafe(32)),
            user_agent=truncate_user_agent(user_agent),
            ip_address=truncate_ip_address(ip_address),
            expires_at=expires_at,
        )
        session_id = str(db_session.id)

        refresh_token = create_refresh_token(
            user.id,
            settings=self.settings,
            extra_claims={"sid": session_id, "jti": str(uuid.uuid4())},
        )
        db_session = await self.repository.rotate_token(
            db_session,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
        )
        db_session = await self.repository.touch(db_session, last_used_at=now)

        pair = TokenPair(
            access_token=create_access_token(
                user.id,
                settings=self.settings,
                extra_claims={"sid": session_id, "jti": str(uuid.uuid4())},
            ),
            refresh_token=refresh_token,
            expires_in=self.settings.access_token_expire_minutes * 60,
            session_id=db_session.id,
        )
        await self._enforce_session_cap(user.id, new_session_id=db_session.id)
        return db_session, pair

    async def _enforce_session_cap(self, user_id: uuid.UUID, *, new_session_id: uuid.UUID) -> None:
        """Evict the oldest live sessions until the user is back under the cap.

        **Why this bound exists.** A refresh token is a long-lived bearer
        credential, and nothing stops an attacker who has stolen one from
        replaying it to sign in again and again. Each replay leaves another live
        session behind, so without a cap the account accumulates sessions at the
        attacker's convenience — and, just as bad, the legitimate owner gets no
        signal, because every one of those sessions looks like an ordinary
        device. Evicting the *oldest* rows means the attacker and the owner are
        treated the same way: whoever signs in too many times loses their oldest
        session first.

        The session just issued is excluded from the eviction candidates. It is
        the newest row, but ``created_at`` is a server default with one-second
        resolution, so a sign-in in the same second as another could tie, and a
        tie must never let a brand-new session evict itself.
        """
        live = await self.repository.list_live_ordered_by_created(user_id)
        excess = len(live) - self.settings.max_active_sessions
        if excess <= 0:
            return
        now = datetime.now(UTC)
        candidates = [row for row in live if row.id != new_session_id]
        for stale in candidates[:excess]:
            await self.repository.revoke(stale, revoked_at=now)

    # -- Rotation ------------------------------------------------------------

    async def rotate(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Session, TokenPair]:
        """Exchange a refresh token for a new pair on the same session row.

        Rotation **replaces** the row's token instead of creating a second row,
        so "one row = one device" stays true: rotating on a phone a hundred
        times still shows up as one device in the sessions screen, and signing
        out of that device still takes one UPDATE.

        Every rejection below answers with the same message. A caller must not be
        able to tell "this session was signed out" from "this token was already
        rotated away" from "this session belongs to someone else" — the first
        tells an attacker that a guess was right.

        Args:
            refresh_token: The raw token presented by the client.
            user_agent: Updated device label from the new request.
            ip_address: Updated address from the new request.

        Returns:
            The same row, now holding the new digest, and a fresh token pair.

        Raises:
            UnauthorizedError: For any token that cannot be used.
        """
        token_data = decode_token(
            refresh_token, expected_type=TokenType.REFRESH, settings=self.settings
        )
        session_id = self._session_from_token(refresh_token, token_data.claims)
        db_session = await self._live_session_for_token(
            session_id, refresh_token, token_data.claims
        )
        user = await self._users.get_by_id(db_session.user_id)
        if user is None:
            raise UnauthorizedError(_INVALID_SESSION)
        if not user.is_active:
            raise UnauthorizedError("This account is inactive.")

        now = datetime.now(UTC)
        if db_session.user_agent != user_agent or db_session.ip_address != ip_address:
            # Refreshed from a different browser or address: the row follows the
            # device it is now being used from, which is the only way the
            # sessions screen can show something a user recognises.
            db_session.user_agent = truncate_user_agent(user_agent)
            db_session.ip_address = truncate_ip_address(ip_address)
        new_refresh = create_refresh_token(
            user.id,
            settings=self.settings,
            extra_claims={"sid": str(db_session.id), "jti": str(uuid.uuid4())},
        )
        db_session = await self.repository.rotate_token(
            db_session,
            token_hash=hash_token(new_refresh),
            expires_at=now + timedelta(days=self.settings.session_absolute_lifetime_days),
        )
        db_session = await self.repository.touch(db_session, last_used_at=now)
        pair = TokenPair(
            access_token=create_access_token(
                user.id,
                settings=self.settings,
                extra_claims={"sid": str(db_session.id), "jti": str(uuid.uuid4())},
            ),
            refresh_token=new_refresh,
            expires_in=self.settings.access_token_expire_minutes * 60,
            session_id=db_session.id,
        )
        return db_session, pair

    def _session_from_token(self, token: str, claims: dict[str, object]) -> uuid.UUID:
        """Resolve a refresh token's ``sid`` claim, or raise.

        The claim is required. A token minted before sessions existed carries no
        session, cannot be revoked individually, and cannot be attributed to a
        device, so accepting one silently would leave exactly the gap sessions
        were introduced to close. It is rejected instead.
        """
        raw_session_id = claims.get("sid")
        if not isinstance(raw_session_id, str) or not raw_session_id:
            raise UnauthorizedError(_INVALID_SESSION)
        try:
            return uuid.UUID(raw_session_id)
        except ValueError:
            raise UnauthorizedError(_INVALID_SESSION) from None

    async def _live_session_for_token(
        self, session_id: uuid.UUID, token: str, claims: dict[str, object]
    ) -> Session:
        """Return the session only if the token is its current, live credential."""
        db_session = await self.repository.get_by_id(session_id)
        if db_session is None:
            raise UnauthorizedError(_INVALID_SESSION)
        if str(db_session.user_id) != claims.get("sub"):
            # The sid is signed, so this should be unreachable; checking anyway
            # means a future change to how the claim is built cannot turn into a
            # token that rotates somebody else's session.
            raise UnauthorizedError(_INVALID_SESSION)
        if db_session.revoked_at is not None:
            raise UnauthorizedError(_INVALID_SESSION)
        if _is_expired(db_session.expires_at, datetime.now(UTC)):
            raise UnauthorizedError(_INVALID_SESSION)
        if not token_fingerprint_matches(token, db_session.token_hash):
            # A rotated-away token: this is what makes a replayed refresh token
            # detectable, since the row only ever holds the current digest.
            raise UnauthorizedError(_INVALID_SESSION)
        return db_session

    # -- Reads ---------------------------------------------------------------

    async def list_for_user(
        self, *, user_id: uuid.UUID, current_session_id: uuid.UUID | None = None
    ) -> tuple[list[Session], uuid.UUID | None]:
        """Return the user's sessions and, separately, the current session's id.

        Marking which row is *current* is deliberately not done here: that is a
        presentation decision, and the schema layer owns it. Returning the id
        alongside the rows keeps the service from having to know how the response
        model is shaped.

        Args:
            user_id: Whose sessions to list. The scope is the caller's own.
            current_session_id: The session the request was made from, if the
                bearer carried a usable ``sid`` claim.

        Returns:
            The rows, newest first, and the current session id as supplied.
        """
        sessions = await self.repository.list_for_user(user_id)
        return sessions, current_session_id

    # -- Revocation ----------------------------------------------------------

    async def revoke(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> Session:
        """Revoke one of this user's own sessions and return the row.

        **The lookup is scoped by ``user_id`` on purpose.** Fetching by id alone
        and checking ownership afterwards would make this endpoint an IDOR: any
        authenticated user who guessed or enumerated a session id could revoke
        another user's session — and, because the answer differs for "exists but
        not yours" versus "does not exist", could use the difference to confirm
        that an id is real. Scoping in the query makes another user's session
        indistinguishable from one that never existed.

        Args:
            session_id: The session to revoke.
            user_id: The caller. Must own the session.

        Returns:
            The revoked row. Revoking an already-revoked session returns it
            unchanged rather than failing, so a double-click is not an error.

        Raises:
            NotFoundError: If the caller does not own a session with this id.
        """
        db_session = await self.repository.get_by_id_for_user(session_id, user_id)
        if db_session is None:
            raise NotFoundError("Session not found.")
        if db_session.revoked_at is not None:
            return db_session
        return await self.repository.revoke(db_session, revoked_at=datetime.now(UTC))

    async def revoke_all(
        self, *, user_id: uuid.UUID, keep_session_id: uuid.UUID | None = None
    ) -> int:
        """Revoke every live session for a user and return how many were ended.

        Args:
            user_id: Whose sessions to end.
            keep_session_id: A session to spare, for "sign out my other
                devices" where the caller stays signed in here. ``None`` ends
                them all, which is what a password reset must do.
        """
        return await self.repository.revoke_all_for_user(
            user_id,
            revoked_at=datetime.now(UTC),
            except_id=keep_session_id,
        )

    async def revoke_by_token(self, token: str) -> None:
        """Revoke the session behind ``token``, if there is one. Never raises.

        Logout has to succeed for the caller whatever the state of the world: a
        token that is already expired, already rotated away, already revoked, or
        was never a session token at all is not a reason to return an error the
        client cannot act on. Every failure here is therefore swallowed, and the
        caller still ends up logged out as far as it is concerned.
        """
        try:
            token_data = decode_token(token, settings=self.settings)
            session_id = self._session_from_token(token, token_data.claims)
            db_session = await self._live_session_for_token(session_id, token, token_data.claims)
        except Exception:
            return
        try:
            await self.repository.revoke(db_session, revoked_at=datetime.now(UTC))
        except Exception:
            return

    # -- Collaborators -------------------------------------------------------

    @property
    def _users(self) -> UserRepository:
        """The account repository, bound to the same request-scoped session.

        Built on demand from the session repository's own
        :class:`~sqlalchemy.ext.asyncio.AsyncSession` rather than injected, so
        that the "one session, one service" construction the API layer uses
        stays intact and a rotation reads the row and the account from a single
        connection.
        """
        return UserRepository(self.repository.session)


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    """Whether a session's token expiry has passed.

    A naive value is read as UTC: the column is timezone-aware, but a value
    assembled in memory by a caller is not guaranteed to be, and treating it as
    local time would expire sessions early for anyone east of UTC.
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now
