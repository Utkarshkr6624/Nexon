"""Shared helpers for the NEXUS developer scripts.

These scripts run before — and independently of — the backend virtualenv, so
nothing here may import from ``backend/`` or from a third-party package except
``psycopg``, which is imported lazily by the two database helpers.

The ``.env`` reader is deliberately hand-written instead of using
``python-dotenv``: ``bootstrap.py`` has to run with a bare interpreter, before
any dependency is installed. It implements the same subset the application
relies on (see ``backend/app/core/config.py``, which uses pydantic-settings).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit, urlunsplit

__all__ = [
    "BACKEND_DIR",
    "ENV_EXAMPLE_FILE",
    "ENV_FILE",
    "FRONTEND_DIR",
    "REPO_ROOT",
    "db_name_from_url",
    "fail",
    "info",
    "import_psycopg",
    "load_settings",
    "ok",
    "read_env_file",
    "redact_url",
    "redact_message",
    "repo_python",
    "resolve_database_url",
    "venv_python",
    "warn",
    "to_psycopg_dsn",
    "with_database",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"

#: SQLAlchemy driver prefixes that raw psycopg does not understand.
_SQLALCHEMY_DRIVERS = ("+psycopg", "+psycopg2", "+asyncpg", "+pg8000", "+aiosqlite")

# Windows consoles still default to a legacy code page, where a non-ASCII
# character in a message comes out as mojibake or raises UnicodeEncodeError.
# Printed messages are kept ASCII as well; this is the safety net.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def info(message: str) -> None:
    """Print a neutral progress line."""
    print(f"  {message}", flush=True)


def ok(message: str) -> None:
    """Print a success line."""
    print(f"[ok]   {message}", flush=True)


def warn(message: str) -> None:
    """Print a warning to stderr."""
    print(f"[warn] {message}", file=sys.stderr, flush=True)


def fail(message: str) -> NoReturn:
    """Print an actionable error and exit with a non-zero status."""
    print(f"[error] {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------
def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file.

    Comments, blank lines and blank values are skipped; a single or double
    quoted value is unquoted. Malformed lines are ignored rather than fatal —
    the same thing pydantic-settings does — because this file may legitimately
    carry comments that document a variable rather than set it.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and value:
            values[key] = value
    return values


def load_settings(*, include_environ: bool = True) -> dict[str, str]:
    """Return the effective settings: ``.env`` overlaid by the real environment.

    The real environment wins so a single command can override one value, e.g.
    ``DATABASE_URL=... python scripts/wait_for_db.py``. Values that are empty in
    ``.env`` are treated as absent, matching ``app/core/config.py``.
    """
    settings = read_env_file(ENV_FILE)
    if not include_environ:
        return settings
    for key, value in os.environ.items():
        if value:
            settings[key] = value
    return settings


def resolve_database_url(
    settings: dict[str, str],
    *,
    key: str = "DATABASE_URL",
    database_suffix: str = "",
) -> str:
    """Return the SQLAlchemy URL for ``key``, assembling one when it is unset.

    Mirrors ``Settings.sqlalchemy_database_uri``: an explicit URL wins, otherwise
    the ``POSTGRES_*`` parts are combined. ``database_suffix`` reproduces
    ``Settings.test_sqlalchemy_database_uri`` (``nexus`` -> ``nexus_test``) for
    callers that key off ``TEST_DATABASE_URL``.
    """
    explicit = settings.get(key, "").strip()
    if explicit:
        return explicit

    user = settings.get("POSTGRES_USER", "nexus")
    password = settings.get("POSTGRES_PASSWORD", "nexus")
    host = settings.get("POSTGRES_HOST", "127.0.0.1")
    port = settings.get("POSTGRES_PORT", "5432")
    database = f"{settings.get('POSTGRES_DB', 'nexus')}{database_suffix}"
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def to_psycopg_dsn(url: str) -> str:
    """Convert a SQLAlchemy URL into a libpq connection string psycopg accepts.

    Two project-specific adjustments are applied:

    * the ``+driver`` suffix is dropped, because libpq has no notion of it;
    * a ``localhost`` host is rewritten to ``127.0.0.1``. On Windows
      ``localhost`` resolves to ``::1`` first, which psycopg's socket layer
      cannot use, so a connection would hang until it times out instead of
      failing fast.
    """
    dsn = url.strip()
    for driver in _SQLALCHEMY_DRIVERS:
        # The dialect stays; only the driver is dropped, so
        # `postgresql+psycopg://` becomes `postgresql://`.
        dsn = dsn.replace(f"{driver}://", "://", 1)
    parts = urlsplit(dsn)
    if parts.scheme == "postgres":
        parts = parts._replace(scheme="postgresql")
    if parts.hostname == "localhost":
        userinfo, at, _ = parts.netloc.rpartition("@")
        hostinfo = f"127.0.0.1:{parts.port}" if parts.port else "127.0.0.1"
        parts = parts._replace(netloc=f"{userinfo}@{hostinfo}" if at else hostinfo)
    return urlunsplit(parts)


def db_name_from_url(url: str) -> str:
    """Return the database name in a PostgreSQL URL."""
    return urlsplit(to_psycopg_dsn(url)).path.lstrip("/")


def redact_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``***``.

    Script output is pasted into issues and chat; a DSN must never carry a
    credential into either.
    """
    parts = urlsplit(to_psycopg_dsn(url))
    if parts.password is None:
        return url
    userinfo, _, hostinfo = parts.netloc.rpartition("@")
    username, _, _ = userinfo.partition(":")
    return urlunsplit(parts._replace(netloc=f"{username}:***@{hostinfo}"))


