# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The reconnect loop under a leader-side 429 (and 410), end to end.

A transient 429 path used to sleep the flat attempt backoff and report the
transport's raw ExceptionGroup; days of that looked like a flaky network. The
loop must instead honor the leader's Retry-After (a longer ask wins over the
local backoff), name the throttle with both versions in the bridge snapshot,
become loudly structural after ``THROTTLE_LOUD_STREAK`` refusals, and put the
same wording in the JSON-RPC error a client gets while the leader is
unreachable — so "Octowright bridge error" says what is wrong, not just
"timed out".

Harness: ``streamable_http_client`` is replaced by a factory that raises the
exact ExceptionGroup the real transport produces for a refused POST, and the
rest of the loop (snapshots, health probes, the flat backoff) is real or
clock-free, so what varies is only the observable the assertions read.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import anyio
import httpx2
import pytest
from mcp.shared.message import SessionMessage

from octowright import proxy_retry
from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _tools_call


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _refusal_group(status: int, *, retry_after: str | None = None) -> ExceptionGroup:
    request = httpx2.Request("POST", "http://127.0.0.1:6299/mcp/")
    headers = [("retry-after", retry_after)] if retry_after is not None else []
    response = httpx2.Response(status, request=request, headers=headers)
    error = httpx2.HTTPStatusError(f"status {status}", request=request, response=response)
    return ExceptionGroup("unhandled errors in a TaskGroup", [error])


class _RefusingLeader:
    """Every connect raises the refusal the real transport surfaces — until
    ``accepting`` flips it to handing out in-memory sessions."""

    def __init__(self, status: int = 429, *, retry_after: str | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        self.attempts = 0
        self.accepting = False
        self.sessions: list[tuple[Any, Any]] = []

    def client(self, _url: Any, **_kwargs: Any) -> Any:
        self.attempts += 1
        if not self.accepting:
            raise _refusal_group(self.status, retry_after=self.retry_after)
        return self._accept()

    @contextlib.asynccontextmanager
    async def _accept(self):  # type: ignore[no-untyped-def]
        read_send, read_recv = anyio.create_memory_object_stream[Any](20)
        write_send, write_recv = anyio.create_memory_object_stream[Any](20)
        self.sessions.append((read_send, write_recv))
        try:
            yield (read_recv, write_send)
        finally:
            await read_send.aclose()
            await write_send.aclose()


def _wire(monkeypatch: pytest.MonkeyPatch, leader: _RefusingLeader) -> tuple[Any, Any, list[dict[str, Any]]]:
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(runtime, "resolve_leader_generation", lambda: "leader-A")
    monkeypatch.setattr(runtime, "streamable_http_client", leader.client)
    monkeypatch.setattr(runtime, "reconnect_delay", lambda attempt, *, max_delay: 0.0)

    async def _leader_version(_url: Any) -> str:
        return "0.26.0-test"

    monkeypatch.setattr(runtime, "_leader_health_version", _leader_version)

    async def _no_notifications(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(runtime, "consume_leader_notifications", _no_notifications)

    snapshots: list[dict[str, Any]] = []

    async def _snapshot(**kwargs: Any) -> bool:
        snapshots.append(kwargs)
        return True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)

    from contextlib import asynccontextmanager

    from mcp.shared.message import SessionMessage

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](20)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](20)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)
    return local_in_send, local_out_recv, snapshots


async def _await_snapshots(snapshots: list[dict[str, Any]], count: int, *, timeout: float = 10.0) -> None:
    with anyio.fail_after(timeout):
        while len(snapshots) < count:
            await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_a_429_loop_is_paced_by_retry_after_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _RefusingLeader(429, retry_after="0.05")
    _local_in, _local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        await _await_snapshots(snapshots, 1)
        started = anyio.current_time()
        await _await_snapshots(snapshots, proxy_retry.THROTTLE_LOUD_STREAK)
        elapsed_4 = anyio.current_time() - started
        tg.cancel_scope.cancel()
    # Retry-After=0.05 per cycle, local backoff pinned to 0: honoring it paces
    # the cycles at ~50ms each; an ignored header collapses this to ~0. The
    # 0.8 tolerance covers the event loop waking a millisecond early.
    assert elapsed_4 >= 4 * 0.05 * 0.8, f"Retry-After was not honored (4 cycles in {elapsed_4:.3f}s)"
    first = snapshots[0]["last_error"]
    assert "leader throttled this follower" in first
    assert "HTTP 429" in first
    assert "leader v0.26.0-test" in snapshots[-1]["last_error"]
    loud = snapshots[-1]["last_error"]
    assert "persistent follower/leader mismatch" in loud
    assert "not a flaky connection" in loud


@pytest.mark.anyio
async def test_a_410_says_reconnect_instead_of_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _RefusingLeader(410, retry_after="0.05")
    _local_in, _local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        await _await_snapshots(snapshots, 1)
        tg.cancel_scope.cancel()
    last_error = snapshots[0]["last_error"]
    assert "leader refused to serve this follower" in last_error
    assert "HTTP 410" in last_error
    assert "reconnect this MCP client" in last_error


@pytest.mark.anyio
async def test_a_client_call_during_throttle_gets_the_named_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client-visible surface: 'Octowright bridge error' names the throttle
    and both versions instead of "leader session unavailable; retry"."""
    monkeypatch.setattr("octowright.defaults.BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.15)
    leader = _RefusingLeader(429, retry_after="0.05")
    local_in, local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        await _await_snapshots(snapshots, 1)  # the throttle state is established
        await local_in.send(_tools_call("octowright_status", "call-1"))
        with anyio.fail_after(5.0):
            response = await local_out.receive()
        tg.cancel_scope.cancel()
    root = getattr(response.message, "root", response.message)
    text = root.error.message
    assert "leader throttled this follower" in text
    assert "HTTP 429" in text
    assert "vs leader v" in text


@pytest.mark.anyio
async def test_a_successful_connect_clears_the_throttle_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror-image failure mode: a follower that throttled and then
    reconnects must not keep "last_error: throttled" in every later snapshot,
    or the summary reads as still-broken."""
    leader = _RefusingLeader(429, retry_after="0.05")
    _local_in, _local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        await _await_snapshots(snapshots, 2)
        assert snapshots[-1]["last_error"] is not None
        leader.accepting = True
        with anyio.fail_after(10.0):
            while not any(snap["last_error"] is None for snap in snapshots[2:]):
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()
    assert leader.sessions, "the follower reconnected once the leader accepted"


@pytest.mark.anyio
async def test_a_timeout_during_throttle_names_the_cause() -> None:
    """The timeout text gets the same cause, so 'Octowright bridge error:
    request timed out' during a throttle no longer reads as a hung leader."""
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=out_send, request_timeout_seconds=20.0)
    sup.connect_status_hint = (
        "leader throttled this follower via its new-session rate limit (HTTP 429),"
        " retry in 10.0s; follower v0.26.0 vs leader v0.26.0"
    )
    now = time.monotonic()
    sup._in_flight["call-1"] = supervisor.InFlightRequest(
        request_id="call-1", method="tools/call", started_at=now, deadline=now - 1.0
    )
    await sup._expire_overdue(now, None)
    response = await out_recv.receive()
    root = getattr(response.message, "root", response.message)
    text = root.error.message
    assert "timed out while waiting for leader response" in text
    assert "leader throttled this follower" in text
