"""Registration, login, token lifecycle and identity endpoints."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.core.security import create_access_token

pytestmark = pytest.mark.integration

CREDENTIALS = {"email": "ada@nexus.dev", "password": "correct-horse-battery"}
SECOND_USER = {"email": "grace@nexus.dev", "password": "another-strong-pass"}


async def _register(client, payload: dict) -> dict:
    return await client.post("/api/v1/auth/register", json=payload)


async def _tokens(client, payload: dict = CREDENTIALS) -> dict:
    response = await client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_register_returns_201_without_credential_material(client):
    response = await _register(client, {**CREDENTIALS, "full_name": "Ada Lovelace"})

    assert response.status_code == 201, response.text
    user = response.json()
    assert set(user) == {
        "id",
        "email",
        "full_name",
        "is_active",
        "is_verified",
        "is_superuser",
        "created_at",
        "updated_at",
    }
    assert user["email"] == CREDENTIALS["email"]
    assert user["full_name"] == "Ada Lovelace"
    assert user["is_active"] is True
    assert "hashed_password" not in user
    assert CREDENTIALS["password"] not in response.text


async def test_register_rejects_a_duplicate_email(client, assert_error_envelope):
    await _register(client, CREDENTIALS)

    response = await _register(client, {**CREDENTIALS, "full_name": "Someone Else"})

    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert CREDENTIALS["password"] not in error["message"]


async def test_register_normalises_the_email(client):
    response = await _register(client, {"email": "  ADA@Nexus.DEV ", "password": "a-strong-pass"})

    assert response.status_code == 201
    assert response.json()["email"] == "ada@nexus.dev"


async def test_register_rejects_a_short_password(client, assert_error_envelope):
    response = await _register(client, {"email": "shorty@nexus.dev", "password": "abc"})

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert any(entry["field"] == "password" for entry in error["details"]["errors"])


async def test_login_returns_a_token_pair(client):
    await _register(client, CREDENTIALS)

    body = await _tokens(client)

    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"] != body["refresh_token"]


async def test_login_with_the_wrong_password_is_rejected(client, assert_error_envelope):
    await _register(client, CREDENTIALS)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": CREDENTIALS["email"], "password": "not-the-password"},
    )

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    # The message must not reveal whether the address is registered.
    assert "password" not in error["message"].lower() or "incorrect" in error["message"].lower()


async def test_me_returns_the_authenticated_user(client):
    await _register(client, CREDENTIALS)
    tokens = await _tokens(client)

    response = await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))

    assert response.status_code == 200
    assert response.json()["email"] == CREDENTIALS["email"]
    assert "hashed_password" not in response.text


async def test_me_without_a_bearer_token_is_rejected(client, assert_error_envelope):
    response = await client.get("/api/v1/auth/me")

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["request_id"]


async def test_me_with_a_malformed_bearer_token_is_rejected(client, assert_error_envelope):
    response = await client.get("/api/v1/auth/me", headers=_bearer("not.a.jwt"))

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_refresh_rotates_the_pair_and_retires_the_old_token(client, assert_error_envelope):
    await _register(client, CREDENTIALS)
    tokens = await _tokens(client)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 200, response.text
    rotated = response.json()
    assert rotated["access_token"] != tokens["access_token"]
    assert rotated["refresh_token"] != tokens["refresh_token"]

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert_error_envelope(replay, status_code=401, code="unauthorized")


async def test_logout_revokes_the_supplied_tokens(client, assert_error_envelope):
    await _register(client, CREDENTIALS)
    tokens = await _tokens(client)

    response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=_bearer(tokens["access_token"]),
    )

    assert response.status_code == 204
    assert response.content == b""

    rejected = await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert_error_envelope(rejected, status_code=401, code="unauthorized")


async def test_an_access_token_is_not_a_refresh_token(client, assert_error_envelope):
    await _register(client, CREDENTIALS)
    tokens = await _tokens(client)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert "refresh" in error["message"].lower()


async def test_a_refresh_token_is_not_a_bearer_token(client, assert_error_envelope):
    await _register(client, CREDENTIALS)
    tokens = await _tokens(client)

    response = await client.get("/api/v1/auth/me", headers=_bearer(tokens["refresh_token"]))

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert "access" in error["message"].lower()


async def test_a_token_for_an_unknown_account_is_rejected(client, assert_error_envelope):
    orphan = create_access_token(uuid.uuid4(), settings=get_settings())

    response = await client.get("/api/v1/auth/me", headers=_bearer(orphan))

    assert_error_envelope(response, status_code=401, code="unauthorized")
