"""API-layer dependencies.

Only wiring that is specific to the HTTP layer lives here: turning the
request-scoped session into the services the routers need, and layering the
*token is still good* checks on top of the canonical identity resolution —
that the token was not denylisted, and that the session row it names is still
live. Everything about *who is calling* — bearer scheme, current user, superuser
check — is owned by ``app.core.deps`` and is re-exported so routers have a
single import site.

The wiring rule here is that a service is built from *real* collaborators. A
provider that passes ``None`` for something the service will actually use does
not produce a simpler object graph, it produces a service that silently degrades
— ``AuthService`` without a session store issues tokens with no row behind them
and cannot revoke a single device, and does so without telling anyone. The
degraded paths exist for the token rules to be exercisable on their own; the API
layer is not that caller. That rule is why :func:`get_authenticated_user` can
consult the ``sessions`` table: the row is there to be consulted.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from app.repositories.activity import ActivityRepository
from app.repositories.audit import AuditRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.project import ProjectRepository
from app.repositories.session import SessionRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.services.activity_service import ActivityService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.project_service import ProjectService
from app.services.session_service import SessionService
from app.services.tag_service import TagService
from app.services.task_service import TaskService
from app.services.user_service import UserService

#: User-Agent values are stored in a 512-character column; the cap is applied
#: where the request is read so no caller has to remember it. Matches
#: ``sessions.user_agent`` and ``audit_logs.user_agent``.
_MAX_USER_AGENT_LENGTH = 512

#: One message for every way the ``sessions`` row behind a bearer can fail to
#: authorise it. "Revoked", "expired" and "no such row" must be indistinguishable
#: to the caller, or this check becomes an oracle for which session ids are real
#: — the same rule :func:`app.api.v1.auth.revoke_session` applies in the other
#: direction.
_SESSION_NOT_LIVE = "This session is no longer valid."


def get_session_repository(session: DbSession) -> SessionRepository:
    """Provide a request-scoped session repository."""
    return SessionRepository(session)


def get_audit_repository(session: DbSession) -> AuditRepository:
    """Provide a request-scoped audit repository."""
    return AuditRepository(session)


def get_password_reset_repository(session: DbSession) -> PasswordResetRepository:
    """Provide a request-scoped password-reset repository."""
    return PasswordResetRepository(session)


def get_project_repository(session: DbSession) -> ProjectRepository:
    """Provide a request-scoped project repository."""
    return ProjectRepository(session)


def get_task_repository(session: DbSession) -> TaskRepository:
    """Provide a request-scoped task repository."""
    return TaskRepository(session)


def get_tag_repository(session: DbSession) -> TagRepository:
    """Provide a request-scoped tag repository."""
    return TagRepository(session)


def get_activity_repository(session: DbSession) -> ActivityRepository:
    """Provide a request-scoped activity repository."""
    return ActivityRepository(session)


SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
AuditRepositoryDep = Annotated[AuditRepository, Depends(get_audit_repository)]
PasswordResetRepositoryDep = Annotated[
    PasswordResetRepository, Depends(get_password_reset_repository)
]
ActivityRepositoryDep = Annotated[ActivityRepository, Depends(get_activity_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_session_service(
    repository: SessionRepositoryDep,
    settings: SettingsDep,
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> SessionService:
    """Provide a request-scoped session service.

    Settings are injected rather than left to the service's own
    ``get_settings()`` fallback so the lifetime and cap values a request runs
    under are the ones resolved for that request. The audit sink is threaded
    through so revoking a session leaves a trail.
    """
    return SessionService(repository, settings, audit)


def get_audit_service(repository: AuditRepositoryDep) -> AuditService:
    """Provide a request-scoped audit service."""
    return AuditService(repository)


def get_activity_service(repository: ActivityRepositoryDep) -> ActivityService:
    """Provide a request-scoped activity service.

    The single sink both work-management services write through. It is wired
    here rather than left optional on purpose: see this module's docstring for
    why a service built from a degraded collaborator is not a simpler object
    graph. ``ProjectService`` and ``TaskService`` both document ``activity=None``
    as "the lifecycle rules without a history sink", which is a real mode for
    exercising the rules in isolation — but it is not the API layer, and shipping
    it would mean every project created through the API leaves no trace.
    """
    return ActivityService(repository)


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
ActivityServiceDep = Annotated[ActivityService, Depends(get_activity_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]
TaskRepositoryDep = Annotated[TaskRepository, Depends(get_task_repository)]
TagRepositoryDep = Annotated[TagRepository, Depends(get_tag_repository)]


def get_project_service(
    repository: ProjectRepositoryDep,
    tasks: TaskRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> ProjectService:
    """Provide a request-scoped project service.

    **The task repository is not optional here.** :class:`ProjectService` takes it
    keyword-only because only :meth:`~app.services.project_service.ProjectService.summary`
    needs it, and it raises ``ValueError`` rather than inventing zero counts when
    it is missing — a wiring mistake should name itself, and
    ``GET /projects/{id}/summary`` is the route that would hit it. The API layer
    is not that caller: it serves the summary, so it wires the collaborator.

    ``activity`` writes ``activity_events`` beside every mutation, and ``audit`` is
    passed for symmetry with the auth services and used by neither — a project
    completing is a fact about the work, not a fact about the account, and
    :mod:`app.services.project_service` states the rule at length.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the values a request runs under are the ones resolved for it.
    """
    return ProjectService(
        repository,
        activity=activity,
        audit=audit,
        settings=settings,
        task_repository=tasks,
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_task_service(
    repository: TaskRepositoryDep,
    projects: ProjectRepositoryDep,
    tags: TagRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> TaskService:
    """Provide a request-scoped task service.

    Three repositories, not one, because the task table cannot answer two of the
    questions its own service asks. A task's ``owner_id`` is denormalised from
    its project, so nothing in the schema stops a row being filed under another
    account's project and :meth:`app.services.task_service.TaskService.create`
    has to re-check that destination through the *scoped* project lookup — a
    check the task repository has no way to perform. Tags are per-user rows, so
    every tag the service acts on is resolved through the scoped tag lookup as
    well. Wiring the dependencies here rather than in the router is what keeps
    ``app/api/v1/tasks.py`` free of repository imports.

    ``activity`` is the feed every mutation writes beside itself — a task
    completing, a board being re-triaged, a deadline slipping. It is wired rather
    than left as the service's documented ``None`` mode because the API layer is
    not a caller exercising the rules in isolation: a card whose history nobody
    can read is a card whose history does not exist.
    """
    return TaskService(
        repository,
        projects,
        tags,
        activity=activity,
        audit=audit,
        settings=settings,
    )


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_tag_service(
    repository: TagRepositoryDep,
    activity: ActivityServiceDep,
    audit: AuditServiceDep,
    settings: SettingsDep,
) -> TagService:
    """Provide a request-scoped tag service.

    ``activity`` is wired rather than left as the service's documented ``None``
    mode for the reason this module's docstring gives in full: a service built
    from a degraded collaborator is not a simpler object graph, it is one that
    silently does less. ``TagService`` records the two tag-application writes
    (``TASK_UPDATED`` on the task, ``PROJECT_UPDATED`` on the project) and
    records nothing for a tag's own create/rename/delete, because
    :class:`~app.models.enums.ActivityEvent` has no member for those — the gap is
    documented at length in :mod:`app.services.tag_service` rather than papered
    over with a borrowed event name.

    ``audit`` is passed through and used by neither, for the same reason
    ``ProjectService`` does not use it: coining a label is a fact about the work,
    not a fact about the account.

    Settings are injected rather than left to the service's own ``get_settings()``
    fallback, so the values a request runs under are the ones resolved for it.
    """
    return TagService(
        repository,
        activity=activity,
        audit=audit,
        settings=settings,
    )


TagServiceDep = Annotated[TagService, Depends(get_tag_service)]


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
    sessions: SessionRepositoryDep,
    session_id: CurrentSessionId,
) -> User:
    """Resolve the caller and reject a bearer the session table no longer backs.

    Two layers, in this order, and the second is the one that makes revocation
    mean what the UI tells the user it means.

    **The denylist first.** :meth:`AuthService.is_revoked` catches the single
    ``jti`` that logout presented, or that a rotation just retired. It is
    process-local by construction, so it is belt-and-braces rather than the
    control.

    **The session row second, and this is the control.** A JWT stays
    cryptographically valid until it expires, so checking only the denylist left
    revoking a device ending nothing but that device's *refresh* credential:
    every access token already minted from it went on authorising requests for
    the whole ``ACCESS_TOKEN_EXPIRE_MINUTES`` window, and ``DELETE
    /auth/sessions/{id}``, ``POST /auth/logout-all`` and ``PATCH
    /auth/password`` all produced that same gap. The row lives in the database,
    so requiring it also makes revocation survive a restart — a documented
    limitation of the denylist that no longer applies to this path.

    **Cost: one primary-key lookup per authenticated request.** For a local-first,
    single-user deployment that is the right trade — the alternative is up to an
    hour during which "that device is signed out" is not true. If the lookup ever
    becomes hot, the follow-up is a per-session cache with a short TTL, never one
    that can outlive a revocation.

    **No new trust boundary.** The ``sid`` claim is inside a signed JWT and
    ``get_current_user`` has already bound the subject to this user, so the row
    cannot name somebody else's device. It is looked up with
    ``get_by_id_for_user(sid, user.id)`` anyway — scoping a lookup by the
    subject it belongs to is free, and it means a future change to how the claim
    is built cannot turn into a cross-tenant read.

    Args:
        current_user: The caller, already resolved from the same token.
        credentials: The raw bearer, when the request carried one.
        auth: Supplies the revocation denylist.
        sessions: Supplies the session row the token claims to belong to.
        session_id: The token's ``sid`` claim, or ``None`` when it has no usable
            one. ``None`` is fatal here rather than skipped: a token with no
            session behind it cannot be revoked individually, which is exactly
            the gap sessions exist to close.

    Returns:
        The caller, unchanged.

    Raises:
        UnauthorizedError: If the token was denylisted, names no usable session,
            or names one that is missing, not the caller's, revoked or expired.
    """
    if credentials is not None and credentials.credentials:
        token_data = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
        if await auth.is_revoked(token_data):
            raise UnauthorizedError("This token has been revoked.")
    if session_id is None:
        raise UnauthorizedError(_SESSION_NOT_LIVE)
    row = await sessions.get_by_id_for_user(session_id, current_user.id)
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or _session_expired(row.expires_at, now):
        raise UnauthorizedError(_SESSION_NOT_LIVE)
    return current_user


AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def _session_expired(expires_at: datetime | None, now: datetime) -> bool:
    """Whether a session row's expiry has already passed.

    Mirrors :func:`app.services.session_service._is_expired` rather than sharing
    it: that helper is private to the service layer and importing across module
    boundaries would make an internal become part of this module's contract. A
    naive value is read as UTC, because the column is timezone-aware but a value
    assembled in memory is not guaranteed to be, and treating it as local time
    expires sessions early for anyone east of UTC.

    The comparison is against this process's clock rather than the database's
    ``func.now()``, which is the one place this path departs from the rule
    :mod:`app.repositories.session` follows everywhere else. The instant itself
    comes from the row, so the only exposure is the drift between the host's
    clock and the server's — and a session's expiry is days away, not seconds.

    Args:
        expires_at: The row's ``expires_at``, or ``None`` for a value that was
            never set. A missing expiry counts as expired: a session that cannot
            say when it dies is not a session that outlives.
        now: The instant to compare against, read once by the caller so the
            comparison is against a single clock reading.
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


