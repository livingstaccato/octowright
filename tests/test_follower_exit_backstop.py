# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follower exit backstop: the stdio bridge must not outlive its MCP client.

A follower that keeps running after its client closes stdin is what accumulated
orphaned ``octowright serve`` processes across idle-restart churn. When stdin
EOFs, the bridge cancels itself and arms a daemon-thread hard-exit timer so a
wedged remote teardown can't keep the process alive past its client.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage

from octowright import proxy_runtime as runtime


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_backstop_fires_after_grace_and_is_daemon() -> None:
    """The timer is a daemon and calls exit_fn(0) once the grace elapses."""
    calls: list[int] = []
    timer = runtime._arm_follower_exit_backstop(0.05, exit_fn=lambda code: calls.append(code))
    try:
        assert timer.daemon is True
        time.sleep(0.25)
        assert calls == [0]
    finally:
        timer.cancel()


def test_backstop_cancelled_does_not_exit() -> None:
    """A graceful shutdown (process exit) abandons the daemon timer — modeled by cancel()."""
    calls: list[int] = []
    timer = runtime._arm_follower_exit_backstop(0.3, exit_fn=lambda code: calls.append(code))
    timer.cancel()
    time.sleep(0.4)
    assert calls == []


@pytest.mark.anyio
async def test_stdin_eof_arms_exit_backstop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing stdin (client gone) cancels the bridge AND arms the hard-exit backstop."""
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(runtime, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    # Spy instead of arming a real os._exit timer in the test process.
    armed: list[float] = []
    monkeypatch.setattr(runtime, "_arm_follower_exit_backstop", lambda grace, **_kw: armed.append(grace))

    @asynccontextmanager
    async def quiet_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, _remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        try:
            yield (remote_read_recv, remote_write_send, lambda: "sess")
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(runtime, "streamablehttp_client", quiet_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    async with anyio.create_task_group() as tg:

        async def run() -> None:
            await runtime.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp/")

        tg.start_soon(run)
        await anyio.sleep(0.05)  # let the bridge connect
        await local_in_send.aclose()  # stdin EOF → the MCP client is gone
        with anyio.fail_after(2.0):
            while not armed:
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()

    assert armed == [runtime.FOLLOWER_EXIT_BACKSTOP_SECONDS]
