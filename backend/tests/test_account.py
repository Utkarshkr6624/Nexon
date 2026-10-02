"""Account deletion, and what the database does to the rows around it.

**Every test here requires a live PostgreSQL and has NOT been executed.** They
are marked ``integration``.

Two database-level decisions are being locked down:

* ``sessions.user_id`` is ``ON DELETE CASCADE`` — a device sign-in is part of
  the account and is meaningless without it, so it goes.
* ``audit_logs.user_id`` is ``ON DELETE SET NULL`` — an audit row is evidence
  that must outlive the account it describes, or a trail that vanished along
  with the thing it describes could not be used to investigate that deletion.

The second is the one worth a test; the first is asserted too so that a change
in either direction has to be deliberate.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_token
from app.models.audit import AuditEvent, AuditLog
from app.models.session import Session
from app.models.user import User

pytestmark = pytest.mark.integration

ADA = {
    "username": "ada",
    "email": "ada@nexus.dev",
    "password": "Correct-Horse-7",
}
GRACE = {
    "username": "grace",
    "email": "grace@nexus.dev",
    "password": "Another-Strong-Pass-9",
}

#: The only keys any caller is allowed to put in ``audit_logs.metadata``.
ALLOWED_METADATA_KEYS = {
    "username",
    "session_id",
    "revoked",
    "kept",
    "revoked_sessions",
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _delete_account(
    client,
    *,
    body: dict | None = None,
    token: str | None = None,
):
    """``DELETE /api/v1/users/me``, carrying a confirmation body.

    ``httpx.AsyncClient.delete`` is a convenience wrapper with no ``json=``
    parameter, so the body-carrying form is ``AsyncClient.request``. The route
    takes a ``UserDeletion`` body on DELETE, so every call in this file has to go
    through it; without that, the request is refused as a validation error
    before the handler runs and the test is asserting against a body that never
    arrived. Kept as a helper so the eleven call sites below read as the
    behaviour they are about rather than as httpx plumbing.
    """
    return await client.request(
        "DELETE",
        "/api/v1/users/me",
        json=body,
        headers=_bearer(token) if token else None,
    )


async def _register(client, payload: dict = ADA) -> dict:
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client, payload: dict = ADA) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _all_audit_rows(db_session) -> list[AuditLog]:
    return list((await db_session.execute(select(AuditLog))).scalars().all())


# -- The confirmation gate ---------------------------------------------------


async def test_deletion_requires_a_confirmation(client, assert_error_envelope):
    """``confirm=false`` is refused, and the account survives."""
    await _register(client)
    tokens = await _login(client)

    response = await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": False},
        token=tokens["access_token"],
    )

    assert_error_envelope(response, status_code=422, code="validation_error")
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 200


async def test_deletion_requires_a_password(client, assert_error_envelope):
    """Possession of a token is not sufficient authority.

    A token left in a shared browser is enough to *read* an account; it is not
    enough to destroy one.
    """
    await _register(client)
    tokens = await _login(client)

    response = await _delete_account(client, body={"confirm": True}, token=tokens["access_token"])

    assert_error_envelope(response, status_code=422, code="validation_error")
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 200


async def test_deletion_rejects_the_wrong_password(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)

    response = await _delete_account(
        client,
        body={"password": "not-the-password", "confirm": True},
        token=tokens["access_token"],
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 200


async def test_deletion_requires_a_bearer_token(client, assert_error_envelope):
    await _register(client)

    response = await _delete_account(client, body={"password": ADA["password"], "confirm": True})

    assert_error_envelope(response, status_code=401, code="unauthorized")


# -- The deletion itself -----------------------------------------------------


async def test_deletion_removes_the_account(client, db_session):
    user = await _register(client)
    tokens = await _login(client)

    response = await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )

    assert response.status_code == 204, response.text
    assert response.content == b""
    result = await db_session.execute(select(User).where(User.id == uuid.UUID(user["id"])))
    assert result.scalar_one_or_none() is None


async def test_deletion_cascades_to_the_sessions(client, db_session):
    """Every device is signed out by the database, with no second pass."""
    await _register(client)
    first = await _login(client)
    second = await _login(client)

    await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=second["access_token"],
    )

    remaining = await db_session.execute(
        select(Session).where(
            Session.id.in_([uuid.UUID(first["session_id"]), uuid.UUID(second["session_id"])])
        )
    )
    assert list(remaining.scalars().all()) == []


async def test_deletion_leaves_the_audit_trail_with_a_null_user(client, db_session):
    """The specific design decision worth locking down.

    ``ON DELETE SET NULL`` on ``audit_logs.user_id``: an account's security
    history has to outlive the account, or an investigation into the deletion
    could not use it.
    """
    user = await _register(client)
    await _login(client)
    user_id = uuid.UUID(user["id"])

    before = await db_session.execute(select(AuditLog).where(AuditLog.user_id == user_id))
    assert list(before.scalars().all()), "there is a trail to survive"

    tokens = await _login(client)
    response = await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )
    assert response.status_code == 204

    survivors = list(await _all_audit_rows(db_session))
    assert survivors, "the audit rows must survive the account"
    assert all(row.user_id is None for row in survivors)
    assert {str(row.event_type) for row in survivors} >= {
        AuditEvent.USER_REGISTERED.value,
        AuditEvent.USER_LOGIN.value,
    }


async def test_deletion_does_not_touch_another_account(client, db_session):
    await _register(client, ADA)
    await _register(client, GRACE)
    ada = await _login(client, ADA)
    await _login(client, GRACE)

    await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=ada["access_token"],
    )

    result = await db_session.execute(select(User).where(User.email == GRACE["email"]))
    assert result.scalar_one_or_none() is not None


async def test_a_deleted_account_can_no_longer_sign_in(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)
    await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )

    assert_error_envelope(
        await client.post("/api/v1/auth/login", json=ADA), status_code=401, code="unauthorized"
    )
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])),
        status_code=401,
        code="unauthorized",
    )


async def test_the_username_is_reusable_after_the_account_is_deleted(client):
    """The unique index only has to be satisfied at any one moment."""
    await _register(client)
    tokens = await _login(client)
    await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )

    response = await client.post("/api/v1/auth/register", json=ADA)

    assert response.status_code == 201, response.text


async def test_a_deleted_account_answers_401_on_a_repeated_deletion(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)
    await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )

    response = await _delete_account(
        client,
        body={"password": ADA["password"], "confirm": True},
        token=tokens["access_token"],
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")


# -- Audit hygiene -----------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/password/forgot",
    ],
)
async def test_no_audit_row_metadata_contains_a_credential(client, db_session, route):
    """The audit trail never holds a credential.

    It is written verbatim into JSONB, exported into logs, and retained for 400
    days. Nothing is filtered on the way in, because a redaction list eventually
    misses the one field that matters — so no caller may put one there.
    """
    await _register(client)
    if route.endswith("register"):
        payload = {"username": "second", "email": "second@nexus.dev", "password": "Another-Pass-4"}
    elif route.endswith("login"):
        payload = {"email": ADA["email"], "password": ADA["password"]}
    else:
        payload = {"email": ADA["email"]}

    response = await client.post(route, json=payload)
    assert response.status_code in (200, 201, 202), response.text

    rows = await _all_audit_rows(db_session)
    assert rows, "the request wrote at least one audit row"

    forbidden = (
        ADA["password"],
        "Another-Pass-4",
        hash_token(ADA["password"]),
    )
    for row in rows:
        rendered = repr(row.metadata_)
        for secret in forbidden:
            assert secret not in rendered, f"{row.event_type} leaked a credential"
        assert "token" not in rendered.lower()


async def test_the_audit_metadata_carries_only_non_sensitive_context(client, db_session):
    """The permitted keys are a closed vocabulary.

    They are the ones the implementation writes today, so a new key that
    smuggles something in shows up as a visible change rather than as new data.
    """
    await _register(client)
    await _login(client)

    for row in await _all_audit_rows(db_session):
        assert set(row.metadata_) <= ALLOWED_METADATA_KEYS, row.event_type
