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

from octowright import proxy_bridge as _bridge
from octowright.proxy_bridge import _heartbeat, _pump


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


@pytest.mark.anyio
async def test_pump_forwards_messages_until_source_closes() -> None:
    source_send, source_recv = anyio.create_memory_object_stream[object](10)
    sink_send, sink_recv = anyio.create_memory_object_stream[object](10)
    await source_send.send({"hello": "world"})
    await source_send.send("done")
    await source_send.aclose()

    await _pump(source_recv, sink_send)
    await sink_send.aclose()

    got = [await sink_recv.receive(), await sink_recv.receive()]
    assert got == [{"hello": "world"}, "done"]


@pytest.mark.anyio
async def test_pump_raises_when_source_yields_exception_value() -> None:
    source_send, source_recv = anyio.create_memory_object_stream[object](10)
    sink_send, _sink_recv = anyio.create_memory_object_stream[object](10)
    await source_send.send(RuntimeError("boom"))
    await source_send.aclose()

    with pytest.raises(RuntimeError, match="boom"):
        await _pump(source_recv, sink_send)


@pytest.mark.anyio
async def test_pump_swallows_closed_resource_error() -> None:
    class ClosedSource:
        def __aiter__(self) -> ClosedSource:
            return self

        async def __anext__(self) -> object:
            raise anyio.ClosedResourceError

    sink_send, _sink_recv = anyio.create_memory_object_stream[object](1)
    await _pump(ClosedSource(), sink_send)


@pytest.mark.anyio
async def test_run_proxy_delegates_to_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_supervised_proxy(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(_bridge, "run_supervised_proxy", fake_run_supervised_proxy)

    await _bridge.run_proxy(
        "http://leader/mcp/",
        health_url="http://leader/api/health",
        heartbeat_interval=3.0,
        heartbeat_max_failures=7,
    )

    assert calls == [
        {
            "leader_mcp_url": "http://leader/mcp/",
            "health_url": "http://leader/api/health",
            "heartbeat_interval": 3.0,
            "heartbeat_max_failures": 7,
        }
    ]
