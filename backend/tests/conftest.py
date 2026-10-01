"""Shared pytest fixtures for the NEXUS backend suite.

Three things happen here that the application does not do for itself:

* **Event loop.** ``pytest-asyncio`` 1.4 has no ``asyncio_default_test_loop_factory``
  ini option, so the loop is selected by installing a policy whose
  ``new_event_loop`` returns :func:`app.core.event_loop.nexus_loop_factory`.
  Without this, psycopg's async driver refuses the Windows default
  ``ProactorEventLoop`` and every database-backed test errors. The factory must
  therefore construct the loop itself; ``asyncio.new_event_loop()`` would route
  back through this policy and recurse until the stack ran out.
* **Database.** The ``nexus_test`` database is created if missing — with the same
  extensions ``scripts/create_test_database.py`` installs — and brought to
  ``head`` with Alembic. ``Base.metadata.create_all`` is deliberately never
  used: a schema built from the models would prove nothing about the migration.
* **Isolation.** The app's engine is redirected at the test database for the whole
  session so API tests never touch the development database, and every managed
  table is truncated before each test — repository methods commit, so that
  truncation is the only thing standing between two tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.event_loop import nexus_loop_factory
from app.db import session as app_db_session
from app.models import Base

#: Kept in sync with docker/postgres/init/10_extensions.sql so a test run on a
#: native PostgreSQL exercises the same features as the container stack.
EXTENSIONS = ("pg_trgm", "unaccent")


class _NexusEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Route every loop created by the suite through :func:`nexus_loop_factory`."""

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return nexus_loop_factory()


# Installed at import time, before pytest-asyncio's session-scoped
# ``event_loop_policy`` fixture snapshots it into the ``asyncio.Runner``.
asyncio.set_event_loop_policy(_NexusEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Fresh settings for the current test.

    ``get_settings`` is an ``lru_cache`` singleton; clearing it before and after
    means a test that monkeypatches the environment cannot leak into the next.
    """
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch):
    """Build a :class:`Settings` from explicit environment overrides."""

    def _factory(**overrides: str) -> Settings:
        for key, value in overrides.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return Settings()

    yield _factory
    # Without this the mutated settings would stay cached for every later test.
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test database
# ---------------------------------------------------------------------------


def _sync_url(async_uri: str) -> str:
    """Strip SQLAlchemy's driver suffix so psycopg can consume the URI."""
    return async_uri.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_name(async_uri: str) -> str:
    return unquote(urlsplit(_sync_url(async_uri)).path).lstrip("/")


def _url_for_database(async_uri: str, database: str) -> str:
    parts = urlsplit(_sync_url(async_uri))
    return parts._replace(path=f"/{database}").geturl()


def _ensure_database_exists(test_uri: str) -> None:
    """Create the test database when it is missing.

    A stock PostgreSQL installation — including the official container image —
    ships only ``postgres``, ``template0`` and ``template1``, so the suite has to
    provision its own database. ``CREATE DATABASE`` cannot run inside a
    transaction, hence AUTOCOMMIT.
    """
    database = _database_name(test_uri)
    maintenance_uri = _url_for_database(test_uri, "postgres")
    with psycopg.connect(maintenance_uri, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{database}"')
    _ensure_extensions(test_uri, database)


def _ensure_extensions(test_uri: str, database: str) -> None:
    """Enable the project's extensions inside ``database``, warning on failure.

    Mirrors ``scripts/create_test_database.py`` so a native PostgreSQL run
    exercises the same features as the container. Not fatal: a build without the
    contrib modules has no ``pg_trgm.control`` to install from, and the test
    database is still perfectly usable. ``CREATE EXTENSION`` cannot run inside a
    transaction either, hence AUTOCOMMIT.
    """
    unavailable: list[str] = []
    with psycopg.connect(_url_for_database(test_uri, database), autocommit=True) as connection:
        for extension in EXTENSIONS:
            try:
                connection.execute(
                    sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension))
                )
            except psycopg.Error as exc:
                unavailable.append(f"{extension} ({str(exc).splitlines()[0]})")
    if unavailable:
        print(
            f"[warn] extensions not installed in the test database: {', '.join(unavailable)}\n"
            "       This PostgreSQL build has no contrib modules; the search tests\n"
            "       that need pg_trgm/unaccent will skip. Install\n"
            "       postgresql-contrib to run them.",
            file=sys.stderr,
            flush=True,
        )


@contextlib.contextmanager
def _preserved_logging() -> Iterator[None]:
    """Restore the stdlib logging tree after Alembic's ``fileConfig`` runs.

    ``migrations/env.py`` calls ``logging.config.fileConfig``; letting that run
    mid-suite would swap out pytest's capture handlers behind its back.
    """
    names = ("", "alembic", "sqlalchemy", "sqlalchemy.engine")
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (
            list(logger.handlers),
            logger.level,
            logger.propagate,
            logger.disabled,
        )
    try:
        yield
    finally:
        for name, (handlers, level, propagate, disabled) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def _alembic_config(test_uri: str) -> AlembicConfig:
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ``%`` is a ConfigParser interpolation character, so it must be doubled for
    # the value to survive a round trip through the ini parser.
    config.set_main_option("sqlalchemy.url", test_uri.replace("%", "%%"))
    return config


