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


# ─── split-brain guard: never spawn a second leader beside a healthy one ──────


@pytest.mark.anyio
async def test_respawn_defers_when_canonical_port_serves_octowright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Split-brain guard: even when the lockfile probe says the leader is gone, a
    healthy octowright already answering on the canonical HTTP port must prevent
    spawning a second (bumped-port) competing daemon."""
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    spawn_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(_sn, "read_lock", lambda *_a, **_k: None)  # lockfile says gone
    monkeypatch.setattr(_sn, "is_stale", lambda _i: True)

    @asynccontextmanager
    async def fake_lock(*_a: Any, **_k: Any) -> Any:
        yield

    monkeypatch.setattr(_sn, "async_election_lock", fake_lock)
    monkeypatch.setattr(_daemon, "spawn_daemon", lambda **k: spawn_calls.append(k) or 1)
    monkeypatch.setattr(_serve.click, "echo", lambda *_a, **_k: None)

    async def canonical_has_leader(_host: Any, _port: Any) -> bool:
        return True

    monkeypatch.setattr(_serve, "_canonical_port_serves_octowright", canonical_has_leader)

    await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)

    assert spawn_calls == []  # guard prevented the competing spawn


@pytest.mark.anyio
async def test_respawn_spawns_when_canonical_port_is_not_octowright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: when the lockfile leader is gone AND the canonical port is not a
    live octowright, the replacement daemon IS spawned (normal failover)."""
    from types import SimpleNamespace

    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    spawn_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(_sn, "read_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(_sn, "is_stale", lambda _i: True)

    @asynccontextmanager
    async def fake_lock(*_a: Any, **_k: Any) -> Any:
        yield

    monkeypatch.setattr(_sn, "async_election_lock", fake_lock)
    monkeypatch.setattr(_daemon, "spawn_daemon", lambda **k: spawn_calls.append(k) or 1)
    monkeypatch.setattr(_daemon, "wait_for_daemon", lambda *_a, **_k: _awaitable(SimpleNamespace(pid=1)))
    monkeypatch.setattr(_serve.click, "echo", lambda *_a, **_k: None)

    async def canonical_free(_host: Any, _port: Any) -> bool:
        return False

    monkeypatch.setattr(_serve, "_canonical_port_serves_octowright", canonical_free)

    await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)

    assert len(spawn_calls) == 1  # normal failover still works


async def _awaitable(value: Any) -> Any:
    return value


@pytest.mark.anyio
async def test_canonical_port_serves_octowright_classifies_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe returns True only for a 200 /api/health with an octowright body,
    False for non-octowright responses and unreachable ports."""
    import httpx

    class _Resp:
        def __init__(self, status: int, body: Any) -> None:
            self.status_code = status
            self._body = body

        def json(self) -> Any:
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    class _Client:
        def __init__(self, resp: Any = None, exc: Any = None) -> None:
            self._resp = resp
            self._exc = exc

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def get(self, _url: str) -> Any:
            if self._exc is not None:
                raise self._exc
            return self._resp

    def client_factory(resp: Any = None, exc: Any = None):  # type: ignore[no-untyped-def]
        return lambda **_k: _Client(resp=resp, exc=exc)

    # live octowright
    monkeypatch.setattr(httpx, "AsyncClient", client_factory(resp=_Resp(200, {"ok": True, "version": "0.10.0"})))
    assert await _serve._canonical_port_serves_octowright(None, None) is True

    # 200 but not octowright shape
    monkeypatch.setattr(httpx, "AsyncClient", client_factory(resp=_Resp(200, {"status": "other"})))
    assert await _serve._canonical_port_serves_octowright(None, None) is False

    # non-200
    monkeypatch.setattr(httpx, "AsyncClient", client_factory(resp=_Resp(503, {"ok": True})))
    assert await _serve._canonical_port_serves_octowright(None, None) is False

    # unreachable
    monkeypatch.setattr(httpx, "AsyncClient", client_factory(exc=httpx.ConnectError("down")))
    assert await _serve._canonical_port_serves_octowright(None, None) is False
