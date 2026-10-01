"""UserRepository against the real test database."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.repositories.user import UserRepository

pytestmark = pytest.mark.integration

#: A repository stores an already-hashed value; the plaintext never reaches this layer.
DUMMY_HASH = "not-a-real-bcrypt-hash"


@pytest.fixture
def repository(db_session) -> UserRepository:
    return UserRepository(db_session)


async def test_create_persists_the_user_with_server_defaults(repository, db_session):
    created = await repository.create(
        email="  Ada@Nexus.DEV ",
        hashed_password=DUMMY_HASH,
        full_name="Ada Lovelace",
    )

    assert isinstance(created, User)
    assert isinstance(created.id, uuid.UUID)
    # The email is normalised before it reaches the column.
    assert created.email == "ada@nexus.dev"
    assert created.is_active is True
    assert created.is_verified is False
    assert created.is_superuser is False
    # Server defaults must be re-read, not left as None on the response object.
    assert created.created_at is not None and created.created_at.tzinfo is not None
    assert created.updated_at is not None

    stored = await repository.get_by_id(created.id)
    assert stored is not None
    assert stored.full_name == "Ada Lovelace"
    assert stored.hashed_password == DUMMY_HASH


async def test_get_by_id_returns_none_for_an_unknown_id(repository):
    assert await repository.get_by_id(uuid.uuid4()) is None


async def test_get_by_email_normalises_case_and_whitespace(repository):
    created = await repository.create(email="grace@nexus.dev", hashed_password=DUMMY_HASH)

    found = await repository.get_by_email("  GRACE@Nexus.Dev  ")
    assert found is not None
    assert found.id == created.id
    assert await repository.get_by_email("nobody@nexus.dev") is None


async def test_exists_by_email(repository):
    assert await repository.exists_by_email("ada@nexus.dev") is False

    await repository.create(email="Ada@Nexus.DEV", hashed_password=DUMMY_HASH)

    assert await repository.exists_by_email("ada@nexus.dev") is True
    assert await repository.exists_by_email(" ADA@nexus.dev ") is True
    assert await repository.exists_by_email("someone-else@nexus.dev") is False


async def test_a_duplicate_email_is_rejected_by_the_unique_index(repository, db_session):
    await repository.create(email="ada@nexus.dev", hashed_password=DUMMY_HASH)

    with pytest.raises(IntegrityError):
        await repository.create(email="ADA@nexus.dev", hashed_password=DUMMY_HASH)
    await db_session.rollback()


async def test_update_fields_persists_a_partial_change(repository):
    created = await repository.create(email="ada@nexus.dev", hashed_password=DUMMY_HASH)

    updated = await repository.update_fields(created, full_name="Ada L.", is_verified=True)

    assert updated.full_name == "Ada L."
    assert updated.is_verified is True
    assert updated.email == "ada@nexus.dev"
    reloaded = await repository.get_by_id(created.id)
    assert reloaded.full_name == "Ada L."
    assert reloaded.is_verified is True


async def test_the_session_fixture_isolates_tests(repository):
    """Each test starts from an empty table.

    The repository methods commit, so nothing here is undone by the fixture's
    closing ``rollback()``: the rows a test writes outlive its session. What
    empties the table is ``truncated_database``, which truncates every managed
    table before each test.
    """
    assert await repository.exists_by_email("ada@nexus.dev") is False
    await repository.create(email="ada@nexus.dev", hashed_password=DUMMY_HASH)
    assert await repository.exists_by_email("ada@nexus.dev") is True
