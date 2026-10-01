"""The shared error envelope, and what must never appear in a response body."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

#: Fragments that would mean the server leaked internals to the client.
FORBIDDEN_FRAGMENTS = (
    "traceback",
    "psycopg",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    'file "',
    "select ",
    "insert into",
    "delete from",
    "users.email",
    "hashed_password",
    "app.core",
    "app.services",
    "app.repositories",
)


def assert_no_internals(response) -> None:
    """Fail if the body carries a stack trace, a SQL fragment or an internal path."""
    body = response.text.lower()
    leaked = [fragment for fragment in FORBIDDEN_FRAGMENTS if fragment in body]
    assert not leaked, f"response body leaked {leaked}: {response.text}"


def assert_json(response) -> None:
    assert response.headers["content-type"].startswith("application/json")


async def test_unknown_path_returns_the_error_envelope(client, assert_error_envelope):
    response = await client.get("/api/v1/nope")

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert_json(response)
    assert error["details"] is None
    assert_no_internals(response)


async def test_wrong_method_returns_a_method_not_allowed_code(client, assert_error_envelope):
    response = await client.delete("/api/v1/health")

    assert_error_envelope(response, status_code=405, code="method_not_allowed")
    assert_no_internals(response)


async def test_malformed_json_returns_a_validation_envelope(client, assert_error_envelope):
    response = await client.post(
        "/api/v1/auth/login",
        content=b'{"email": "ada@nexus.dev", "password": ',
        headers={"Content-Type": "application/json"},
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["details"]["errors"]
    assert_no_internals(response)


async def test_wrong_field_types_return_a_validation_envelope(client, assert_error_envelope):
    response = await client.post("/api/v1/auth/login", json={"email": 42, "password": ["nope"]})

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert {entry["field"] for entry in error["details"]["errors"]} == {"email", "password"}
    assert_no_internals(response)


async def test_missing_body_returns_a_validation_envelope(client, assert_error_envelope):
    response = await client.post("/api/v1/auth/login")

    assert_error_envelope(response, status_code=422, code="validation_error")
    assert_no_internals(response)


@pytest.mark.parametrize(
    ("method", "path", "payload", "status_code", "code"),
    [
        ("GET", "/api/v1/definitely-not-a-route", None, 404, "not_found"),
        ("GET", "/api/v1/auth/me", None, 401, "unauthorized"),
        (
            "POST",
            "/api/v1/auth/login",
            {"email": "nobody@nexus.dev", "password": "whatever-pass"},
            401,
            "unauthorized",
        ),
        ("POST", "/api/v1/auth/login", {"email": "x", "password": ""}, 422, "validation_error"),
        ("POST", "/api/v1/auth/refresh", {"refresh_token": "nonsense"}, 401, "unauthorized"),
        ("POST", "/api/v1/auth/register", {"email": "nope"}, 422, "validation_error"),
    ],
)
async def test_every_failure_uses_the_same_shape(
    client, assert_error_envelope, method, path, payload, status_code, code
):
    response = await client.request(method, path, json=payload)

    error = assert_error_envelope(response, status_code=status_code, code=code)
    assert set(error) == {"code", "message", "details", "request_id"}
    assert_no_internals(response)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/definitely-not-a-route", None),
        ("GET", "/api/v1/auth/me", None),
        ("POST", "/api/v1/auth/refresh", {"refresh_token": "nonsense"}),
        ("POST", "/api/v1/auth/login", {"email": 1, "password": 2}),
    ],
)
async def test_error_codes_are_stable_snake_case(client, method, path, payload):
    response = await client.request(method, path, json=payload)

    code = response.json()["error"]["code"]
    assert code == code.lower()
    assert code.replace("_", "").isalnum()
    assert not code.startswith("internal") or response.status_code >= 500
