"""Access logging: what reaches the log line, and what must never.

Also covers the parts of :func:`app.main.create_app` that only run for
non-default settings — the CORS middleware, the body-capture middleware behind
``log_request_body`` and the docs/OpenAPI URL overrides.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.logging import REDACTED_KEYS
from app.core.middleware import (
    _MAX_CAPTURED_BODY_BYTES,
    _MAX_LOGGED_BODY_CHARS,
    _body_preview,
    _redact_query,
)
from app.main import create_app

REDACTED = "***redacted***"

#: A throwaway value standing in for a real credential; nothing here is a secret.
PLAINTEXT = "correct-horse-battery-staple"

CORS_ORIGIN = "https://console.nexus.test"
OTHER_ORIGIN = "https://evil.nexus.test"
OPENAPI_URL = "/nexus/openapi.json"
DOCS_URL = "/nexus/docs"
REDOC_URL = "/nexus/redoc"

FORM_BODY = f"email=ada@nexus.dev&password={PLAINTEXT}-formencoded&session=abc123"
JSON_BODY = {"email": "ada@nexus.dev", "password": PLAINTEXT, "remember_me": True}


def _settings(**overrides) -> Settings:
    """Deterministic settings, independent of whatever the developer's .env says."""
    return Settings(_env_file=None, environment="test", debug=False, **overrides)


def _app(settings: Settings) -> FastAPI:
    """A NEXUS app plus routes that report exactly what they were handed."""

    async def echo(request: Request) -> dict[str, object]:
        body = await request.body()
        return {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    async def noop() -> dict[str, str]:
        return {"ok": "ok"}

    application = create_app(settings)
    application.add_api_route("/_test/echo", echo, methods=["POST"])
    application.add_api_route("/_test/noop", noop, methods=["GET"])
    return application


@pytest.fixture
def body_logging_app() -> FastAPI:
    return _app(
        _settings(
            log_request_body=True,
            cors_origins=CORS_ORIGIN,
            openapi_url=OPENAPI_URL,
            docs_url=DOCS_URL,
            redoc_url=REDOC_URL,
        )
    )


@pytest.fixture
def plain_app() -> FastAPI:
    return _app(_settings())


def _client(application: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=application)
    return AsyncClient(transport=transport, base_url="http://nexus.test")


@pytest.fixture
async def body_logging_client(body_logging_app) -> AsyncIterator[AsyncClient]:
    async with _client(body_logging_app) as http_client:
        yield http_client


@pytest.fixture
async def plain_client(plain_app) -> AsyncIterator[AsyncClient]:
    async with _client(plain_app) as http_client:
        yield http_client


def _access_record(caplog) -> logging.LogRecord:
    matches = [record for record in caplog.records if record.getMessage() == "request_completed"]
    assert len(matches) == 1, [record.getMessage() for record in caplog.records]
    return matches[0]


# -- Body preview ------------------------------------------------------------


def test_a_json_credential_is_redacted_from_the_preview():
    preview = _body_preview(json.dumps(JSON_BODY).encode())

    assert PLAINTEXT not in preview
    assert json.loads(preview) == {
        "email": "ada@nexus.dev",
        "password": REDACTED,
        "remember_me": True,
    }


def test_nested_credentials_are_redacted_from_the_preview():
    body = {"user": {"password": PLAINTEXT, "roles": ["admin"]}}

    assert json.loads(_body_preview(json.dumps(body).encode()))["user"]["password"] == REDACTED


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(FORM_BODY.encode("utf-8"), id="form-urlencoded"),
        pytest.param(b"-----boundary\r\nname=ada\r\npassword=hunter2\r\n", id="multipart"),
        pytest.param(b'{"password": "unterminated', id="malformed-json"),
    ],
)
def test_a_body_that_key_based_redaction_cannot_walk_is_never_quoted(body):
    """Any wire format that hides credentials from a key-based scrub is summarised."""
    preview = _body_preview(body)

    assert PLAINTEXT not in preview
    assert "hunter2" not in preview
    assert preview == f"<not logged: not a JSON object, {len(body)} bytes>"


def test_a_json_scalar_is_treated_as_a_non_object():
    assert _body_preview(b'"just a string"') == "<not logged: not a JSON object, 15 bytes>"


def test_an_empty_body_logs_nothing():
    assert _body_preview(b"") == ""


def test_a_large_json_body_is_capped_in_the_preview():
    body = json.dumps({"items": [{"n": index} for index in range(500)]}).encode()

    preview = _body_preview(body)

    assert len(preview) <= _MAX_LOGGED_BODY_CHARS + len("...<truncated>")
    assert preview.endswith("...<truncated>")


