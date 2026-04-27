# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the follower→leader stdio↔HTTP bridge watchdog.

The watchdog is the recovery path for a wedged leader: when the leader's
event loop stops pumping SSE responses but doesn't close the stream, the
``async for`` pump sits forever. The watchdog polls ``/api/health`` and
cancels the bridge after N consecutive failures so the MCP client sees
stdio close rather than hang on a tool call.
"""

from __future__ import annotations

import anyio
import httpx
import pytest

from octowright.proxy_bridge import _heartbeat


@pytest.mark.anyio
async def test_heartbeat_cancels_after_consecutive_failures() -> None:
    """Three failed probes in a row → cancel scope fires."""
    fail_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal fail_count
        fail_count += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with anyio.create_task_group() as tg:
        # Patch httpx.AsyncClient to use our mock transport.
        original = httpx.AsyncClient

        def patched(**kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            return original(**kwargs)

        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/api/health", 0.05, 3)
            with anyio.move_on_after(2.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert fail_count >= 3


@pytest.mark.anyio
async def test_heartbeat_resets_on_recovery() -> None:
    """Failures interleaved with success do not trigger cancellation prematurely."""
    seq = iter([200, 503, 200, 503, 503, 200, 503, 503, 503])
    cancellations = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(next(seq))
        except StopIteration:
            return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with anyio.create_task_group() as tg:
        original = httpx.AsyncClient

        def patched(**kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            return original(**kwargs)

        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/api/health", 0.02, 3)
            with anyio.move_on_after(2.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]
            cancellations = 1 if tg.cancel_scope.cancel_called else 0

    assert cancellations == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
