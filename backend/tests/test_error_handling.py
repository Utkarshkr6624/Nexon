"""The 5xx path: what a client may see, and what must never reach it.

Every test here needs a route that actually fails, so it builds its own app and
a client with ``raise_app_exceptions=False`` — the framework's ``Exception``
handler re-raises after rendering, so the default transport would never show the
response the user would have received.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from tests.test_errors import assert_no_internals

#: The message a client is contractually allowed to see for a server-side fault.
INTERNAL_ERROR_MESSAGE = "An internal server error occurred."

#: What a failing handler has to hand: a driver message, the query behind it and
#: the column it failed on. None of it may survive into a response body.
LEAKY_DETAIL = (
    'duplicate key value violates unique constraint "users_email_key"; '
    "SELECT users.email FROM users WHERE email = $1"
)

#: Fragments specific to this file's failure, checked alongside the suite-wide
#: ``FORBIDDEN_FRAGMENTS`` — the generic list cannot know what a given handler
#: happened to have in hand.
LEAKED_IF_UNHANDLED = (LEAKY_DETAIL, "RuntimeError", "users_email_key", "SELECT users", "Traceback")


@pytest.fixture
def failing_app() -> FastAPI:
    """A small app with routes that fail in each of the shapes a handler can."""
    application = create_app(Settings(_env_file=None, environment="test", debug=False))

    @application.get("/_test/unhandled")
    async def unhandled() -> None:
        raise RuntimeError(LEAKY_DETAIL)

    @application.get("/_test/http-500")
    async def http_500() -> None:
        raise HTTPException(status_code=500, detail=LEAKY_DETAIL)

    @application.get("/_test/http-402")
    async def http_402() -> None:
        raise HTTPException(status_code=402, detail="Payment Required")

    @application.get("/_test/http-413")
    async def http_413() -> None:
        raise HTTPException(status_code=413, detail="Payload Too Large")

    @application.get("/_test/http-415")
    async def http_415() -> None:
        raise HTTPException(status_code=415, detail="Unsupported Media Type")

    return application


@pytest.fixture
async def failing_client(failing_app) -> AsyncIterator[AsyncClient]:
    """A non-raising client over the failing app, so the rendered 500 is visible."""
    transport = ASGITransport(app=failing_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


def _record(caplog, message: str) -> logging.LogRecord:
    """The single record logged under ``message``."""
    matches = [record for record in caplog.records if record.getMessage() == message]
    assert len(matches) == 1, [record.getMessage() for record in caplog.records]
    return matches[0]


# -- The catch-all -----------------------------------------------------------


async def test_an_unhandled_exception_renders_the_internal_error_envelope(
    failing_client, assert_error_envelope
):
    response = await failing_client.get("/_test/unhandled")

    error = assert_error_envelope(response, status_code=500, code="internal_error")
    assert error["message"] == INTERNAL_ERROR_MESSAGE
    assert error["details"] is None


async def test_the_500_body_carries_nothing_from_the_failure(failing_client):
    response = await failing_client.get("/_test/unhandled")

    body = response.text
    assert_no_internals(response)
    leaked = [fragment for fragment in LEAKED_IF_UNHANDLED if fragment in body]
    assert not leaked, f"500 body leaked {leaked}: {body}"
    assert response.headers["content-type"].startswith("application/json")


async def test_the_traceback_is_kept_server_side(failing_client, caplog):
    """The detail has to reach the log even though it never reaches the client."""
    caplog.set_level(logging.ERROR)

    response = await failing_client.get("/_test/unhandled")

    assert response.status_code == 500
    record = _record(caplog, "unhandled_exception")
    assert record.exception_type == "RuntimeError"
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == LEAKY_DETAIL


# -- X-Request-ID on 5xx -----------------------------------------------------


async def test_a_500_carries_the_request_id_and_the_body_agrees(failing_client):
    """The header is stamped by the outermost middleware, below the 500 renderer."""
    response = await failing_client.get("/_test/unhandled")

    assert response.status_code == 500
    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id)
    assert response.json()["error"]["request_id"] == request_id


async def test_the_logged_500_is_correlatable_by_the_same_request_id(failing_client, caplog):
    caplog.set_level(logging.ERROR)

    response = await failing_client.get("/_test/unhandled")

    record = _record(caplog, "unhandled_exception")
    assert record.request_id == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "header", ["X-Request-ID", "X-Correlation-ID"], ids=["request-id", "correlation-id"]
)
async def test_a_caller_supplied_id_is_honoured_on_the_5xx_path(failing_client, header):
    supplied = "0f1e2d3c-4b5a-4968-8776-655443332211"

    response = await failing_client.get("/_test/unhandled", headers={header: supplied})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == supplied
    assert response.json()["error"]["request_id"] == supplied


async def test_an_over_long_inbound_id_is_capped(failing_client):
    """An attacker-supplied id must not be able to grow a log line without bound."""
    supplied = "x" * 500

    response = await failing_client.get("/_test/unhandled", headers={"X-Request-ID": supplied})

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 128
    assert response.json()["error"]["request_id"] == request_id


async def test_an_inbound_id_cannot_forge_a_log_line(failing_client):
    supplied = "\n  forged-entry  \n"

    response = await failing_client.get("/_test/unhandled", headers={"X-Request-ID": supplied})

    request_id = response.headers["X-Request-ID"]
    assert request_id == "forged-entry"
    assert "\n" not in request_id
    assert response.json()["error"]["request_id"] == request_id


# -- Caller-supplied detail --------------------------------------------------


async def test_a_5xx_http_exception_does_not_echo_its_detail(failing_client, assert_error_envelope):
    response = await failing_client.get("/_test/http-500")

    error = assert_error_envelope(response, status_code=500, code="internal_error")
    assert error["message"] == INTERNAL_ERROR_MESSAGE
    assert error["details"] is None
    assert LEAKY_DETAIL not in response.text
    assert_no_internals(response)


async def test_a_5xx_http_exception_still_records_its_detail(failing_client, caplog):
    caplog.set_level(logging.ERROR)

    response = await failing_client.get("/_test/http-500")

    assert response.status_code == 500
    assert _record(caplog, "http_exception").detail == LEAKY_DETAIL


@pytest.mark.parametrize(
    ("path", "detail"),
    [("/_test/http-413", "Payload Too Large"), ("/_test/http-415", "Unsupported Media Type")],
)
async def test_a_4xx_http_exception_still_returns_its_detail(failing_client, path, detail):
    response = await failing_client.get(path)

    error = response.json()["error"]
    assert error["message"] == detail
    assert error["details"] is None
    assert error["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    ("path", "status_code"),
    [("/_test/http-402", 402), ("/_test/http-413", 413), ("/_test/http-415", 415)],
)
async def test_an_unmapped_status_is_reported_as_a_client_error(
    failing_client, assert_error_envelope, path, status_code
):
    """413/415/402 are the caller's fault, so blaming the server is a lie.

    The frontend branches on ``code``; an ``internal_error`` on a 4xx would send
    every oversized upload or wrong content type down the server-error path.
    """
    response = await failing_client.get(path)

    assert_error_envelope(response, status_code=status_code, code="bad_request")