def redact_message(message: str, url: str) -> str:
    """Strip a credential that libpq echoed back inside an error message.

    psycopg puts the whole connection string in some errors, so redacting only
    the string this script prints is not enough. Both the percent-encoded and
    the plain ``user:password@`` spellings are covered.
    """
    dsn = to_psycopg_dsn(url)
    parts = urlsplit(dsn)
    if parts.password is None:
        return message
    plain = f"{parts.username}:{parts.password}@"
    encoded = f"{quote(parts.username or '', safe='')}:{quote(parts.password, safe='')}@"
    return (
        message.replace(url, redact_url(url))
        .replace(dsn, redact_url(url))
        .replace(encoded, f"{parts.username}:***@")
        .replace(plain, f"{parts.username}:***@")
    )


def with_database(url: str, database: str) -> str:
    """Return ``url`` pointed at a different database."""
    parts = urlsplit(to_psycopg_dsn(url))
    return urlunsplit(parts._replace(path=f"/{database}"))


# ---------------------------------------------------------------------------
# Interpreters and dependencies
# ---------------------------------------------------------------------------
def venv_python(venv_dir: Path) -> Path | None:
    """Return the interpreter of a virtualenv, or ``None`` if it is not usable.

    Handles the Windows layout (``Scripts/python.exe``) and the POSIX one
    (``bin/python``) so callers do not have to branch on the platform.
    """
    candidates = (venv_dir / "Scripts" / "python.exe", venv_dir / "bin" / "python")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def repo_python() -> Path:
    """Return the interpreter to use for the backend virtualenv.

    Prefers ``backend/.venv`` so every command in the project runs against the
    same environment; falls back to the interpreter running this script.
    """
    candidate = venv_python(BACKEND_DIR / ".venv")
    return candidate if candidate is not None else Path(sys.executable)


def import_psycopg():
    """Import and return the ``psycopg`` module, or exit with a remedy.

    The module is returned rather than a single symbol so callers can still
    reach ``psycopg.sql`` and name ``psycopg.errors`` in a message.
    """
    try:
        import psycopg  # noqa: PLC0415
    except ImportError:  # pragma: no cover - environment dependent
        fail(
            "psycopg is not available to this interpreter "
            f"({sys.executable}).\n"
            "       Run:  python scripts/bootstrap.py\n"
            "       ...or invoke the script with the project interpreter:\n"
            "             backend/.venv/Scripts/python scripts/wait_for_db.py"
        )
    return psycopg
