"""Password hashing, token fingerprinting and JWT issuance/verification.

Tokens are HS256 JWTs signed with ``settings.secret_key``. The claims this
module always sets are ``sub`` (the user id), ``type`` (``access`` or
``refresh``), ``iat``, ``nbf`` and ``exp``; callers add their own through
``extra_claims``. The ``type`` claim is what stops a refresh token from being
replayed as a bearer credential on regular endpoints. Refresh tokens are
expected to carry a ``sid`` claim (the ``sessions.id`` they belong to) and a
``jti`` claim (unique per token, used to revoke one token without revoking its
whole session).

The identical-token hazard
--------------------------
Every input to the signature is derived from the subject, the token type and a
timestamp with one-second resolution. Two refresh tokens minted for the same
subject in the same clock second, with no ``extra_claims``, are byte-identical:
the second one is not a new credential, it is a copy of the first. That is why
``AuthService`` always supplies a unique ``jti``, and why ``sid`` is mandatory
in practice — a token without them cannot be individually revoked and cannot
be attributed to a device.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import bcrypt
import jwt

from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError

__all__ = [
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
    "TokenData",
    "TokenType",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "hash_token",
    "new_session_id",
    "token_fingerprint_matches",
    "verify_password",
]

#: bcrypt hashes at most this many bytes of input and errors on longer input.
_BCRYPT_MAX_BYTES = 72

#: bcrypt work factor. Deliberately above the library default: 12 is ~250 ms
#: on commodity hardware, which is the intended trade-off against brute force.
_BCRYPT_ROUNDS = 12


class TokenType(StrEnum):
    """The two kinds of token this system issues."""

    ACCESS = "access"
    REFRESH = "refresh"


ACCESS_TOKEN_TYPE = TokenType.ACCESS.value
REFRESH_TOKEN_TYPE = TokenType.REFRESH.value


@dataclass(frozen=True, slots=True)
class TokenData:
    """A verified token's claims."""

    subject: str
    token_type: str
    expires_at: datetime | None
    issued_at: datetime | None
    claims: dict[str, object]


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    if not password:
        raise ValueError("password must not be empty")
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode(
        "ascii"
    )


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored hash, in constant time.

    Returns ``False`` for a missing or corrupt hash rather than raising, so a
    bad row in the database cannot turn a login attempt into a 500.
    """
    if not password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_bcrypt_bytes(password), hashed_password.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return False


def hash_token(token: str) -> str:
    """Return the 64-character hex SHA-256 digest of ``token``.

    Used to store refresh and password-reset tokens. SHA-256 rather than
    bcrypt, unlike :func:`hash_password`, because the input is a 256-bit
    cryptographically random signed token, not a low-entropy secret a user
    chose and can guess: there is no dictionary to slow an attacker down, so a
    work factor buys nothing and costs ~250 ms on every request that touches a
    session row. The raw token is never persisted and never logged, so a
    database leak yields digests that cannot be replayed against the API — the
    attacker would have to recover the preimage of a 256-bit value.

    Args:
        token: The raw token, exactly as handed to the client.

    Returns:
        Lowercase hex digest, safe to store in a ``CHAR(64)`` column.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_fingerprint_matches(token: str, stored_hash: str) -> bool:
    """Whether ``token`` digests to ``stored_hash``, compared in constant time.

    ``hmac.compare_digest`` rather than ``==``: an ordinary string comparison
    short-circuits on the first differing byte, and the length and timing of
    that difference is a (weak, but free to remove) oracle for a stored digest.

    Args:
        token: The raw token presented by the client.
        stored_hash: The digest previously produced by :func:`hash_token`.

    Returns:
        ``True`` on a match. Never raises, so a corrupt or empty stored value
        denies the request instead of turning it into a 500.
    """
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def new_session_id() -> str:
    """Return a fresh session id as a string.

    The single place session ids are minted, so the "sessions are identified by
    a random UUID" rule is stated once rather than at every call site. Returned
    as a string because it travels in a JWT claim, where everything is a string;
    the repository coerces it back to ``uuid.UUID`` for the database.
    """
    return str(uuid.uuid4())


def create_access_token(
    subject: str | uuid.UUID,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    """Issue a short-lived access token."""
    settings = settings or get_settings()
    lifetime = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    return _encode(subject, TokenType.ACCESS, lifetime, settings, extra_claims)


def create_refresh_token(
    subject: str | uuid.UUID,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    """Issue a long-lived refresh token.

    Callers MUST pass ``extra_claims={"sid": ..., "jti": ...}``.

    ``sid`` is the string form of the ``sessions.id`` UUID this token belongs
    to; it is how the API identifies which device session is presenting it, and
    without it a token cannot be revoked by signing the session out or
    invalidated on logout. ``jti`` must be unique for every token ever minted,
    including the rotations of an existing session: two refresh tokens for the
    same subject with no distinct ``jti`` issued in the same clock second are
    byte-identical, so revoking one would silently revoke the other and a
    "revoke this device" call could take out a second device's session.

    Both claims pass through the filter in :func:`_encode` untouched; ``sub``,
    ``type``, ``iat``, ``nbf`` and ``exp`` remain owned by this module.
    """
    settings = settings or get_settings()
    lifetime = expires_delta or timedelta(days=settings.refresh_token_expire_days)
    return _encode(subject, TokenType.REFRESH, lifetime, settings, extra_claims)


def decode_token(
    token: str,
    *,
    expected_type: TokenType | str | None = None,
    settings: Settings | None = None,
) -> TokenData:
    """Verify a JWT and return its claims.

    Raises :class:`UnauthorizedError` for any failure — bad signature, wrong
    algorithm, expiry, malformed input — so callers never have to reason about
    which library exception occurred.
    """
    settings = settings or get_settings()
    if not token or not isinstance(token, str):
        raise UnauthorizedError("Authentication credentials were not provided.")

    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("The authentication token has expired.") from None
    except jwt.InvalidTokenError:
        raise UnauthorizedError("The authentication token is invalid.") from None

    subject = claims.get("sub")
    token_type = claims.get("type")
    if not isinstance(subject, str) or not subject:
        raise UnauthorizedError("The authentication token is invalid.")
    if not isinstance(token_type, str):
        raise UnauthorizedError("The authentication token is invalid.")

    if expected_type is not None and not hmac.compare_digest(
        token_type, TokenType(expected_type).value
    ):
        raise UnauthorizedError(f"This endpoint requires a {TokenType(expected_type).value} token.")

    return TokenData(
        subject=subject,
        token_type=token_type,
        expires_at=_as_datetime(claims.get("exp")),
        issued_at=_as_datetime(claims.get("iat")),
        claims=claims,
    )


def _encode(
    subject: str | uuid.UUID,
    token_type: TokenType,
    lifetime: timedelta,
    settings: Settings,
    extra_claims: dict[str, object] | None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(subject),
        "type": token_type.value,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
    }
    if extra_claims:
        # ``sub``/``type``/``exp`` are owned by this module and must not be
        # overridable by a caller-supplied claim bag.
        payload.update({k: v for k, v in extra_claims.items() if k not in payload})
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _bcrypt_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return raw[:_BCRYPT_MAX_BYTES]
    return raw


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    return None
