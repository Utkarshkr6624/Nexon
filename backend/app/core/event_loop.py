"""Event loop selection for the psycopg async driver.

psycopg 3 implements async I/O with ``loop.add_reader``, which asyncio's
Windows default (``ProactorEventLoop``) does not provide. Running the app or
the test suite on that loop therefore fails with::

    InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

A ``SelectorEventLoop`` provides the reader callbacks psycopg needs, so this
module selects it on every platform rather than branching: it is already the
default on Linux and macOS, and selecting it explicitly elsewhere keeps the
choice in one place. The loop is also constructed directly instead of via
``asyncio.new_event_loop()``, because that call routes through the active event
loop policy — which, in the test suite, is itself wired back to
``nexus_loop_factory``, so delegating to it would recurse forever.

``nexus_loop_factory`` is also the uvicorn ``--loop`` target, which is how the
server is started — see ``run.py``.
"""

from __future__ import annotations

import asyncio
import selectors

__all__ = ["nexus_loop_factory", "selector_loop_factory"]


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Create a fresh SelectorEventLoop, the loop psycopg requires."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def nexus_loop_factory() -> asyncio.AbstractEventLoop:
    """Create the event loop NEXUS runs on, on every platform."""
    return selector_loop_factory()
