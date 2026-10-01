"""Redaction of sensitive values before they reach a log sink."""

from __future__ import annotations

import json
import logging
import sys
import uuid

import pytest

from app.core.logging import (
    REDACTED_KEYS,
    ContextFilter,
    JsonFormatter,
    get_logger,
    log_event,
    new_request_id,
    redact,
    request_id_var,
    set_request_id,
)

REDACTED = "***redacted***"
#: A throwaway value standing in for a real credential; nothing here is a secret.
PLAINTEXT = "correct-horse-battery-staple"

#: ``redact`` folds ``-`` to ``_`` before the lookup, so a hyphenated header
#: name is matched by its underscore entry. Checked separately because the
#: hyphen is what makes the case non-obvious.
HYPHENATED_SPELLINGS = frozenset({"x-api-key", "set-cookie"})


def _record(msg: str = "x", **attrs: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for name, value in attrs.items():
        setattr(record, name, value)
    return record


@pytest.mark.parametrize(
    "key", sorted(REDACTED_KEYS - {k.replace("-", "_") for k in HYPHENATED_SPELLINGS})
)
def test_every_declared_key_is_redacted(key):
    assert redact({key: "hunter2"}) == {key: REDACTED}


@pytest.mark.parametrize("key", sorted(HYPHENATED_SPELLINGS))
def test_hyphenated_keys_are_redacted(key):
    assert redact({key: "header-value"}) == {key: REDACTED}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("password", "hunter2"),
        ("hashed_password", "$2b$12$abcdefghijklmnopqrstuv"),
        ("token", "eyJhbGciOiJIUzI1NiJ9"),
        ("access_token", "eyJhbGciOiJIUzI1NiJ9"),
        ("refresh_token", "eyJhbGciOiJIUzI1NiJ9"),
        ("authorization", "Bearer eyJhbGciOiJIUzI1NiJ9"),
        ("secret", "s3cr3t"),
        ("secret_key", "s3cr3t"),
        ("api_key", "key-material"),
    ],
)
def test_credentials_never_survive_redaction(key, value):
    result = redact({key: value})

    assert result[key] == REDACTED
    assert value not in json.dumps(result)


def test_redaction_ignores_case_surrounding_space_and_dashes():
    payload = {"Password": "hunter2", "  HASHED-PASSWORD  ": "$2b$12$abc", "AUTHORIZATION": "x"}

    assert set(redact(payload).values()) == {REDACTED}


def test_nested_mappings_and_sequences_are_redacted():
    payload = {
        "user": {"email": "ada@nexus.dev", "password": "hunter2"},
        "attempts": [
            {"email": "ada@nexus.dev", "password": "hunter2"},
            {"email": "bob@nexus.dev", "password": "swordfish"},
        ],
        "headers": {"authorization": "Bearer abc"},
    }

    result = redact(payload)

    assert result["user"]["email"] == "ada@nexus.dev"
    assert result["user"]["password"] == REDACTED
    assert result["attempts"][0]["password"] == REDACTED
    assert result["attempts"][1]["password"] == REDACTED
    assert result["headers"]["authorization"] == REDACTED


def test_deeply_nested_structures_are_bounded_rather_than_recursing_forever():
    payload: dict = {"value": "leaf"}
    for _ in range(20):
        payload = {"child": payload}

    # Depth is capped, so the result stays finite instead of raising.
    assert json.dumps(redact(payload))


def test_non_sensitive_values_are_left_alone():
    payload = {
        "method": "POST",
        "path": "/api/v1/auth/login",
        "status_code": 401,
        "duration_ms": 1.23,
        "user": {"id": "5b1f", "tags": ["a", "b"]},
        "nothing": None,
    }

    assert redact(payload) == payload


def test_scalars_pass_through():
    assert redact("plain") == "plain"
    assert redact(7) == 7
    assert redact(None) is None


def test_a_realistic_login_body_comes_out_clean():
    """The exact shape ``RequestContextMiddleware`` would log."""
    payload = {
        "email": "ada@nexus.dev",
        "password": "correct-horse-battery-staple",
        "nested": {"hashed_password": "$2b$12$abc", "remember_me": True},
    }

    serialised = json.dumps(redact(payload))

    assert "correct-horse-battery-staple" not in serialised
    assert "$2b$12$abc" not in serialised
    assert "ada@nexus.dev" in serialised


def test_json_formatter_redacts_mapping_valued_fields():
    line = json.loads(JsonFormatter("NEXUS").format(_record(context={"password": "hunter2"})))

    assert line["context"] == {"password": REDACTED}


def test_json_formatter_redacts_scalar_extra_fields():
    line = json.loads(JsonFormatter("NEXUS").format(_record(password=PLAINTEXT)))

    assert line["password"] == REDACTED


def test_log_event_redacts_scalar_fields_before_they_reach_a_record():
    """The path the application itself uses, which is the one that matters."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = get_logger("app.test")
    handler = _Capture()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        log_event(
            logger,
            logging.INFO,
            "login_attempt",
            email="ada@nexus.dev",
            password=PLAINTEXT,
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = True

    assert len(records) == 1
    assert records[0].password == REDACTED
    assert records[0].email == "ada@nexus.dev"


def test_json_formatter_keeps_tracebacks_server_side():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = _record("unhandled_exception")
        record.exc_info = sys.exc_info()

    line = json.loads(JsonFormatter("NEXUS").format(record))

    assert line["exception"].startswith("Traceback")
    assert "RuntimeError: boom" in line["exception"]


def test_context_filter_attaches_the_current_request_id():
    record = _record()
    token = set_request_id("req-123")
    try:
        ContextFilter().filter(record)
    finally:
        request_id_var.reset(token)

    assert record.request_id == "req-123"


def test_new_request_id_is_a_uuid():
    assert uuid.UUID(new_request_id())
