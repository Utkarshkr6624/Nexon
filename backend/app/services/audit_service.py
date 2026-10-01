"""Writes to the security audit trail.

The trail is written from the service layer, not from the routers, so that the
same event is recorded whether it is triggered by an HTTP endpoint or by a
background job later on. This module deliberately knows nothing about HTTP: the
API layer hands it a client address and a user agent as plain values, using the
:func:`client_ip_from_headers` and :func:`truncate_user_agent` helpers below.

Repository contract relied on by this module::

    AuditRepository.record(*, user_id, event_type, ip_address=None,
                           user_agent=None, metadata=None) -> AuditLog
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.models.audit import AuditEvent, AuditLog, validate_audit_event
from app.repositories.audit import AuditRepository

__all__ = [
    "AuditService",
    "client_ip_from_headers",
    "truncate_ip_address",
    "truncate_user_agent",
]

logger = logging.getLogger("app.services.audit_service")

#: Matches ``sessions.ip_address`` and ``audit_logs.ip_address``. Long enough for
#: an IPv6 address with a port, and it is also the cap applied here: a value that
#: cannot fit the column must be truncated here rather than surface as a driver
#: error from inside an audit write.
_MAX_IP_LENGTH = 45
#: Matches ``sessions.user_agent`` and ``audit_logs.user_agent``.
_MAX_USER_AGENT_LENGTH = 512


def client_ip_from_headers(headers: Mapping[str, str]) -> str | None:
    """Return the originating client address implied by request headers.

    Uses exactly the rule :func:`app.core.middleware._client_ip` applies to the
    access log: the left-most ``X-Forwarded-For`` entry, because that is the
    address the outermost proxy saw. The rule is duplicated rather than shared
    because the middleware's helper takes a :class:`fastapi.Request` and this
    module must not import FastAPI; the two must keep agreeing or the audit
    trail and the access log will disagree about who a request came from.

    Args:
        headers: Request headers. Anything with a case-insensitive ``get`` will
            do, which includes Starlette's ``Headers``.

    Returns:
        The address, capped at the column width, or ``None`` when the request
        carried no forwarding header. Absence is normal and meaningful: a client
        that reached the process directly sends none.
    """
    forwarded = headers.get("x-forwarded-for")
    if not forwarded:
        return None
    first = forwarded.split(",")[0].strip()
    return first[:_MAX_IP_LENGTH] or None


def truncate_ip_address(ip_address: str | None) -> str | None:
    """Return a client address safe to store, capped at the column width.

    Both address columns are 45 characters wide and both are written from
    request-derived data, so the bound is applied here rather than being
    rediscovered by each caller. An over-long value must not become a driver
    error raised from inside an audit or session write.

    Args:
        ip_address: The address as observed, if any.

    Returns:
        The value trimmed to the column width, or ``None``.
    """
    if not ip_address:
        return None
    return ip_address[:_MAX_IP_LENGTH]


def truncate_user_agent(user_agent: str | None) -> str | None:
    """Return a user agent safe to store, capped at the column width.

    The User-Agent header is attacker-controlled and unbounded, and the column
    is 512 characters wide, so the value is truncated here. Truncating at the
    storage boundary means every writer of these columns gets the same bound
    instead of each one remembering to apply its own.

    Args:
        user_agent: The raw header value, if the request carried one.

    Returns:
        The value trimmed to the column width, or ``None``.
    """
    if not user_agent:
        return None
    return user_agent[:_MAX_USER_AGENT_LENGTH]


class AuditService:
    """Records security-relevant events, and records them best-effort.

    **Why ``record`` never raises.** Audit writing is observability, not
    business logic, and it must not be able to deny service. The failure mode
    this protects against is concrete: if the audit table is unavailable, a
    rollback of it, a lock, or a full disk, then a service that propagated the
    error would refuse to let anyone sign in — an attacker who can fill the
    audit table, or an unlucky migration, would take authentication down for
    every user. So a failed write is logged at WARNING with ``exc_info`` (the
    detail stays in the log, where an operator can see it) and ``record``
    returns ``None``. The caller cannot tell the difference, and cannot be
    tempted to "fix" it by making the write mandatory.

    This is a deliberate trade, and the other side of it is that a broken audit
    trail is silent at the request level. That is why the failure is logged at
    WARNING rather than swallowed: the trail going dark must be visible in the
    operational logs even though it is invisible to the user.

    The one thing a caller must not do is retry. A write that failed partway
    through is not safe to assume did not happen, and re-recording a security
    event is how a trail stops being evidence.
    """

    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    async def record(
        self,
        event: AuditEvent | str,
        *,
        user_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditLog | None:
        """Append one event to the trail, swallowing and logging any failure.

        Args:
            event: The event to record. A plain string is accepted so that a
                caller which already holds one does not have to convert it; an
                unrecognised value is rejected rather than stored, because a row
                no filter can match is a missing event.
            user_id: The account the event concerns, or ``None`` when it has no
                account behind it — a failed sign-in against an address nobody
                owns, most importantly, which is exactly the event that column is
                nullable for.
            ip_address: Originating client address, from
                :func:`client_ip_from_headers`.
            user_agent: Request user agent, from :func:`truncate_user_agent`.
            metadata: Non-sensitive context only. This lands verbatim in JSONB
                and is retained for ``audit_log_retention_days``, so a password,
                a token or a hash of either written here is a secret that cannot
                be un-leaked. Nothing is filtered on the way in, because a
                redaction list eventually misses the one field that matters.

        Returns:
            The stored row, or ``None`` if the write failed for any reason.
        """
        try:
            return await self.repository.record(
                user_id=user_id,
                event_type=validate_audit_event(event).value,
                ip_address=truncate_ip_address(ip_address),
                user_agent=truncate_user_agent(user_agent),
                metadata=metadata,
            )
        except Exception:
            logger.warning(
                "audit_write_failed",
                exc_info=True,
                extra={"event_type": str(event), "user_id": str(user_id) if user_id else None},
            )
            return None
