"""The guard that keeps two pytest sessions out of one test database.

``truncated_database`` empties every managed table before each test, so a second
session against the same database does not merely slow the first one down — it
deletes the rows the first one is about to read, and the failures that follow
look like product defects. These tests pin the three properties that turn that
into a sentence instead: the lock is actually held, a conflict is actually
detected, and the detection is scoped to one database rather than to the server.
"""

from __future__ import annotations

import psycopg
import pytest

from tests import conftest
from tests.conftest import (
    SessionLockError,
    _acquire_session_lock,
    _async_url,
    _database_lock_key,
    _database_name,
    _lock_conflict_message,
    _suggest_alternative_database,
    _take_session_lock,
    _url_for_database,
)

pytestmark = pytest.mark.integration


def test_the_key_is_derived_from_the_database_name():
    """A constant key would make every session on the server exclude the others."""
    assert _database_lock_key("nexus_test") != _database_lock_key("nexus_test_2")
    assert _database_lock_key("nexus_test") != _database_lock_key("nexus_other")
    assert _database_lock_key("nexus_test") == _database_lock_key("nexus_test")


def test_the_key_fits_the_two_int32_form_the_server_accepts():
    first, second = _database_lock_key("nexus_test")

    for part in (first, second):
        assert -(2**31) <= part < 2**31


def test_the_alternative_suggested_stays_next_to_the_database_in_use():
    """``nexus_test`` already means "the test database"; ``nexus_test_2`` is clearer."""
    assert _suggest_alternative_database("nexus_test") == "nexus_test_2"
    assert _suggest_alternative_database("nexus") == "nexus_test"


def test_the_suggested_fix_is_the_current_url_with_the_new_database_name(test_database_url: str):
    """Same server, same credentials, different database — copyable as it stands."""
    alternative = _async_url(
        _url_for_database(test_database_url, _suggest_alternative_database("nexus_test"))
    )

    assert alternative.startswith("postgresql+psycopg://")
    assert _database_name(alternative) == "nexus_test_2"


def test_this_session_holds_the_lock_on_its_own_database(test_database_url: str):
    """The guard only works if the first session really is holding it."""
    database = _database_name(test_database_url)
    assert database in conftest._SESSION_LOCKS

    with psycopg.connect(_url_for_database(test_database_url, "postgres"), autocommit=True) as peer:
        acquired = peer.execute(
            "SELECT pg_try_advisory_lock(%s::int, %s::int)", _database_lock_key(database)
        ).fetchone()[0]

    assert acquired is False


def test_a_lock_on_another_database_does_not_block_this_session(test_database_url: str):
    """Exclusion is per-database; a second session elsewhere is unaffected.

    The probe database is a name no run would use rather than the one the
    conflict message suggests, because that suggestion is exactly the name a
    parallel session is most likely to be pointed at — and this test is about
    exclusion, not about that name.
    """
    other = f"{_database_name(test_database_url)}_session_lock_probe"

    with psycopg.connect(_url_for_database(test_database_url, "postgres"), autocommit=True) as peer:
        acquired = peer.execute(
            "SELECT pg_try_advisory_lock(%s::int, %s::int)", _database_lock_key(other)
        ).fetchone()[0]
        peer.execute("SELECT pg_advisory_unlock(%s::int, %s::int)", _database_lock_key(other))

    assert acquired is True


def test_a_second_session_is_told_which_database_is_taken_and_how_to_move(test_database_url: str):
    """A second session is refused rather than admitted.

    One attempt, not the production retry budget: the budget exists to forgive
    a session that is shutting down, and this session is not shutting down.
    """
    with pytest.raises(SessionLockError) as caught:
        _take_session_lock(test_database_url, attempts=1)

    message = str(caught.value)
    assert _database_name(test_database_url) in message
    assert "TEST_DATABASE_URL=" in message
    suggested = next(line for line in message.splitlines() if "TEST_DATABASE_URL=" in line)
    assert _database_name(suggested) != _database_name(test_database_url)
    assert "truncates every managed table" in message


def test_the_conflict_message_is_the_whole_of_what_the_operator_sees(test_database_url: str):
    """No traceback, no driver error: the message is the entire diagnosis."""
    message = _lock_conflict_message(test_database_url, "nexus_test", 4242)

    assert "nexus_test" in message
    assert "backend PID 4242" in message
    assert message.endswith("\n")
    # The driver only ever appears inside the URL being handed back.
    assert "psycopg.Error" not in message
    assert "Traceback" not in message


def test_the_session_ends_instead_of_truncating_when_the_database_is_taken(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
):
    """The conflict has to reach the operator as an exit, not as a raised error.

    The database name is one this session does not hold, so the idempotent cache
    cannot short-circuit the attempt, and the acquisition is forced to fail the
    way it does against a live session.
    """
    taken = _suggest_alternative_database(_database_name(test_database_url))

    def _refuse(uri: str, *, attempts: int = 1) -> conftest._SessionDatabaseLock:
        raise SessionLockError(_lock_conflict_message(uri, _database_name(uri), 4242))

    monkeypatch.setattr(conftest, "_take_session_lock", _refuse)

    with pytest.raises(pytest.exit.Exception) as caught:
        _acquire_session_lock(_url_for_database(test_database_url, taken))

    assert caught.value.returncode == 1
    assert taken in str(caught.value.msg)
