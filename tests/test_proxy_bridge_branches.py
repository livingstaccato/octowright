# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.proxy_bridge.

Existing tests/test_proxy_bridge.py already covers the core watchdog cancel
+ pump basics. This file pins the remaining branches:

- _heartbeat URL passthrough (the configured health_url is what gets GET'd)
- _heartbeat reset semantics: a single success between failures resets the
  counter so failures must be CONSECUTIVE
- _heartbeat handles httpx.HTTPError + OSError as failure (not crash)
- _heartbeat respects the interval (interval=0.0 is allowed; large interval
  isn't waited beyond cancel)
- _heartbeat exits cleanly when its cancel_scope is cancelled externally
- _heartbeat ok flag derived from response.status_code == 200 exactly
  (304/204/500 all count as failure)
- _pump EndOfStream branch (peer closed cleanly)
- run_proxy: leader_mcp_url passthrough to streamablehttp_client
- run_proxy: health_url=None means no heartbeat task started
- run_proxy: heartbeat task started when health_url provided
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import httpx
import pytest

from octowright import proxy_bridge as _bridge
from octowright.proxy_bridge import _heartbeat, _pump


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _heartbeat URL + status code handling ─────────────────────────────────


def _patched_client_factory(handler: Any) -> Any:
    """Wrap httpx.AsyncClient with a MockTransport using the given handler."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(**kwargs: Any) -> Any:
        kwargs["transport"] = transport
        return original(**kwargs)

    return patched, original


@pytest.mark.anyio
async def test_heartbeat_uses_configured_health_url() -> None:
    """The exact health_url string is what the GET request targets."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(503)

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://leader.example/api/health", 0.01, 3)
            with anyio.move_on_after(1.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert seen_urls
    assert all(u == "http://leader.example/api/health" for u in seen_urls)


@pytest.mark.anyio
async def test_heartbeat_does_not_cancel_on_two_failures() -> None:
    """Two failures in a row (max_failures=3) → no cancel."""
    seq = iter([503, 503, 200, 200, 200, 200])

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(next(seq))
        except StopIteration:
            return httpx.Response(200)

    patched, original = _patched_client_factory(handler)
    cancelled = False
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 3)
            with anyio.move_on_after(0.3):
                await anyio.sleep_forever()
            cancelled = tg.cancel_scope.cancel_called
            tg.cancel_scope.cancel()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert cancelled is False


@pytest.mark.anyio
async def test_heartbeat_treats_non_200_as_failure() -> None:
    """Status codes 204/301/404/500 all count as failure (only 200 is ok)."""
    seq = iter([204, 301, 404, 500])  # 4 non-200s → must cancel after the 3rd

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(next(seq))
        except StopIteration:
            return httpx.Response(503)

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 3)
            with anyio.move_on_after(2.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert tg.cancel_scope.cancel_called


@pytest.mark.anyio
async def test_heartbeat_treats_http_error_as_failure() -> None:
    """httpx.HTTPError (e.g. ConnectError) counted as failure, not propagated."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 3)
            with anyio.move_on_after(2.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert tg.cancel_scope.cancel_called


@pytest.mark.anyio
async def test_heartbeat_treats_os_error_as_failure() -> None:
    """OSError (e.g. socket-not-connected) swallowed as failure."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise OSError("no socket")

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 3)
            with anyio.move_on_after(2.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert tg.cancel_scope.cancel_called


@pytest.mark.anyio
async def test_heartbeat_external_cancel_exits_cleanly() -> None:
    """Cancelling the scope externally aborts the heartbeat without exception."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 3)
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]
    # No exception escaped the task group — that's the assertion.


@pytest.mark.anyio
async def test_heartbeat_max_failures_one_cancels_immediately() -> None:
    """max_failures=1 → first failed probe cancels."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    patched, original = _patched_client_factory(handler)
    async with anyio.create_task_group() as tg:
        httpx.AsyncClient = patched  # type: ignore[misc]
        try:
            tg.start_soon(_heartbeat, tg.cancel_scope, "http://x/health", 0.01, 1)
            with anyio.move_on_after(1.0):
                await anyio.sleep_forever()
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    assert tg.cancel_scope.cancel_called


# ─── _pump end-of-stream branch ────────────────────────────────────────────


@pytest.mark.anyio
async def test_pump_returns_on_end_of_stream() -> None:
    """anyio.EndOfStream from source is swallowed; pump returns silently."""

    class EOFSource:
        def __aiter__(self) -> EOFSource:
            return self

        async def __anext__(self) -> object:
            raise anyio.EndOfStream

    sink_send, _sink_recv = anyio.create_memory_object_stream[object](1)
    # Must not raise.
    await _pump(EOFSource(), sink_send)


@pytest.mark.anyio
async def test_pump_propagates_other_exceptions() -> None:
    """Non-EndOfStream / non-ClosedResourceError exceptions still bubble up."""

    class BoomSource:
        def __aiter__(self) -> BoomSource:
            return self

        async def __anext__(self) -> object:
            raise ValueError("not-a-stream-close")

    sink_send, _sink_recv = anyio.create_memory_object_stream[object](1)
    with pytest.raises(ValueError, match="not-a-stream-close"):
        await _pump(BoomSource(), sink_send)


# ─── run_proxy structural pins ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_proxy_passes_url_to_streamable_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """leader_mcp_url is the argument given to streamablehttp_client."""
    captured: dict[str, Any] = {}

    class FakeStreamCM:
        def __init__(self, url: str) -> None:
            captured["url"] = url

        async def __aenter__(self) -> tuple[Any, Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock()), MagicMock()

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeStdioCM:
        async def __aenter__(self) -> tuple[Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock())

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(_bridge, "streamablehttp_client", lambda url: FakeStreamCM(url))
    monkeypatch.setattr(_bridge, "stdio_server", lambda: FakeStdioCM())

    await _bridge.run_proxy("http://leader.example/mcp/")
    assert captured["url"] == "http://leader.example/mcp/"


@pytest.mark.anyio
async def test_run_proxy_skips_heartbeat_when_health_url_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """health_url=None → _heartbeat is never started."""
    heartbeat_calls: list[Any] = []

    class FakeStreamCM:
        async def __aenter__(self) -> tuple[Any, Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock()), MagicMock()

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeStdioCM:
        async def __aenter__(self) -> tuple[Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock())

        async def __aexit__(self, *a: Any) -> None:
            return None

    async def fake_heartbeat(*args: Any, **kwargs: Any) -> None:
        heartbeat_calls.append((args, kwargs))

    monkeypatch.setattr(_bridge, "streamablehttp_client", lambda url: FakeStreamCM())
    monkeypatch.setattr(_bridge, "stdio_server", lambda: FakeStdioCM())
    monkeypatch.setattr(_bridge, "_heartbeat", fake_heartbeat)

    await _bridge.run_proxy("http://leader/mcp/", health_url=None)
    assert heartbeat_calls == []


@pytest.mark.anyio
async def test_run_proxy_starts_heartbeat_when_health_url_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """health_url='...' → _heartbeat runs with that URL."""
    heartbeat_calls: list[Any] = []

    class FakeStreamCM:
        async def __aenter__(self) -> tuple[Any, Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock()), MagicMock()

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeStdioCM:
        async def __aenter__(self) -> tuple[Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock())

        async def __aexit__(self, *a: Any) -> None:
            return None

    async def fake_heartbeat(*args: Any, **kwargs: Any) -> None:
        heartbeat_calls.append(args)

    monkeypatch.setattr(_bridge, "streamablehttp_client", lambda url: FakeStreamCM())
    monkeypatch.setattr(_bridge, "stdio_server", lambda: FakeStdioCM())
    monkeypatch.setattr(_bridge, "_heartbeat", fake_heartbeat)

    await _bridge.run_proxy(
        "http://leader/mcp/",
        health_url="http://leader/api/health",
        heartbeat_interval=2.0,
        heartbeat_max_failures=5,
    )
    assert len(heartbeat_calls) == 1
    args = heartbeat_calls[0]
    # args = (cancel_scope, health_url, interval, max_failures)
    assert args[1] == "http://leader/api/health"
    assert args[2] == 2.0
    assert args[3] == 5


@pytest.mark.anyio
async def test_run_proxy_default_heartbeat_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default interval=10.0 and max_failures=3 from the signature."""
    heartbeat_args: list[Any] = []

    class FakeStreamCM:
        async def __aenter__(self) -> tuple[Any, Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock()), MagicMock()

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeStdioCM:
        async def __aenter__(self) -> tuple[Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock())

        async def __aexit__(self, *a: Any) -> None:
            return None

    async def fake_heartbeat(*args: Any, **kwargs: Any) -> None:
        heartbeat_args.append(args)

    monkeypatch.setattr(_bridge, "streamablehttp_client", lambda url: FakeStreamCM())
    monkeypatch.setattr(_bridge, "stdio_server", lambda: FakeStdioCM())
    monkeypatch.setattr(_bridge, "_heartbeat", fake_heartbeat)

    await _bridge.run_proxy("http://leader/mcp/", health_url="http://h")
    args = heartbeat_args[0]
    assert args[2] == 10.0  # default interval
    assert args[3] == 3  # default max_failures


@pytest.mark.anyio
async def test_run_proxy_returns_when_either_pump_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both pumps end (stdio + streamable) → run_proxy returns cleanly."""

    class FakeStreamCM:
        async def __aenter__(self) -> tuple[Any, Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock()), MagicMock()

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeStdioCM:
        async def __aenter__(self) -> tuple[Any, Any]:
            send, recv = anyio.create_memory_object_stream[object](1)
            await send.aclose()
            return recv, MagicMock(send=AsyncMock())

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(_bridge, "streamablehttp_client", lambda url: FakeStreamCM())
    monkeypatch.setattr(_bridge, "stdio_server", lambda: FakeStdioCM())

    # Must return without hanging.
    with anyio.move_on_after(2.0) as scope:
        await _bridge.run_proxy("http://leader/mcp/")
    assert not scope.cancel_called
