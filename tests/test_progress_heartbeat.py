# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side progress heartbeat (``server/_heartbeat.py``).

The follower bridge injects a synthetic ``progressToken`` into every tools/call
and re-arms its in-flight deadline on each ``notifications/progress`` it sees
(``proxy_supervisor._rearm_deadline``). Nothing on the leader emitted those
pings, so a slow-but-alive tool call blew the flat bridge deadline and surfaced
to the agent as a spurious "Octowright disconnected". This wrapper emits the
missing heartbeat: while a tool handler runs, it sends progress on the injected
token so the deadline stays alive as long as the leader event loop is alive.

Driven directly by setting octowright's request contextvar — no HTTP /
MCP server app needed — mirroring ``test_idempotency_cache.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.server import _heartbeat
from octowright.server import _request_context as _rc


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@contextlib.contextmanager
def _request_context(progress_token: Any, session: Any) -> Iterator[None]:
    """Set the octowright request contextvar to a minimal RequestContext carrying ``progress_token``
    in _meta (as the follower injects) and owned by ``session``."""
    # MCP 2.0 hands handlers a plain dict for _meta, with the spec field
    # snake_cased (`progress_token`), so build the shape the SDK really passes.
    meta = {"progress_token": progress_token} if progress_token is not None else None
    ctx = SimpleNamespace(meta=meta, session=session, request_id="r")
    token = _rc._request_ctx.set(ctx)
    try:
        yield
    finally:
        _rc._request_ctx.reset(token)


def _session() -> SimpleNamespace:
    return SimpleNamespace(send_progress_notification=AsyncMock())


# ─── the keepalive fires ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_slow_tool_gets_progress_pings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.02)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        await asyncio.sleep(0.12)
        return "done"

    sess = _session()
    with _request_context("owpt-42", sess):
        assert await tool() == "done"

    calls = sess.send_progress_notification.await_args_list
    assert len(calls) >= 2  # ~5 pings over 0.12s at 0.02s cadence
    # every ping carries the exact token the follower injected
    for call in calls:
        assert call.kwargs["progress_token"] == "owpt-42"
    # progress is monotonically increasing (MCP requires it)
    progresses = [call.kwargs["progress"] for call in calls]
    assert progresses == sorted(progresses)
    assert len(set(progresses)) == len(progresses)


@pytest.mark.anyio
async def test_fast_tool_gets_no_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.5)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        return "fast"

    sess = _session()
    with _request_context("owpt-1", sess):
        assert await tool() == "fast"
    sess.send_progress_notification.assert_not_awaited()


# ─── passthrough when there is nothing to keep alive ─────────────────────────


@pytest.mark.anyio
async def test_no_progress_token_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        await asyncio.sleep(0.05)
        return "ok"

    sess = _session()
    with _request_context(None, sess):  # no token → nothing to re-arm
        assert await tool() == "ok"
    sess.send_progress_notification.assert_not_awaited()


@pytest.mark.anyio
async def test_no_request_context_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        await asyncio.sleep(0.05)
        return "ok"

    # no request context at all (outside-a-request path)
    assert await tool() == "ok"


def test_sync_tool_returned_unchanged() -> None:
    def tool() -> str:
        return "sync"

    assert _heartbeat._progress_heartbeat(tool) is tool


# ─── failure + teardown leave no lingering beat task ─────────────────────────


@pytest.mark.anyio
async def test_exception_propagates_and_cancels_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        await asyncio.sleep(0.03)
        raise RuntimeError("boom")

    sess = _session()
    with _request_context("owpt-9", sess), pytest.raises(RuntimeError, match="boom"):
        await tool()
    # no beat task left pending on the loop
    await asyncio.sleep(0.03)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == []


@pytest.mark.anyio
async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    started = asyncio.Event()

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        started.set()
        await asyncio.sleep(10)
        return "never"

    sess = _session()

    async def _run() -> None:
        with _request_context("owpt-c", sess):
            await tool()

    task = asyncio.ensure_future(_run())
    await started.wait()
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.02)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == []


# ─── ceiling backstop: a handler wedged past its own timeout stops the pings ──


@pytest.mark.anyio
async def test_ceiling_stops_pinging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_MAX_SECONDS", 0.05)

    @_heartbeat._progress_heartbeat
    async def tool(**_kw: Any) -> str:
        await asyncio.sleep(0.2)  # runs well past the ceiling
        return "eventually"

    sess = _session()
    with _request_context("owpt-ceil", sess):
        assert await tool() == "eventually"

    # pings stopped at the ceiling (~0.05s / 0.01s ≈ 5), not for the full 0.2s (~20)
    assert sess.send_progress_notification.await_count <= 8


# ─── end-to-end: leader heartbeat re-arms the real follower deadline ──────────


@pytest.mark.anyio
async def test_leader_heartbeat_rearms_follower_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The load-bearing glue test. Compose the REAL follower supervisor with the
    REAL leader heartbeat and prove the token round-trips: the follower injects a
    synthetic progressToken on a tools/call; the leader heartbeat (reading that
    exact token from the request context) emits progress on it; feeding that
    progress back into the follower pushes the in-flight deadline out. Without this
    chain a slow tool call trips the flat deadline and surfaces as a false
    disconnect.
    """
    import time as _time

    from octowright.proxy_supervisor import BridgeSupervisor
    from tests._proxy_supervisor_helpers import _progress, _tools_call

    # -- follower side: forward a tools/call, which injects a synthetic token --
    sent: list[Any] = []

    class _LocalWrite:
        async def send(self, message: Any) -> None:
            sent.append(message)

    sup = BridgeSupervisor(local_read=None, local_write=_LocalWrite(), request_timeout_seconds=20.0)
    sup.track_local_message(_tools_call("browser_wait_for", "req-1"))
    injected_token = sup._in_flight["req-1"].progress_token
    assert injected_token  # follower injected a synthetic token
    deadline_before = sup._in_flight["req-1"].deadline

    # -- leader side: the heartbeat reads that exact token and echoes progress --
    monkeypatch.setattr(_heartbeat, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    captured_token: list[Any] = []

    async def _send_progress(**kwargs: Any) -> None:
        captured_token.append(kwargs["progress_token"])

    leader_session = SimpleNamespace(send_progress_notification=_send_progress)

    @_heartbeat._progress_heartbeat
    async def slow_tool(**_kw: Any) -> str:
        await asyncio.sleep(0.08)
        return "ok"

    # The leader receives the follower-injected token as request_context.meta.progressToken.
    with _request_context(injected_token, leader_session):
        assert await slow_tool() == "ok"

    assert captured_token, "leader emitted no progress"
    # -- the token the leader echoed is exactly the one the follower registered --
    assert captured_token[0] == injected_token

    # -- feeding that progress back into the follower re-arms the deadline --
    # simulate leader→follower delivery of one progress ping on the echoed token,
    # far in the future so the re-armed deadline is unambiguously later.
    rearm_at = _time.monotonic() + 1000.0
    monkeypatch.setattr("octowright.proxy_supervisor.time.monotonic", lambda: rearm_at)
    await sup.forward_remote_message(_progress(captured_token[0]))

    assert sup._in_flight["req-1"].deadline > deadline_before
    # synthetic token progress is swallowed — never forwarded to the client
    assert sent == []