def _assert_separate_test_database(test_uri: str, app_uri: str) -> None:
    """Abort the session unless the test database is not the application one.

    ``truncated_database`` runs ``TRUNCATE ... RESTART IDENTITY CASCADE``, so a
    ``TEST_DATABASE_URL`` left pointing at the application database would empty
    real data. ``scripts/create_test_database.py`` refuses the same collision,
    but the conftest that actually truncates has to refuse it too — that script
    is optional and may never have been run.
    """
    database = _database_name(test_uri)
    if database == _database_name(app_uri):
        pytest.exit(
            f'TEST_DATABASE_URL resolves to the application database "{database}".\n'
            "The suite truncates every managed table there, so it refuses to run.\n"
            "Point TEST_DATABASE_URL at a separate database (nexus -> nexus_test),\n"
            "or drop it so it is derived from POSTGRES_DB.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Create ``nexus_test`` if needed and migrate it to ``head``."""
    get_settings.cache_clear()
    settings = get_settings()
    uri = settings.test_sqlalchemy_database_uri
    _assert_separate_test_database(uri, settings.sqlalchemy_database_uri)
    _ensure_database_exists(uri)
    with _preserved_logging():
        command.upgrade(_alembic_config(uri), "head")
    return uri


@pytest.fixture(scope="session")
def engine(test_database_url: str) -> Iterator[AsyncEngine]:
    """Session-wide engine on the test database, installed as the app's engine.

    ``NullPool`` keeps no connections between checkouts. That matters here
    because ``pytest.ini`` scopes the asyncio loop to a single test, so a pooled
    connection opened under one loop would be reused under the next.
    """
    previous_engine = app_db_session._engine
    previous_factory = app_db_session._session_factory

    test_engine = create_async_engine(test_database_url, poolclass=NullPool)
    app_db_session.configure(test_engine)
    try:
        yield test_engine
    finally:
        app_db_session._engine = previous_engine
        app_db_session._session_factory = previous_factory
        with contextlib.suppress(RuntimeError):
            asyncio.run(test_engine.dispose(), loop_factory=nexus_loop_factory)


@pytest.fixture
async def truncated_database(engine: AsyncEngine) -> None:
    """Empty every managed table so each test starts from a known state."""
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if not tables:
        return
    async with engine.connect() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await connection.commit()


@pytest.fixture
async def db_session(engine: AsyncEngine, truncated_database: None) -> AsyncIterator[AsyncSession]:
    """A session on the test database, discarded after the test.

    The repository methods call ``session.commit()``, so writes made by a test
    are committed and outlive the session — the ``rollback()`` below only
    discards whatever a test left uncommitted. Isolation comes from
    ``truncated_database``, which empties every managed table before each test.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """The real application object.

    ``ASGITransport`` does not run the lifespan, so nothing here may assume the
    startup or shutdown hooks have fired; the engine is installed by the
    ``engine`` fixture instead, which is what those hooks would have done.
    """
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def offline_client(app) -> AsyncIterator[AsyncClient]:
    """A client with no database fixture in scope, for DB-free assertions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
async def non_raising_client(app) -> AsyncIterator[AsyncClient]:
    """A client that observes the rendered 5xx response instead of the exception.

    :class:`httpx.ASGITransport` defaults to ``raise_app_exceptions=True``, so
    an exception escaping the app is re-raised inside the test and the response
    the user would have received is never visible. That is the behaviour a test
    wants when it is asserting on the bug, but it makes the application's own
    error handling — the ``internal_error`` catch-all handler — impossible to
    test: with the default transport the handler's output can never be observed.

    Use this fixture for anything that deliberately raises, and the default
    clients everywhere else: ``offline_client`` when no database is needed,
    ``client`` when the route does. Like the others, it depends on ``app`` alone,
    so a database-backed test can add ``truncated_database`` to its signature.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
async def client(app, engine: AsyncEngine, truncated_database: None) -> AsyncIterator[AsyncClient]:
    """An :class:`AsyncClient` wired to the app in-process, over a clean database.

    ``ASGITransport`` does not run the lifespan; the ``engine`` fixture has
    already put the test database behind ``app.db.session`` so the handlers find
    a reachable, migrated database without any startup hook having fired.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as http_client:
        yield http_client


@pytest.fixture
def assert_error_envelope():
    """Assert the shared error envelope and return its ``error`` object."""

    def _assert(response, *, status_code: int, code: str) -> dict:
        assert response.status_code == status_code, response.text
        payload = response.json()
        assert set(payload) == {"error"}, payload
        error = payload["error"]
        assert set(error) == {"code", "message", "details", "request_id"}, error
        assert error["code"] == code
        assert isinstance(error["message"], str) and error["message"]
        assert error["request_id"] == response.headers["X-Request-ID"]
        return error

    return _assert
