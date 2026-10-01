#!/usr/bin/env python
"""Create the pytest database (``nexus_test``) if it does not exist yet.

Idempotent: an existing database is left completely alone apart from the
extension check below, so running this before every test run is safe.

    python scripts/create_test_database.py
    python scripts/create_test_database.py --url postgresql+psycopg://... --drop

The maintenance connection is made in AUTOCOMMIT mode because ``CREATE
DATABASE`` cannot run inside a transaction block. Credentials come from ``.env``
and are never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    db_name_from_url,
    fail,
    import_psycopg,
    info,
    load_settings,
    ok,
    redact_message,
    redact_url,
    resolve_database_url,
    to_psycopg_dsn,
    warn,
    with_database,
)

#: Kept in sync with docker/postgres/init/10_extensions.sql so a test run on a
#: native PostgreSQL exercises the same features as the container stack.
EXTENSIONS = ("pg_trgm", "unaccent")

MAINTENANCE_DB = "postgres"


def ensure_database(psycopg, dsn: str, database: str, *, drop: bool) -> None:
    """Create ``database`` on the server at ``dsn`` if it is missing."""
    # psycopg.sql keeps the identifier properly quoted, so a database name with
    # a dash or uppercase cannot turn into broken or injected SQL.
    from psycopg import sql  # noqa: PLC0415 - only needed on this path

    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            exists = cursor.fetchone() is not None

            if exists and drop:
                cursor.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )
                ok(f"dropped existing database \"{database}\"")
                exists = False

            if exists:
                ok(f'database "{database}" already exists (left untouched)')
            else:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                ok(f'created database "{database}"')


def ensure_extensions(psycopg, dsn: str, database: str) -> list[str]:
    """Enable the project's extensions inside ``database``; return the failures.

    Not fatal: a stock PostgreSQL build without the contrib modules simply has
    no ``pg_trgm.control`` to install from, and the test database is still
    perfectly usable. It is a warning, not an error, so the operator can decide
    whether their server needs the contrib package.
    """
    from psycopg import sql  # noqa: PLC0415

    unavailable: list[str] = []
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                for extension in EXTENSIONS:
                    try:
                        cursor.execute(
                            sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                                sql.Identifier(extension)
                            )
                        )
                    except psycopg.Error as exc:
                        unavailable.append(f"{extension} ({str(exc).splitlines()[0]})")
    except psycopg.Error as exc:
        warn(f'could not open "{database}" to check extensions: {redact_message(str(exc), dsn)}')
        return [f"connection to {database} failed"]

    if unavailable:
        warn(
            f'extensions not installed in "{database}": {", ".join(unavailable)}\n'
            "       This PostgreSQL build has no contrib modules. Install them\n"
            "       (postgresql-contrib / postgresql16-contrib) and re-run, or keep\n"
            "       going: the test database itself is ready."
        )
    else:
        info(f'extensions available in "{database}": {", ".join(EXTENSIONS)}')
    return unavailable


def check_reachable(psycopg, dsn: str) -> None:
    """Fail early, and specifically, when the server cannot be reached."""
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
            connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - report anything libpq raises
        detail = redact_message(f"{type(exc).__name__}: {exc}", dsn)
        fail(
            f"could not connect to {redact_url(dsn)}\n"
            f"       {detail}\n"
            "       Start PostgreSQL, or point DATABASE_URL in .env at the right server.\n"
            "       `python scripts/wait_for_db.py --timeout 5` gives the same diagnosis."
        )


def main() -> int:
    """Create the test database, then align its extensions with the container."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        default=None,
        help="application database URL; the test database name is taken from TEST_DATABASE_URL",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="drop the test database first and recreate it (destructive)",
    )
    args = parser.parse_args()

    settings = load_settings()
    main_url = args.url or resolve_database_url(settings)
    test_url = resolve_database_url(settings, key="TEST_DATABASE_URL", database_suffix="_test")

    test_database = db_name_from_url(test_url) or "nexus_test"
    main_database = db_name_from_url(main_url) or ""
    if test_database == main_database:
        fail(
            f'TEST_DATABASE_URL points at the application database ("{test_database}").\n'
            "       Set TEST_DATABASE_URL in .env to a separate database, or drop it so\n"
            "       it is derived from POSTGRES_DB (nexus -> nexus_test)."
        )

    psycopg = import_psycopg()
    maintenance_dsn = to_psycopg_dsn(with_database(main_url, MAINTENANCE_DB))
    info(f"using maintenance database \"{MAINTENANCE_DB}\" on {redact_url(maintenance_dsn)}")

    check_reachable(psycopg, maintenance_dsn)
    ensure_database(psycopg, maintenance_dsn, test_database, drop=args.drop)
    unavailable = ensure_extensions(psycopg, to_psycopg_dsn(test_url), test_database)

    ok(f'test database "{test_database}" is ready')
    if unavailable:
        warn("ready to run pytest, but the search extensions are missing (see above)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
