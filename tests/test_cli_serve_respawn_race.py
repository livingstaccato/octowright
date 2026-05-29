# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from octowright.cli import serve as _serve


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_concurrent_respawn_followers_spawn_one_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    replacement = SimpleNamespace(pid=777, mcp_url="http://127.0.0.1:8765/mcp/")
    current: Any | None = None
    in_lock = False
    outside_reads = 0
    both_followers_saw_dead_leader = anyio.Event()
    election_lock = anyio.Lock()
    spawn_calls: list[dict[str, Any]] = []

    def fake_read_lock() -> Any | None:
        nonlocal outside_reads
        if in_lock:
            return current
        outside_reads += 1
        if outside_reads == 2:
            both_followers_saw_dead_leader.set()
        return None

    @asynccontextmanager
    async def fake_election_lock() -> Any:
        nonlocal in_lock
        await both_followers_saw_dead_leader.wait()
        async with election_lock:
            in_lock = True
            try:
                yield
            finally:
                in_lock = False

    async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
        return _info is replacement

    def fake_spawn_daemon(**kwargs: Any) -> int:
        nonlocal current
        spawn_calls.append(kwargs)
        current = replacement
        return replacement.pid

    async def fake_wait_for_daemon(timeout: float = 10.0, poll_seconds: float = 0.2) -> Any:
        return replacement

    monkeypatch.setattr(_sn, "read_lock", fake_read_lock)
    monkeypatch.setattr(_sn, "is_stale", lambda _info: False)
    monkeypatch.setattr(_sn, "probe_http_alive", fake_probe)
    monkeypatch.setattr(_sn, "async_election_lock", fake_election_lock)
    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn_daemon)
    monkeypatch.setattr(_daemon, "wait_for_daemon", fake_wait_for_daemon)
    monkeypatch.setattr(_serve.click, "echo", lambda *_args, **_kwargs: None)

    async def respawn_once() -> None:
        await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)

    async with anyio.create_task_group() as tg:
        tg.start_soon(respawn_once)
        tg.start_soon(respawn_once)

    assert len(spawn_calls) == 1
