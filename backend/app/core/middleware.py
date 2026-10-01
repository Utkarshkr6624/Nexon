"""Request-scoped context: correlation ids, timing and access logging.

:func:`add_request_context_middleware` installs :class:`RequestContextMiddleware`
as the outermost middleware, ahead of ``ServerErrorMiddleware``. A middleware
registered the ordinary way always sits *inside* that layer, and the layer is
what turns an unhandled exception into the 500 response — so a middleware
installed there never observes the failure response and cannot stamp
``X-Request-ID`` on it. The correlation id would then be missing from the
header of every 500 while still being present in the error body, defeating the
frontend's error correlation on exactly the requests that need it.

Both the header and the access line therefore work on raw ASGI messages: the
header is stamped onto ``http.response.start`` as it goes out, and the status
code is read back from the same message.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings
from app.core.logging import (
    REDACTED_KEYS,
    get_logger,
    log_event,
    redact,
    request_id_var,
    set_request_id,
)

__all__ = [
    "REQUEST_ID_HEADER",
    "BodyCaptureMiddleware",
    "RequestContextMiddleware",
    "add_request_context_middleware",
]

REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger("app.core.middleware")

#: Inbound header names that identify an upstream trace, most specific first.
_INBOUND_REQUEST_ID_HEADERS = ("x-request-id", "x-correlation-id", "x-trace-id")

#: An inbound correlation id is attacker-controlled, so it is length-capped to
#: keep log lines bounded and free of newline-forged entries.
_MAX_REQUEST_ID_LENGTH = 128

_MAX_LOGGED_BODY_CHARS = 2000

#: Upper bound on the body bytes buffered for the access-log preview. Anything
#: past it is still read and replayed to the handler untouched, but it is never
#: held for logging, so one large upload cannot be turned into unbounded memory.
_MAX_CAPTURED_BODY_BYTES = 256 * 1024

#: Mirrors the marker used by :func:`app.core.logging.redact` for a redacted
#: value, so a scrubbed query string reads the same as a scrubbed payload.
_REDACTED = "***redacted***"

#: ``key=value`` pairs in a raw query string whose key folds onto
#: :data:`REDACTED_KEYS`. The value runs to the next parameter separator, so
#: nothing after the secret is swallowed and nothing inside it survives.
_REDACTED_PARAM_PATTERN = re.compile(rf"(?i)\b({'|'.join(sorted(REDACTED_KEYS))})(\s*=\s*)[^&#]*")


class RequestContextMiddleware:
    """Bind a request id, time the request and emit one access log line.

    Pure ASGI, and deliberately so: the ``X-Request-ID`` header is written onto
    the outgoing response-start message rather than onto a response object, so
    it is present whatever produced that response — including the catch-all 500,
    which ``ServerErrorMiddleware`` renders below this layer.
    """

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.log_request_body = self.settings.log_request_body
        self.slow_request_ms = self.settings.slow_request_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the request id, time the call, and log exactly one line."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = _resolve_request_id(request)
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        # No response started yet means the request failed on its way to one.
        status_code = 500

        async def send_stamped(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_stamped)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            fields: dict[str, Any] = {
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": _client_ip(request),
                "query": _redact_query(request.url.query) or None,
                "user_agent": request.headers.get("user-agent"),
            }
            if self.log_request_body:
                fields["body"] = _body_preview(
                    getattr(request.state, "body", None),
                    total_length=getattr(request.state, "body_length", None),
                )
                fields["content_type"] = request.headers.get("content-type")
            log_event(
                logger,
                _access_log_level(status_code, duration_ms, self.slow_request_ms),
                "request_completed",
                **fields,
            )
            request_id_var.reset(token)


class BodyCaptureMiddleware:
    """Buffer the request body so it stays replayable and can be logged.

    Only installed when ``LOG_REQUEST_BODY`` is on. Recorded ASGI messages are
    replayed verbatim downstream, so route handlers see an unchanged body; only
    the copy kept for the log is capped at
    :data:`_MAX_CAPTURED_BODY_BYTES`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Buffer an HTTP body, publish it on ``scope['state']``, then replay."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        messages: list[Message] = []
        captured = b""
        total_length = 0
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            chunk = message.get("body", b"")
            total_length += len(chunk)
            if len(captured) < _MAX_CAPTURED_BODY_BYTES:
                captured += chunk[: _MAX_CAPTURED_BODY_BYTES - len(captured)]
            more_body = message.get("more_body", False)

        state = scope.setdefault("state", {})
        state["body"] = captured
        # The true size, so the log can say how much was dropped without ever
        # keeping the bytes that were.
        state["body_length"] = total_length

        pending = list(messages)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            # Past the end of the body: let the server close the stream.
            return await receive()

        await self.app(scope, replay, send)


