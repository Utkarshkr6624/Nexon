"""Password hashing and JWT issuance/verification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    new_session_id,
    token_fingerprint_matches,
    verify_password,
)
from app.models.user import User
from app.schemas.user import TokenPair
from app.services.auth_service import AuthService

PASSWORD = "correct-horse-battery-staple"
SUBJECT = uuid.uuid4()

STRONG_SECRET = "k7Qm2Zt9XpL4vR8sN6bY3wJ1hG5dF0cA7eU9iO2pS4tV6xZ8mB1nC3qW5eR7yT9u"


@pytest.fixture(scope="module")
def security_settings() -> Settings:
    """Deterministic settings, independent of whatever the developer's .env says."""
    return Settings(
        _env_file=None,
        secret_key=STRONG_SECRET,
        jwt_algorithm="HS256",
        access_token_expire_minutes=30,
        refresh_token_expire_days=3,
    )


# -- Passwords ---------------------------------------------------------------


def test_password_hash_round_trip():
    hashed = hash_password(PASSWORD)

    assert hashed != PASSWORD
    assert PASSWORD not in hashed
    assert hashed.startswith(("$2a$", "$2b$", "$2y$"))
    assert verify_password(PASSWORD, hashed)


def test_password_hash_is_salted():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_wrong_password_is_rejected():
    hashed = hash_password(PASSWORD)

    assert verify_password("correct-horse-battery-stapl", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(PASSWORD.upper(), hashed) is False


def test_verifying_against_a_corrupt_hash_returns_false_instead_of_raising():
    assert verify_password(PASSWORD, "not-a-bcrypt-hash") is False
    assert verify_password(PASSWORD, "") is False


def test_hashing_an_empty_password_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")


# -- Token digests -----------------------------------------------------------


def test_hash_token_is_a_64_character_hex_sha256():
    """The digest is exactly as wide as the column.

    ``sessions.token_hash`` and ``password_reset_tokens.token_hash`` declare
    ``CHAR(64)``, so a different length would not fit the column.
    """
    digest = hash_token("a.b.c")

    assert len(digest) == 64
    assert digest == digest.lower()
    assert int(digest, 16) >= 0  # pure hex
    assert digest.isalnum()


def test_hash_token_is_deterministic():
    """Lookup is by digest, so the same token must always hash to the same value."""
    assert hash_token("a.b.c") == hash_token("a.b.c")


def test_hash_token_is_not_the_token_and_cannot_be_reversed():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZGQifQ.signature"

    digest = hash_token(token)

    assert digest != token
    assert token not in digest
    # SHA-256, not bcrypt: the input is 256 bits of randomness, so there is no
    # dictionary to slow an attacker down and a work factor would only add
    # latency to every request that reads a session row.
    assert not digest.startswith(("$2a$", "$2b$", "$2y$"))
    assert hash_token("a.b.c") != hash_token("a.b.d")


def test_hash_token_handles_an_empty_token():
    """Hashing must not raise on input it could be handed from a header."""
    assert len(hash_token("")) == 64


def test_token_fingerprint_matches_accepts_the_matching_pair():
    token = "a.b.c"

    assert token_fingerprint_matches(token, hash_token(token)) is True


def test_token_fingerprint_matches_rejects_a_mismatch():
    stored = hash_token("the-real-token")

    assert token_fingerprint_matches("a-different-token", stored) is False
    assert token_fingerprint_matches("the-real-token", hash_token("other")) is False


@pytest.mark.parametrize(
    ("token", "stored"),
    [
        ("", hash_token("a.b.c")),
        ("a.b.c", ""),
        ("", ""),
        ("a.b.c", "not-a-digest"),
        ("a.b.c", None),
        (None, hash_token("a.b.c")),
        ("a.b.c", hash_token("a.b.c").upper()),
    ],
)
def test_token_fingerprint_matches_denys_bad_input_instead_of_raising(token, stored):
    """Never raises.

    A corrupt or empty stored value has to deny the request, not turn it into a
    500: the whole point of the comparison is to be the last thing standing
    between a bad token and a session.
    """
    assert token_fingerprint_matches(token, stored) is False


# -- Session ids -------------------------------------------------------------


def test_new_session_id_is_a_parseable_uuid4():
    parsed = uuid.UUID(new_session_id())

    assert parsed.version == 4


def test_new_session_id_is_returned_as_a_string():
    """It travels in a JWT claim, where everything is a string."""
    raw = new_session_id()

    assert isinstance(raw, str)
    assert str(uuid.UUID(raw)) == raw


def test_two_session_ids_differ():
    ids = {new_session_id() for _ in range(100)}

    assert len(ids) == 100


# -- Tokens ------------------------------------------------------------------


def test_access_token_carries_the_expected_claims(security_settings):
    token = create_access_token(SUBJECT, settings=security_settings)

    claims = jwt.decode(token, STRONG_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["sub"] == str(SUBJECT)
    assert claims["type"] == ACCESS_TOKEN_TYPE
    assert claims["iat"] <= claims["exp"]
    assert claims["nbf"] <= claims["exp"]


def test_refresh_token_is_typed_as_refresh(security_settings):
    token = create_refresh_token(SUBJECT, settings=security_settings)

    assert decode_token(token, settings=security_settings).token_type == REFRESH_TOKEN_TYPE


def test_decode_token_returns_the_subject_and_lifetimes(security_settings):
    token = create_access_token(SUBJECT, settings=security_settings)

    data = decode_token(token, settings=security_settings)

    assert data.subject == str(SUBJECT)
    assert data.token_type == TokenType.ACCESS
    assert data.expires_at is not None and data.expires_at > data.issued_at
    assert data.expires_at.tzinfo is not None


def test_the_default_access_token_lifetime_follows_the_settings(security_settings):
    before = datetime.now(UTC)

    data = decode_token(
        create_access_token(SUBJECT, settings=security_settings),
        settings=security_settings,
    )

    expected = timedelta(minutes=security_settings.access_token_expire_minutes)
    assert expected - timedelta(seconds=5) < data.expires_at - before < expected


def test_the_default_refresh_token_lifetime_follows_the_settings(security_settings):
    before = datetime.now(UTC)

    data = decode_token(
        create_refresh_token(SUBJECT, settings=security_settings),
        settings=security_settings,
    )

    expected = timedelta(days=security_settings.refresh_token_expire_days)
    assert expected - timedelta(seconds=5) < data.expires_at - before < expected


def test_an_explicit_lifetime_overrides_the_setting(security_settings):
    data = decode_token(
        create_access_token(
            SUBJECT, settings=security_settings, expires_delta=timedelta(seconds=5)
        ),
        settings=security_settings,
    )

    assert data.expires_at - data.issued_at == timedelta(seconds=5)


def test_an_expired_token_is_rejected(security_settings):
    token = create_access_token(
        SUBJECT, settings=security_settings, expires_delta=timedelta(seconds=-5)
    )

    with pytest.raises(UnauthorizedError, match="expired"):
        decode_token(token, settings=security_settings)


def test_a_token_typed_as_access_cannot_be_used_to_refresh(security_settings):
    token = create_access_token(SUBJECT, settings=security_settings)

    with pytest.raises(UnauthorizedError, match="requires a refresh token"):
        decode_token(token, expected_type=TokenType.REFRESH, settings=security_settings)


def test_a_token_typed_as_refresh_cannot_be_used_as_a_credential(security_settings):
    token = create_refresh_token(SUBJECT, settings=security_settings)

    with pytest.raises(UnauthorizedError, match="requires a access token"):
        decode_token(token, expected_type=TokenType.ACCESS, settings=security_settings)


def test_a_tampered_signature_is_rejected(security_settings):
    token = create_access_token(SUBJECT, settings=security_settings)
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"

    with pytest.raises(UnauthorizedError, match="invalid"):
        decode_token(tampered, settings=security_settings)


def test_a_tampered_payload_is_rejected(security_settings):
    """Re-encoding a token's claims with a foreign key must not produce a valid token."""
    token = create_access_token(SUBJECT, settings=security_settings)
    claims = jwt.decode(token, STRONG_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    claims["sub"] = str(uuid.uuid4())
    forged = jwt.encode(claims, "y" * 48, algorithm="HS256")

    with pytest.raises(UnauthorizedError, match="invalid"):
        decode_token(forged, settings=security_settings)
    # Sanity: the original token is unaffected by the tampering above.
    assert decode_token(token, settings=security_settings).subject == str(SUBJECT)


def test_a_token_signed_with_another_secret_is_rejected(security_settings):
    other = Settings(_env_file=None, secret_key="x" * 48)
    token = create_access_token(SUBJECT, settings=other)

    with pytest.raises(UnauthorizedError, match="invalid"):
        decode_token(token, settings=security_settings)


def test_malformed_input_is_rejected(security_settings):
    for candidate in ("", "not-a-jwt", "a.b.c", "....", None):
        with pytest.raises(UnauthorizedError):
            decode_token(candidate, settings=security_settings)


def test_an_unsigned_alg_none_token_is_rejected(security_settings):
    forged = jwt.encode(
        {"sub": str(SUBJECT), "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
        key="",
        algorithm="none",
    )

    with pytest.raises(UnauthorizedError, match="invalid"):
        decode_token(forged, settings=security_settings)


def test_extra_claims_cannot_override_the_reserved_ones(security_settings):
    token = create_access_token(
        SUBJECT,
        settings=security_settings,
        extra_claims={"sub": str(uuid.uuid4()), "type": "superuser", "jti": "abc"},
    )

    data = decode_token(token, settings=security_settings)

    assert data.subject == str(SUBJECT)
    assert data.token_type == ACCESS_TOKEN_TYPE
    assert data.claims["jti"] == "abc"


def test_a_caller_supplied_token_id_reaches_the_claims(security_settings):
    token = create_access_token(
        SUBJECT, settings=security_settings, extra_claims={"jti": "token-id-1"}
    )

    assert decode_token(token, settings=security_settings).claims["jti"] == "token-id-1"


def test_tokens_minted_for_the_same_subject_are_deterministic_without_a_jti(
    security_settings,
):
    """Documents why the auth service always supplies a ``jti``.

    The built-in claims carry only second-resolution timestamps, so two tokens
    minted for the same subject inside one clock second are byte-identical. That
    is harmless only because ``AuthService.issue_token_pair`` adds a ``jti``;
    without one, rotation and the revocation denylist could not tell two
    otherwise identical tokens apart.
    """
    first = create_access_token(SUBJECT, settings=security_settings)
    second = create_access_token(SUBJECT, settings=security_settings)

    assert first == second
    assert "jti" not in decode_token(first, settings=security_settings).claims


def test_the_auth_service_mints_distinguishable_tokens(security_settings):
    """The pair issued by the API always carries distinct, revocable ids."""
    service = AuthService(repository=None, settings=security_settings)
    user = User(id=SUBJECT)

    pair = service.issue_token_pair(user)
    other = service.issue_token_pair(user)

    assert isinstance(pair, TokenPair)
    access_claims = decode_token(pair.access_token, settings=security_settings).claims
    refresh_claims = decode_token(pair.refresh_token, settings=security_settings).claims
    assert access_claims["jti"] != refresh_claims["jti"]
    assert pair.access_token != other.access_token
    assert pair.expires_in == security_settings.access_token_expire_minutes * 60