# -- Query string ------------------------------------------------------------


def test_a_query_credential_is_redacted_but_the_rest_survives():
    assert _redact_query("token=abc123&page=2&sort=name") == f"token={REDACTED}&page=2&sort=name"


@pytest.mark.parametrize("key", sorted(REDACTED_KEYS))
def test_every_declared_key_is_redacted_in_a_raw_query(key):
    assert _redact_query(f"{key}=swordfish") == f"{key}={REDACTED}"


def test_a_query_value_does_not_swallow_the_next_parameter():
    assert _redact_query("session=abc123&page=2") == f"session={REDACTED}&page=2"


# -- The access log ----------------------------------------------------------


async def test_a_logged_request_body_keeps_its_credentials_out_of_the_log(
    body_logging_client, caplog
):
    caplog.set_level(logging.INFO)

    response = await body_logging_client.post(
        "/_test/echo", content=json.dumps(JSON_BODY), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    record = _access_record(caplog)
    assert PLAINTEXT not in caplog.text
    assert json.loads(record.body)["password"] == REDACTED


async def test_a_form_encoded_body_is_never_logged_verbatim(body_logging_client, caplog):
    caplog.set_level(logging.INFO)

    response = await body_logging_client.post(
        "/_test/echo",
        content=FORM_BODY,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    record = _access_record(caplog)
    assert PLAINTEXT not in caplog.text
    assert "hunter2" not in caplog.text
    assert "abc123" not in caplog.text
    assert record.body == f"<not logged: not a JSON object, {len(FORM_BODY)} bytes>"


async def test_a_query_credential_never_reaches_the_access_log(body_logging_client, caplog):
    caplog.set_level(logging.INFO)

    await body_logging_client.get("/_test/noop?token=abc123&page=2")

    # Scoped to the app's own record: httpx logs the request line verbatim, and
    # that sink is not ours to judge.
    record = _access_record(caplog)
    assert record.query == f"token={REDACTED}&page=2"
    assert "abc123" not in record.query


async def test_an_oversized_body_is_capped_in_the_log_but_replayed_in_full(
    body_logging_client, caplog
):
    """The capture limit bounds memory, not what the handler is allowed to read."""
    caplog.set_level(logging.INFO)
    payload = b'{"data": "' + b"x" * (_MAX_CAPTURED_BODY_BYTES + 4096) + b'"}'

    response = await body_logging_client.post(
        "/_test/echo", content=payload, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    record = _access_record(caplog)
    assert record.body == f"<not logged: truncated at the capture limit, {len(payload)} bytes>"


async def test_nothing_is_captured_when_body_logging_is_off(plain_client, caplog):
    caplog.set_level(logging.INFO)

    response = await plain_client.post(
        "/_test/echo", content=json.dumps(JSON_BODY), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    assert "body" not in _access_record(caplog).__dict__
    assert PLAINTEXT not in caplog.text


# -- Non-default settings ----------------------------------------------------


async def test_the_configured_origin_is_allowed(body_logging_client):
    response = await body_logging_client.options(
        "/_test/echo",
        headers={
            "Origin": CORS_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-request-id",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == CORS_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "x-request-id" in response.headers["access-control-allow-headers"].lower()


async def test_an_unlisted_origin_is_not_allowed(body_logging_client):
    response = await body_logging_client.get("/_test/noop", headers={"Origin": OTHER_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_a_simple_request_gets_the_cors_headers_and_the_request_id(body_logging_client):
    """The correlation header must survive the CORS layer, or the browser drops it."""
    response = await body_logging_client.get("/_test/noop", headers={"Origin": CORS_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == CORS_ORIGIN
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]
    assert response.headers["X-Request-ID"]


async def test_the_configured_openapi_document_is_the_one_served(body_logging_client):
    response = await body_logging_client.get(OPENAPI_URL)

    assert response.status_code == 200
    document = response.json()
    assert document["info"]["title"] == "NEXUS API"
    assert "/_test/echo" in document["paths"]
    assert (await body_logging_client.get("/openapi.json")).status_code == 404


async def test_the_configured_docs_page_is_served(body_logging_client):
    response = await body_logging_client.get(DOCS_URL)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert (await body_logging_client.get("/docs")).status_code == 404
    assert (await body_logging_client.get(REDOC_URL)).status_code == 200
    assert (await body_logging_client.get("/redoc")).status_code == 404
