"""The event loop NEXUS runs on, and the policy it has to survive."""

from __future__ import annotations

import asyncio

import pytest

from app.core.event_loop import nexus_loop_factory


class _DelegatingPolicy(asyncio.DefaultEventLoopPolicy):
    """A policy whose ``new_event_loop`` routes straight back to the factory.

    This is what ``tests/conftest.py`` installs, so a factory that obtained its
    loop through ``asyncio.new_event_loop()`` would call itself forever. The
    stock Windows policy never exposes the path, which is why the suite could
    stay green here while recursing on Linux and macOS.
    """

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return nexus_loop_factory()


def test_the_factory_survives_a_policy_that_delegates_back_to_it():
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(_DelegatingPolicy())
    try:
        try:
            loop = asyncio.new_event_loop()
        except RecursionError:
            pytest.fail("nexus_loop_factory recursed through the event loop policy")
    finally:
        asyncio.set_event_loop_policy(previous)

    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_the_factory_builds_the_loop_psycopg_needs():
    """Psycopg's async driver is built on ``loop.add_reader``."""
    loop = nexus_loop_factory()

    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert callable(loop.add_reader)
    finally:
        loop.close()


def test_each_call_yields_a_fresh_loop():
    first = nexus_loop_factory()
    second = nexus_loop_factory()

    try:
        assert first is not second
        assert not first.is_closed()
    finally:
        first.close()
        second.close()
