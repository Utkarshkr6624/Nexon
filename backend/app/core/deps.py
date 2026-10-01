"""Reusable FastAPI dependencies for authentication and authorisation.

This is the canonical implementation. ``app/api/deps.py`` owns HTTP-layer
wiring (session -> repository -> service); anything that is purely about
"who is calling" lives here so routers depend on one place.

The module talks to the user *repository* only — it never imports a service
layer, which keeps the dependency graph acyclic and makes the auth rules
readable in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import Permission, has_permission
from app.core.security import TokenType, decode_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.user import UserRepository

__all__ = [
    "CurrentSessionId",
    "CurrentUser",
    "DbSession",
    "SuperUser",
    "bearer_scheme",
    "get_current_active_superuser",
    "get_current_session_id",
    "get_current_user",
    "get_optional_user",
    "get_user_repository",
    "require_permission",
]

#: ``auto_error=False`` so a missing header yields our 401 envelope instead of
#: FastAPI's built-in 403.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_db)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_user_repository(session: DbSession) -> UserRepository:
    """Provide a request-scoped user repository."""
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


async def get_optional_user(
    credentials: Credentials,
    repository: UserRepositoryDep,
) -> User | None:
    """Resolve a bearer token when one is present, without requiring it."""
    if credentials is None or not credentials.credentials:
        return None
    return await _authenticate(credentials, repository)


async def get_current_user(
    credentials: Credentials,
    repository: UserRepositoryDep,
) -> User:
    """Resolve the bearer token to an active :class:`User`.

    Raises :class:`UnauthorizedError` for a missing, malformed, expired or
    wrong-type token, an unknown subject, and an inactive account.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication credentials were not provided.")
    return await _authenticate(credentials, repository)


async def get_current_active_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Resolve the caller and require administrator privileges.

    Two things grant it, and both must: the ``role`` column and the legacy
    ``is_superuser`` boolean. The role is authoritative — it is what
    :mod:`app.core.permissions` consults, and a permission gate that disagrees
    with the superuser gate on the same request is a bug waiting to happen. The
    column is still honoured because Phase 1 rows can only be promoted by setting
    it, so dropping it here would silently un-promote every administrator created
    before the role column existed. New code should set ``role``; the column stays
    until a migration proves no row still relies on it.
    """
    if not current_user.is_superuser and current_user.role != UserRole.ADMIN.value:
        raise ForbiddenError("This action requires superuser privileges.")
    return current_user


async def get_current_session_id(credentials: Credentials) -> uuid.UUID | None:
    """Return the ``sid`` claim of the presented access token, if there is one.

    A session id is an *enrichment*, never a gate: every endpoint that needs one
    already resolves the user first, so this dependency only has to answer
    "which device is asking". Any failure — no header, an expired token, a
    refresh token replayed as a bearer, a ``sid`` that is not a UUID — yields
    ``None`` and lets the caller decide what to do with the absence. Raising here
    would turn a cosmetic lookup into a second, differently-worded 401 on top of
    the one :func:`get_current_user` already produced.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        token_data = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
        raw_sid = token_data.claims.get("sid")
        return uuid.UUID(str(raw_sid)) if raw_sid else None
    except (UnauthorizedError, ValueError, TypeError, AttributeError):
        return None


def require_permission(permission: Permission | str) -> Callable[..., Any]:
    """Build a dependency that admits only roles holding ``permission``.

    A factory rather than a plain dependency because the check has an argument:
    the route states *which* capability it is protecting
    (``Depends(require_permission(Permission.USERS_WRITE))``) and this turns
    that statement into the caller check. The returned callable is an ordinary
    FastAPI dependency and composes with the rest, so authorisation reads in the
    route signature next to the identity it depends on.

    The role check is the same fail-closed lookup every other gate uses, and it
    runs *after* authentication rather than instead of it: an anonymous request
    must not be able to tell "you are not signed in" from "you may not do that".
    """

    def dependency(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        """Return the caller if their role grants ``permission``."""
        if not has_permission(current_user.role, permission):
            raise ForbiddenError("You do not have permission to perform this action.")
        return current_user

    return dependency


async def _authenticate(
    credentials: HTTPAuthorizationCredentials,
    repository: UserRepository,
) -> User:
    token_data = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    try:
        user_id = uuid.UUID(token_data.subject)
    except ValueError:
        raise UnauthorizedError("The authentication token is invalid.") from None

    user = await repository.get_by_id(user_id)
    if user is None:
        raise UnauthorizedError("The authentication token is invalid.")
    if not user.is_active:
        raise UnauthorizedError("This account is inactive.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
SuperUser = Annotated[User, Depends(get_current_active_superuser)]
#: The caller's session id, or ``None``. Optional on purpose: a client with no
#: bearer token is still a legitimate caller of the unauthenticated endpoints,
#: and this must not turn into a 401 there.
CurrentSessionId = Annotated[uuid.UUID | None, Depends(get_current_session_id)]
