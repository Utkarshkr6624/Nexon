"""Regression tests for the user-account fixes.

Three defects, all in the same corner of the codebase:

* ``GET /api/v1/users/`` answered **500** for an administrator, because neither
  :meth:`UserService.list_all` nor :meth:`UserRepository.list_all` existed. The
  route is the permission system's one observable gate, so a 500 there meant the
  whole role → permission map was unobservable.
* Editing a profile answered **409 against your own username**: the uniqueness
  pre-check asked "does anybody hold this name" instead of "does anybody
  *else*", so a form that resubmits the username field on every save could never
  be saved.
* :meth:`UserRepository.update_fields` was a bare ``setattr`` over whatever it
  was handed, which is a mass-assignment hole the moment a caller forwards a
  payload — ``**payload.model_dump()`` would then be able to write ``role``,
  ``is_active`` or ``created_at``.

None of them needs a database to lock down, so every test here runs: the SQL
assertions capture the statement the *repository* built, the service assertions
drive the real service over a staged repository, and the HTTP assertions drive
the real application with its repositories overridden. The doubles
(:class:`FakeAsyncSession` and :func:`compiled_sql`) are imported from
:mod:`tests.test_regressions_security` rather than duplicated — the same pattern
``test_error_handling`` uses to borrow ``assert_no_internals`` from
``test_errors``.
"""

from __future__ import annotations

import contextlib
import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_session_repository
from app.core.config import Settings, get_settings
from app.core.deps import get_user_repository
from app.core.exceptions import ConflictError
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import create_app
from app.models.session import Session
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserUpdate
from app.services.user_service import UserService
from tests.test_regressions_security import FakeAsyncSession, compiled_sql

#: Every column ``update_fields`` must refuse. ``id`` and ``created_at`` are the
#: row's identity and its timeline; ``role``, ``is_active`` and
#: ``is_superuser`` are authorisation.
FORBIDDEN_FIELDS = ("id", "role", "is_active", "created_at", "updated_at", "is_superuser")

#: Named so the stand-in session row below does not read as a credential.
SESSION_DIGEST = "an-unrelated-digest"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _LiveSessionRepository:
    """A session repository that answers "yes, this session is live"."""

    def __init__(self, row: Session) -> None:
        self.row = row

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        return self.row

    async def get_by_id_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> Session | None:
        return self.row if self.row.user_id == user_id else None

    async def list_for_user(self, user_id: uuid.UUID, *, include_inactive: bool = False) -> list:
        return [self.row]


class _UsernameRepository(UserRepository):
    """The real repository, with the two existence answers staged per test.

    Inheriting rather than stubbing keeps every query the real one builds; only
    the number the ``count(*)`` returns is replaced, so the compiled SQL under
    assertion is the SQL that would run.
    """

    def __init__(self, session: FakeAsyncSession, *, username_taken: bool) -> None:
        super().__init__(session)
        self.username_taken = username_taken

    async def exists_by_username(
        self, username: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        await super().exists_by_username(username, exclude_user_id=exclude_user_id)
        return self.username_taken


def _user(*, role: str = "user", username: str = "ada") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"{username}@nexus.dev",
        username=username,
        hashed_password="x",  # noqa: S106
        display_name="Ada Lovelace",
        role=role,
        is_active=True,
        is_verified=True,
        created_at=now,
        updated_at=now,
    )


def _session_row(user_id: uuid.UUID) -> Session:
    """A live session row owned by ``user_id``."""
    now = datetime.now(UTC)
    return Session(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=SESSION_DIGEST,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=30),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _access_token(user: User, session_id: uuid.UUID) -> str:
    """An access token signed with the settings the application itself resolves."""
    return create_access_token(
        user.id,
        settings=get_settings(),
        extra_claims={"sid": str(session_id), "jti": str(uuid.uuid4())},
    )


@contextlib.asynccontextmanager
async def _client_for(*, repository, user: User) -> AsyncIterator[AsyncClient]:
    """A client over the real app with the database-facing dependencies replaced.

    ``get_db`` is overridden alongside the repository so the audit sink, which
    shares the request-scoped session, cannot reach for an engine either.
    """
    application = create_app(Settings(_env_file=None, environment="test", debug=False))
    session = repository.session
    session.scalar = user
    session.rows = [user]

    async def _db():
        yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_user_repository] = lambda: repository
    application.dependency_overrides[get_session_repository] = lambda: _LiveSessionRepository(
        _session_row(user.id)
    )

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as client:
        yield client


