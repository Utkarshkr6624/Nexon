"""Development and container entrypoint for the NEXUS backend.

``python run.py`` is the supported way to start the API. It exists so that the
event loop is selected in exactly one place: psycopg's async driver cannot run
on the ProactorEventLoop that asyncio defaults to on Windows, and
``app.core.event_loop`` resolves that platform difference for the server, the
Alembic migrations and the test suite alike.

Configuration comes from the environment (``NEXUS_HOST``, ``NEXUS_PORT``,
``NEXUS_RELOAD``); everything else is read through ``app.core.config``.
"""

from __future__ import annotations

import os

import uvicorn

#: uvicorn resolves ``--loop`` as an import path, so the factory has to be
#: importable by name rather than passed as a callable.
LOOP_TARGET = "app.core.event_loop:nexus_loop_factory"

_TRUTHY = {"1", "true", "yes", "on"}


def main() -> None:
    """Start the API server from environment configuration."""
    uvicorn.run(
        "app.main:app",
        host=os.getenv("NEXUS_HOST", "127.0.0.1"),
        port=int(os.getenv("NEXUS_PORT", "8000")),
        reload=os.getenv("NEXUS_RELOAD", "false").lower() in _TRUTHY,
        # The request-context middleware already emits one access log line per
        # request; uvicorn's own would be a duplicate.
        access_log=False,
        loop=LOOP_TARGET,
    )


if __name__ == "__main__":
    main()
