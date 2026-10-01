"""Password and session-security wire models.

Everything that changes or recovers credentials. These schemas are the boundary
for the two flows that are worth more to an attacker than the data itself —
taking over an account and keeping access to it.

The password *policy* vocabulary (:data:`PASSWORD_RULES`,
:func:`password_rule_status`) lives in :mod:`app.schemas.user`, next to the
validator that enforces it, and is re-exported here because this is the module a
client integrating "security" imports from. There is still only one definition:
the objects below and the ones in :mod:`app.schemas.user` are the same objects.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.user import (
    MAX_PASSWORD_LENGTH,
    PASSWORD_RULES,
    EmailAddress,
    Password,
    PasswordRule,
    _EmailNormaliser,
    password_min_length,
    password_rule_status,
)

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "PASSWORD_RULES",
    "PasswordChange",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "PasswordResetRequested",
    "PasswordRule",
    "password_min_length",
    "password_rule_status",
]

#: A reset token is a signed JWT, so it is long. The floor is what keeps a
#: client from posting a truncated or empty value and getting a misleading
#: "expired" answer instead of a validation error.
_MIN_RESET_TOKEN_LENGTH = 20
_MAX_RESET_TOKEN_LENGTH = 512


class PasswordChange(BaseModel):
    """Self-service password change.

    ``current_password`` is required even though the caller already holds a valid
    access token: a token proves a session, not that the person at the keyboard
    owns the account. That is the difference between changing a password and
    being handed one by someone who found an unlocked machine.
    """

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: Password


class PasswordResetRequest(_EmailNormaliser, BaseModel):
    """Ask for a reset link.

    Only an address goes out — never the fact that an account exists, and never
    the token. The service answers with the same body whether or not the address
    is registered, so this endpoint cannot be used to enumerate accounts.
    """

    email: EmailAddress


class PasswordResetConfirm(BaseModel):
    """Redeem a reset token and set a new password."""

    token: str = Field(min_length=_MIN_RESET_TOKEN_LENGTH, max_length=_MAX_RESET_TOKEN_LENGTH)
    new_password: Password


class PasswordResetRequested(BaseModel):
    """Acknowledgement that a reset was requested.

    ``dev_token`` is populated ONLY outside production, where there is no mail
    service to deliver the link — a local-first install has to be able to complete
    its own account recovery. It carries the raw reset token, which is a bearer
    credential for a full account takeover, so the router MUST leave it ``None``
    whenever ``settings.is_production``; the field exists in the schema so the
    OpenAPI contract does not change shape between environments, not because it
    is safe to send anywhere.

    ``accepted`` is always true when this body is returned at all: an unknown
    address is answered with the identical body, so a false here would be a
    signal that the address is not registered.
    """

    accepted: bool = True
    dev_token: str | None = Field(
        default=None,
        description="Raw reset token, non-production only. Always null in production.",
    )