# ---------------------------------------------------------------------------
# 7. The administrative listing answers 200
# ---------------------------------------------------------------------------


def test_both_layers_expose_a_list_all_with_no_arguments():
    """The fix was two methods; this pins the shape the route calls them through."""
    for method in (UserRepository.list_all, UserService.list_all):
        assert inspect.iscoroutinefunction(method), method
        assert list(inspect.signature(method).parameters) == ["self"], method
        # ``from __future__ import annotations`` means the return type is the
        # string ``list[User]``; only the leading ``list`` is worth pinning,
        # because that is what the route iterates.
        assert inspect.signature(method).return_annotation.startswith("list["), method


async def test_the_admin_listing_answers_200_with_the_accounts():
    """The regression itself, end to end over the real route.

    Before the fix ``users.list_all()`` did not exist, so the handler raised
    ``AttributeError`` and the caller received the ``internal_error`` envelope
    with a 500. The repository *and* the service are the real ones here — only
    the session underneath them is staged — so removing either method turns this
    red again.
    """
    admin = _user(role="admin", username="ada")
    session = FakeAsyncSession()
    repository = UserRepository(session)

    async with _client_for(repository=repository, user=admin) as client:
        response = await client.get(
            "/api/v1/users/", headers=_bearer(_access_token(admin, _session_row(admin.id).id))
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert [entry["username"] for entry in body] == ["ada"]
    assert body[0]["permissions"] == sorted(body[0]["permissions"])
    assert "users.read" in body[0]["permissions"]


async def test_an_ordinary_caller_is_still_refused_with_403(assert_error_envelope):
    """A 200 for everybody would not be a fix, it would be the gate removed.

    The listing is a permission-system fixture: the refusal has to stay
    observable, and it has to stay a 403 rather than a 401, because
    authentication runs first.
    """
    ordinary = _user()
    repository = UserRepository(FakeAsyncSession())

    async with _client_for(repository=repository, user=ordinary) as client:
        response = await client.get(
            "/api/v1/users/",
            headers=_bearer(_access_token(ordinary, _session_row(ordinary.id).id)),
        )

    assert_error_envelope(response, status_code=403, code="forbidden")


async def test_the_listing_reaches_the_repository_rather_than_short_circuiting():
    """The handler must actually ask the repository; an empty list is not evidence."""
    admin = _user(role="admin")
    session = FakeAsyncSession()
    repository = _UsernameRepository(session, username_taken=False)

    async with _client_for(repository=repository, user=admin) as client:
        await client.get(
            "/api/v1/users/", headers=_bearer(_access_token(admin, _session_row(admin.id).id))
        )

    assert any("FROM users" in compiled_sql(statement) for statement in session.statements)
    assert any("ORDER BY" in compiled_sql(statement) for statement in session.statements)


# ---------------------------------------------------------------------------
# 8. Editing a profile no longer 409s against your own username
# ---------------------------------------------------------------------------


async def test_the_username_check_can_leave_the_caller_out():
    """``users.id != <caller>`` reaches the statement.

    This is the whole fix, and it has to be in the ``WHERE`` clause: a pre-check
    that loads the holder's row and compares ids in Python answers a different
    question under a race, and puts the ownership decision in two places.
    """
    session = FakeAsyncSession()
    repository = UserRepository(session)

    await repository.exists_by_username("ada", exclude_user_id=uuid.uuid4())

    sql = session.last_sql
    assert "users.username =" in sql
    assert "users.id !=" in sql


async def test_the_registration_form_of_the_check_carries_no_exclusion():
    """Registration has no self-row to discount, so it must not pretend to.

    A registration check that quietly excluded nothing is the same as one that
    excluded an arbitrary row, and the difference only shows up as duplicate
    sign-ups getting past the pre-check.
    """
    session = FakeAsyncSession()
    repository = UserRepository(session)

    await repository.exists_by_username("ada")

    sql = session.last_sql
    assert "users.username =" in sql
    assert "users.id !=" not in sql


async def test_a_user_may_keep_the_username_they_already_hold():
    """The service asks about *other* accounts, by passing the caller's id.

    The profile form resubmits the username field on every save, so a caller who
    already holds the name must be told the write is fine — not that it is taken
    by themselves.
    """
    user = _user()
    session = FakeAsyncSession()
    repository = _UsernameRepository(session, username_taken=False)

    updated = await UserService(repository).update(user, UserUpdate(username="ada"))

    assert updated.username == "ada"
    assert "WHERE users.username" in session.last_sql
    assert "users.id !=" in session.last_sql


async def test_another_accounts_username_is_still_a_conflict():
    """The exclusion must not become a way to take a name that is really taken."""
    user = _user(username="ada")
    repository = _UsernameRepository(FakeAsyncSession(), username_taken=True)

    with pytest.raises(ConflictError):
        await UserService(repository).update(user, UserUpdate(username="grace"))

    assert user.username == "ada", "a refused update must not have written anything"


async def test_saving_your_own_profile_with_its_username_answers_200():
    """End to end over the real route, with the uniqueness answer staged.

    This is the 409 the audit reproduced, asserted as the user experiences it:
    a form that posts ``{"username": "ada", "display_name": "Ada L."}`` for the
    account already called ``ada``.
    """
    user = _user(username="ada")
    repository = _UsernameRepository(FakeAsyncSession(), username_taken=False)

    async with _client_for(repository=repository, user=user) as client:
        response = await client.patch(
            "/api/v1/users/me",
            json={"username": "ada", "display_name": "Ada L."},
            headers=_bearer(_access_token(user, _session_row(user.id).id)),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "ada"
    assert body["display_name"] == "Ada L."
    # The 200 has to come from the exclusion, not from a lucky staging: the
    # statement the repository actually ran is the one that carries it.
    assert any(
        "users.id !=" in compiled_sql(statement) for statement in repository.session.statements
    ), "the uniqueness check must exclude the caller"


async def test_saving_a_username_another_account_holds_answers_409(assert_error_envelope):
    """The refusal is unchanged for the case it was written for."""
    user = _user(username="ada")
    repository = _UsernameRepository(FakeAsyncSession(), username_taken=True)

    async with _client_for(repository=repository, user=user) as client:
        response = await client.patch(
            "/api/v1/users/me",
            json={"username": "grace"},
            headers=_bearer(_access_token(user, _session_row(user.id).id)),
        )

    assert_error_envelope(response, status_code=409, code="conflict")


# ---------------------------------------------------------------------------
# 10. update_fields refuses anything outside its allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
async def test_update_fields_refuses_a_field_outside_the_allowlist(field):
    """A mass-assignment hole, closed one column at a time.

    Before the fix the method was ``setattr`` over whatever it was handed, so
    ``role`` was writable by any caller with a repository handle — including a
    service that forwarded a request payload without meaning to.
    """
    session = FakeAsyncSession()
    repository = UserRepository(session)
    user = _user()

    with pytest.raises(ValueError, match=field):
        await repository.update_fields(user, **{field: "admin"})

    # A programming error, refused loudly — and refused before the session was
    # touched, so nothing half-written can be committed by a later flush.
    assert session.added == []
    assert session.commits == 0


async def test_the_refusal_names_every_offending_field():
    """The message has to be actionable: which field, and what is allowed."""
    repository = UserRepository(FakeAsyncSession())

    with pytest.raises(ValueError) as refused:
        await repository.update_fields(_user(), role="admin", is_active=True)

    message = str(refused.value)
    assert "is_active" in message
    assert "role" in message
    assert "display_name" in message, "the message should say what is allowed"


async def test_update_fields_accepts_an_allowlisted_field():
    """The guard is an allowlist, not a denylist of the obvious columns.

    A denylist is exactly the shape that lets the next column through unremarked,
    so a normal write has to keep working and has to be visible in the row.
    """
    session = FakeAsyncSession()
    repository = UserRepository(session)
    user = _user()

    updated = await repository.update_fields(user, display_name="Ada L.")

    assert updated.display_name == "Ada L."
    assert session.commits == 1
    assert session.added == [user]


async def test_a_cleared_optional_field_is_still_a_value_to_write():
    """``None`` writes; omitting the key skips. The allowlist must not change that."""
    session = FakeAsyncSession()
    repository = UserRepository(session)
    user = _user()
    user.avatar_url = "https://cdn.example.com/ada.png"

    updated = await repository.update_fields(user, avatar_url=None)

    assert updated.avatar_url is None


async def test_the_email_and_username_are_normalised_on_the_way_in():
    """The two unique columns are re-normalised at the boundary, as documented."""
    session = FakeAsyncSession()
    repository = UserRepository(session)
    user = _user()

    await repository.update_fields(user, email="  Ada@Nexus.DEV ", username="  Ada  ")

    assert user.email == "ada@nexus.dev"
    assert user.username == "Ada"