def _access_log_level(status_code: int, duration_ms: float, slow_request_ms: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400 or duration_ms >= slow_request_ms:
        return logging.WARNING
    return logging.INFO


def _resolve_request_id(request: Request) -> str:
    for header in _INBOUND_REQUEST_ID_HEADERS:
        candidate = request.headers.get(header)
        if candidate:
            cleaned = candidate.strip()[:_MAX_REQUEST_ID_LENGTH]
            if cleaned:
                return cleaned
    return str(uuid.uuid4())


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Left-most entry is the originating client.
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _redact_query(query: str) -> str:
    """Scrub sensitive parameter values out of a raw query string.

    :func:`redact` only walks mappings and sequences, so a query string reaches
    the log untouched without this. The path is structural and is never
    redacted; only the values are.
    """
    return _REDACTED_PARAM_PATTERN.sub(rf"\1\2{_REDACTED}", query)


def _body_preview(body: bytes | None, *, total_length: int | None = None) -> str:
    """Render a request body for the access log, with secrets redacted.

    Only a JSON object or array is rendered, because :func:`redact` can walk
    those and can be relied on to catch the credential fields. Every other wire
    format — form encoded, multipart, malformed JSON — carries secrets in
    shapes no key-based scrub can be trusted to recognise, so those are
    summarised by size instead of quoted.
    """
    if not body:
        return ""
    size = total_length if total_length is not None else len(body)

    parsed: Any = None
    with contextlib.suppress(ValueError, TypeError):
        parsed = json.loads(body)
    if not isinstance(parsed, (dict, list)):
        reason = "truncated at the capture limit" if size > len(body) else "not a JSON object"
        return f"<not logged: {reason}, {size} bytes>"

    text = json.dumps(redact(parsed), ensure_ascii=False)
    if len(text) > _MAX_LOGGED_BODY_CHARS:
        text = f"{text[:_MAX_LOGGED_BODY_CHARS]}...<truncated>"
    return text


def _install_outermost(app: FastAPI, middleware_class: type, **kwargs: Any) -> None:
    """Wrap ``app``'s whole middleware stack in ``middleware_class``.

    ``add_middleware`` can only insert *inside* ``ServerErrorMiddleware``, which
    is the layer that renders an unhandled exception into the 500 response.
    Overriding the stack builder puts the new layer above it, and defers the
    build to the first request so exception handlers and routes registered
    after the call are still part of the stack.
    """
    build_middleware_stack = app.build_middleware_stack

    def build_with_context() -> ASGIApp:
        return middleware_class(build_middleware_stack(), **kwargs)

    app.build_middleware_stack = build_with_context  # type: ignore[method-assign]


def add_request_context_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Install request context (and optional body capture) on ``app``."""
    settings = settings or get_settings()
    if settings.log_request_body:
        app.add_middleware(BodyCaptureMiddleware)
    # Added last, so it still wraps body capture, CORS and the router.
    _install_outermost(app, RequestContextMiddleware, settings=settings)
