"""Alembic migration environment for NEXUS.

Runs in both modes:

* **offline** (``alembic upgrade head --sql``) emits SQL to stdout without
  connecting to a database.
* **online** connects through the project's async psycopg3 driver
  (``postgresql+psycopg``) and drives Alembic from a sync-facade connection via
  ``connection.run_sync()``. SQLAlchemy's async engine cannot run migrations
  directly, so every migration step executes on the greenlet-safe sync side.

The database URL is never read from this file or from ``os.environ`` directly:
it comes from :func:`app.core.config.get_settings`, which is the single source
of truth for connection settings.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.types import Uuid

from app.core.config import get_settings
from app.core.event_loop import nexus_loop_factory
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The complete schema lives in the ORM metadata. Models are never re-declared
# here — importing ``app.models`` is what registers every table.
target_metadata = Base.metadata

#: Tables Alembic must never treat as part of the project schema.
EXCLUDED_TABLES = frozenset({"alembic_version", "spatial_ref_sys"})

#: Only the default schema is managed by these migrations.
MANAGED_SCHEMA = "public"


def get_url() -> str:
    """Resolve the async SQLAlchemy URL for this migration run."""
    configured = (config.get_main_option("sqlalchemy.url", "") or "").strip()
    if configured:
        return configured
    return get_settings().sqlalchemy_database_uri


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Restrict autogenerate to the schema NEXUS owns."""
    if type_ == "table":
        if name in EXCLUDED_TABLES:
            return False
        schema = getattr(obj, "schema", None)
        return schema is None or schema == MANAGED_SCHEMA
    return True


def render_item(type_, obj, autogen_context):
    """Render column types in their portable, explicit form.

    ``postgresql.UUID`` renders as ``postgresql.UUID(as_uuid=True)`` so that a
    generated migration states the dialect and the ``as_uuid`` return type
    explicitly instead of relying on a default.
    """
    if type_ == "type" and isinstance(obj, Uuid):
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.UUID(as_uuid=True)"
    return False


def _autogenerate_options() -> dict[str, object]:
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "include_object": include_object,
        "render_item": render_item,
    }


def run_migrations_offline() -> None:
    """Emit migration SQL without a live database connection."""
    context.configure(
        url=get_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_autogenerate_options(),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an already-open (sync-facade) connection."""
    context.configure(connection=connection, **_autogenerate_options())

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open the async engine and hand a sync connection to Alembic."""
    connectable = create_async_engine(get_url(), poolclass=pool.NullPool)

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    # The loop choice lives in app.core.event_loop so the API, the migrations
    # and the test suite cannot drift apart on platform loop requirements.
    asyncio.run(run_async_migrations(), loop_factory=nexus_loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()