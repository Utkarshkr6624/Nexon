"""API-layer dependencies.

Only wiring that is specific to the HTTP layer lives here: turning the
request-scoped session into the services the routers need, and layering token
revocation on top of the canonical identity resolution. Everything about *who
is calling* — bearer scheme, current user, superuser check — is owned by
``app.core.deps`` and is re-exported so routers have a single import site.

The wiring rule here is that a service is built from *real* collaborators. A
provider that passes ``None`` for something the service will actually use does
not produce a simpler object graph, it produces a service that silently degrades
— ``AuthService`` without a session store issues tokens with no row behind them
and cannot revoke a single device, and does so without telling anyone. The
degraded paths exist for the token rules to be exercisable on their own; the API
layer is not that caller.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.deps import (
    Credentials,
    CurrentSessionId,
    CurrentUser,
    DbSession,
    SuperUser,
    UserRepositoryDep,
    bearer_scheme,
    get_current_session_id,
    get_current_user,
    get_optional_user,
    get_user_repository,
)
from app.core.exceptions import UnauthorizedError
from app.core.security import TokenType, decode_token
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.session import SessionRepository
from app.repositories.user import UserRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.session_service import SessionService
from app.services.user_service import UserService

#: User-Agent values are stored in a 512-character column; the cap is applied
#: where the request is read so no caller has to remember it. Matches
#: ``sessions.user_agent`` and ``audit_logs.user_agent``.
_MAX_USER_AGENT_LENGTH = 512


def get_session_repository(session: DbSession) -> SessionRepository:
    """Provide a request-scoped session repository."""
    return SessionRepository(session)


def get_audit_repository(session: DbSession) -> AuditRepository:
    """Provide a request-scoped audit repository."""
    return AuditRepository(session)


def get_password_reset_repository(session: DbSession) -> PasswordResetRepository:
    """Provide a request-scoped password-reset repository."""
    return PasswordResetRepository(session)


SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
AuditRepositoryDep = Annotated[AuditRepository, Depends(get_audit_repository)]
PasswordResetRepositoryDep = Annotated[
    PasswordResetRepository, Depends(get_password_reset_repository)
]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_session_service(repository: SessionRepositoryDep, settings: SettingsDep) -> SessionService:
    """Provide a request-scoped session service.

    Settings are injected rather than left to the service's own
    ``get_settings()`` fallback so the lifetime and cap values a request runs
    under are the ones resolved for that request.
    """
    return SessionService(repository, settings)


def get_audit_service(repository: AuditRepositoryDep) -> AuditService:
    """Provide a request-scoped audit service."""
    return AuditService(repository)


def get_auth_service(
    repository: UserRepositoryDep,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> AuthService:
    """Provide a request-scoped auth service wired to sessions and the trail."""
    return AuthService(repository, sessions, audit)


def get_user_service(
    repository: UserRepositoryDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> UserService:
    """Provide a request-scoped user service.

    The audit sink is passed through so account changes are recorded from the
    service rather than from the router that happened to call it.
    """
    return UserService(repository, audit=audit)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_client_context(request: Request) -> tuple[str | None, str | None]:
    """Return ``(ip_address, user_agent)`` for the calling request.

    The address rule is the one :func:`app.core.middleware._client_ip` applies
    to the access log — the left-most ``X-Forwarded-For`` entry, because that is
    what the outermost proxy saw, falling back to the socket peer when the
    request arrived directly — so the audit trail and the access log never
    disagree about where a request came from.

    **Neither value is an identity claim.** Both headers are supplied by the
    client: anything that can reach the process can put any string in them. They
    are recorded because "this sign-in came from that address, with that browser"
    is what makes an audit row worth reading, not because they are trustworthy.
    No authorisation decision may ever rest on them — see
    :attr:`app.models.session.Session.ip_address`.
    """
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = forwarded.split(",")[0].strip() if forwarded else ""
    if not ip_address:
        ip_address = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent") or ""
    return ip_address or None, user_agent[:_MAX_USER_AGENT_LENGTH] or None


ClientContext = Annotated[tuple[str | None, str | None], Depends(get_client_context)]


async def get_authenticated_user(
    current_user: Annotated[User, Depends(get_current_user)],
    credentials: Credentials,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    """Resolve the caller and reject a token that logout revoked.

    A JWT stays cryptographically valid after logout; this is what makes the
    revocation denylist observable to the endpoints that opt in.
    """
    if credentials is not None and credentials.credentials:
        token_data = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
        if await auth.is_revoked(token_data):
            raise UnauthorizedError("This token has been revoked.")
    return current_user


AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]

__all__ = [
    "AuditRepository",
    "AuditRepositoryDep",
    "AuditService",
    "AuditServiceDep",
    "AuthService",
    "AuthServiceDep",
    "AuthenticatedUser",
    "ClientContext",
    "Credentials",
    "CurrentSessionId",
    "CurrentUser",
    "DbSession",
    "PasswordResetRepository",
    "PasswordResetRepositoryDep",
    "SessionRepository",
    "SessionRepositoryDep",
    "SessionService",
    "SessionServiceDep",
    "Settings",
    "SettingsDep",
    "SuperUser",
    "UserRepository",
    "UserRepositoryDep",
    "UserService",
    "UserServiceDep",
    "bearer_scheme",
    "get_audit_repository",
    "get_audit_service",
    "get_auth_service",
    "get_authenticated_user",
    "get_client_context",
    "get_current_session_id",
    "get_current_user",
    "get_optional_user",
    "get_password_reset_repository",
    "get_session_repository",
    "get_session_service",
    "get_settings",
    "get_user_repository",
    "get_user_service",
]
