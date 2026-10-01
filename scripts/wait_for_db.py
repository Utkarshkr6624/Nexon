#!/usr/bin/env python
"""Block until the configured PostgreSQL server accepts connections.

Used by ``scripts/dev.sh`` so "the database is not up yet" is reported once,
clearly, instead of surfacing as a failed migration or a stack trace from the
API.

    python scripts/wait_for_db.py
    python scripts/wait_for_db.py --timeout 120 --url postgresql+psycopg://...

Exit status is 0 as soon as ``SELECT 1`` succeeds, 1 on timeout or on a
configuration error. The URL is printed with its password redacted.
"""

from __future__ import annotations

import argparse
import sys
import time
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
)

DEFAULT_TIMEOUT = 60.0
DEFAULT_INTERVAL = 1.0
#: Per-attempt connect timeout. Kept small so a black-holed TCP connection (a
#: firewall rather than a closed port) still yields back to the retry loop.
CONNECT_TIMEOUT = 5


def try_connect(psycopg, dsn: str) -> tuple[bool, str]:
    """Attempt one connection. Return ``(reachable, reason_if_not)``.

    The reason is redacted: libpq quotes the connection string back in several
    of its errors, and a password must not reach a terminal or a log.
    """
    try:
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT) as connection:
            connection.execute("SELECT 1")
        return True, ""
    except Exception as exc:  # noqa: BLE001 - report anything libpq raises
        return False, redact_message(f"{type(exc).__name__}: {exc}".strip(), dsn)


def explain(reason: str, database: str) -> str:
    """Turn a connection failure into a message that says what to do next."""
    lowered = reason.lower()
    if "does not exist" in lowered and ("database" in lowered or "role" in lowered):
        if "role" in lowered:
            return (
                f"the server does not know the role in DATABASE_URL.\n"
                "       Check POSTGRES_USER in .env against the server's own role."
            )
        return (
            f'the server is up but the database "{database}" does not exist.\n'
            "       Create the schema with:  (cd backend && alembic upgrade head)"
        )
    if "password authentication failed" in lowered or "no password supplied" in lowered:
        return (
            f'the server rejected the credentials for "{database}".\n'
            "       Check POSTGRES_USER / POSTGRES_PASSWORD in .env."
        )
    if "connection refused" in lowered or "connection timeout expired" in lowered:
        return (
            "no PostgreSQL server answered on that host and port.\n"
            "       Either it is not running, or a firewall is dropping the packets.\n"
            "       The target is printed below; check POSTGRES_HOST/POSTGRES_PORT in .env."
        )
    return f"last error: {reason}"


def main() -> int:
    """Poll the database until it is reachable or the timeout expires."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        default=None,
        help="database URL (default: DATABASE_URL from .env, else the POSTGRES_* parts)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to keep retrying before giving up (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between attempts (default: {DEFAULT_INTERVAL:g})",
    )
    parser.add_argument("--quiet", action="store_true", help="only report the final outcome")
    args = parser.parse_args()

    if args.timeout <= 0:
        fail("--timeout must be greater than 0")
    if args.interval <= 0:
        fail("--interval must be greater than 0")

    url = args.url or resolve_database_url(load_settings())
    dsn = to_psycopg_dsn(url)
    database = db_name_from_url(url) or "(default)"
    psycopg = import_psycopg()

    if not args.quiet:
        info(f"waiting up to {args.timeout:g}s for {redact_url(url)}")

    deadline = time.monotonic() + args.timeout
    attempt = 0
    last_reason = ""
    while True:
        attempt += 1
        reachable, last_reason = try_connect(psycopg, dsn)
        if reachable:
            ok(f'database "{database}" is accepting connections (attempt {attempt})')
            return 0
        if time.monotonic() >= deadline:
            break
        if not args.quiet:
            info(f"attempt {attempt} failed: {last_reason.splitlines()[0] or '?'}")
        time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))

    tries = "try" if attempt == 1 else "tries"
    fail(
        f'database "{database}" was still unreachable after {args.timeout:g}s '
        f"({attempt} {tries}).\n"
        f"       {explain(last_reason, database)}\n"
        f"       target: {redact_url(url)}"
    )
    return 1  # unreachable; keeps the return type obvious


if __name__ == "__main__":
    raise SystemExit(main())