__all__ = [
    "ActivityRepository",
    "ActivityRepositoryDep",
    "ActivityService",
    "ActivityServiceDep",
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
    "ProjectRepository",
    "ProjectRepositoryDep",
    "ProjectService",
    "ProjectServiceDep",
    "SessionRepository",
    "SessionRepositoryDep",
    "SessionService",
    "SessionServiceDep",
    "Settings",
    "SettingsDep",
    "SuperUser",
    "TagRepository",
    "TagRepositoryDep",
    "TagService",
    "TagServiceDep",
    "TaskRepository",
    "TaskRepositoryDep",
    "TaskService",
    "TaskServiceDep",
    "UserRepository",
    "UserRepositoryDep",
    "UserService",
    "UserServiceDep",
    "bearer_scheme",
    "get_activity_repository",
    "get_activity_service",
    "get_audit_repository",
    "get_audit_service",
    "get_auth_service",
    "get_authenticated_user",
    "get_client_context",
    "get_current_session_id",
    "get_current_user",
    "get_optional_user",
    "get_password_reset_repository",
    "get_project_repository",
    "get_project_service",
    "get_session_repository",
    "get_session_service",
    "get_settings",
    "get_tag_repository",
    "get_tag_service",
    "get_task_repository",
    "get_task_service",
    "get_user_repository",
    "get_user_service",
]
