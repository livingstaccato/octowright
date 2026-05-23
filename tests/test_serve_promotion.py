# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Decision-flow tests for ``cli.serve._serve_async``.

These exercise the leader-vs-follower election plus the promotion path that
fires when a previously-healthy leader dies mid-session. The actual
``_run_leader`` and ``_run_follower`` are stubbed; we only verify which one
gets called and how many times.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from octowright import singleton
from octowright.cli import serve as _serve
from tests.conftest import _free_port

_DAEMON_PORT = _free_port()
_DAEMON_INFO = singleton.LeaderInfo(
    # Use the test process PID so the real ``is_stale()`` (which does a kill -0
    # liveness check) reports the fake daemon as alive. Otherwise the recheck
    # path would see "stale daemon, spawn replacement" and pollute the log.
    pid=os.getpid(),
    http_host="127.0.0.1",
    http_port=_DAEMON_PORT,
    mcp_url=f"http://127.0.0.1:{_DAEMON_PORT}/mcp/",
    started_at=0.0,
)


@pytest.fixture
def call_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace _run_leader / _run_follower / spawn_daemon with recording stubs.

    The fake spawn_daemon flips ``read_lock`` to return the daemon info on
    subsequent reads — mimicking the real "daemon writes lockfile, recheck
    finds healthy leader" behaviour. Without this, the recheck path would
    keep finding a dead lock and spawn a second daemon, polluting the log.
    """
    log: list[str] = []
    daemon_alive = {"v": False}

    async def fake_leader(**_kwargs: Any) -> None:
        log.append("leader")

    async def fake_follower(_url: str) -> None:
        log.append("follower")

    def fake_spawn(**_kwargs: Any) -> int:
        log.append("spawn-daemon")
        daemon_alive["v"] = True
        return _DAEMON_INFO.pid

    async def fake_wait(timeout: float = 10.0, poll_seconds: float = 0.2) -> singleton.LeaderInfo | None:
        return _DAEMON_INFO

    # Note: tests that need a custom read_lock should monkeypatch it AFTER
    # this fixture runs. The default read_lock here returns the daemon info
    # once spawned, None otherwise — only used by the recheck step.
    def default_read_lock() -> singleton.LeaderInfo | None:
        return _DAEMON_INFO if daemon_alive["v"] else None

    async def alive_after_spawn(_info: Any, timeout: float = 2.0) -> bool:
        return daemon_alive["v"]

    monkeypatch.setattr(_serve, "_run_leader", fake_leader)
    monkeypatch.setattr(_serve, "_run_follower", fake_follower)
    monkeypatch.setattr(singleton, "read_lock", default_read_lock)
    monkeypatch.setattr(singleton, "probe_http_alive", alive_after_spawn)
    from octowright import daemonize as _daemon

    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn)
    monkeypatch.setattr(_daemon, "wait_for_daemon", fake_wait)
    return log


def _kwargs() -> dict[str, Any]:
    return {
        "http_host": None,
        "http_port": None,
        "no_http": False,
        "keep_alive": True,
        "idle_grace": None,
    }


@pytest.mark.asyncio
async def test_no_lock_means_spawn_daemon_then_follow(call_log: list[str]) -> None:
    """No live leader → spawn a detached daemon → become follower of it.

    The fixture's default read_lock returns None until the spawn flips the
    flag, so this is the no-lock-on-first-read scenario by construction.
    """
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["spawn-daemon", "follower"]


@pytest.mark.asyncio
async def test_no_singleton_skips_lock_check(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """--no-singleton: skip every lock check and go straight to leader mode."""

    def boom() -> None:
        raise AssertionError("read_lock must not be called when no_singleton=True")

    monkeypatch.setattr(singleton, "read_lock", boom)
    await _serve._serve_async(no_singleton=True, **_kwargs())
    assert call_log == ["leader"]


@pytest.mark.asyncio
async def test_stale_pid_spawns_replacement_daemon(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """A lock with a dead PID is treated as absent — spawn a fresh daemon.

    First read returns the stale lock; later reads (from the recheck path)
    return the freshly-spawned daemon's info via the fixture's flip.
    """
    stale_port = _free_port()
    stale = singleton.LeaderInfo(
        pid=2_000_000_000,
        http_host="127.0.0.1",
        http_port=stale_port,
        mcp_url=f"http://127.0.0.1:{stale_port}/mcp/",
        started_at=0.0,
    )
    reads = [stale]
    daemon = _DAEMON_INFO

    def reads_then_daemon() -> singleton.LeaderInfo | None:
        # First call: stale. Subsequent: daemon (after spawn flips it).
        if reads:
            return reads.pop(0)
        return daemon

    monkeypatch.setattr(singleton, "read_lock", reads_then_daemon)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["spawn-daemon", "follower"]


@pytest.mark.asyncio
async def test_live_pid_but_dead_http_spawns_replacement_daemon(
    monkeypatch: pytest.MonkeyPatch, call_log: list[str]
) -> None:
    """PID alive but HTTP probe fails — wedged leader, spawn replacement daemon."""
    wedged_port = _free_port()
    wedged = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=wedged_port,
        mcp_url=f"http://127.0.0.1:{wedged_port}/mcp/",
        started_at=0.0,
    )
    reads = [wedged]
    daemon = _DAEMON_INFO
    probe_results = [False]  # first probe: wedged-False; later: True (daemon healthy)

    def reads_then_daemon() -> singleton.LeaderInfo | None:
        if reads:
            return reads.pop(0)
        return daemon

    async def probe_first_dead(_info: Any, timeout: float = 2.0) -> bool:
        if probe_results:
            return probe_results.pop(0)
        return True

    monkeypatch.setattr(singleton, "read_lock", reads_then_daemon)
    monkeypatch.setattr(singleton, "probe_http_alive", probe_first_dead)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["spawn-daemon", "follower"]


@pytest.mark.asyncio
async def test_healthy_leader_means_follower(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """Live PID + responsive HTTP = follower mode, no daemon spawn."""
    port = _free_port()
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=port,
        mcp_url=f"http://127.0.0.1:{port}/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def always_alive(_info: Any, timeout: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", always_alive)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["follower"]


@pytest.mark.asyncio
async def test_follower_spawns_replacement_when_leader_dies_during_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial probe says leader alive; bridge raises; recheck shows it gone → spawn fresh daemon."""
    port = _free_port()
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=port,
        mcp_url=f"http://127.0.0.1:{port}/mcp/",
        started_at=0.0,
    )

    # State machine: first read returns the live leader; second (recheck) returns None.
    reads: list[singleton.LeaderInfo | None] = [info, None]
    monkeypatch.setattr(singleton, "read_lock", lambda: reads.pop(0))

    async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", fake_probe)

    log: list[str] = []

    def fake_spawn(**_kwargs: Any) -> int:
        log.append("spawn-daemon")
        return 99999

    async def fake_wait(timeout: float = 10.0, poll_seconds: float = 0.2) -> singleton.LeaderInfo | None:
        return info

    async def dying_follower(_url: str) -> None:
        log.append("follower")
        raise ConnectionError("leader vanished")

    from octowright import daemonize as _daemon

    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn)
    monkeypatch.setattr(_daemon, "wait_for_daemon", fake_wait)
    monkeypatch.setattr(_serve, "_run_follower", dying_follower)

    await _serve._serve_async(no_singleton=False, **_kwargs())
    # First the bridge to the still-alive leader; bridge dies; recheck shows
    # leader gone (lockfile None on second read); spawn replacement daemon.
    assert log == ["follower", "spawn-daemon"]


@pytest.mark.asyncio
async def test_follower_does_not_spawn_daemon_if_leader_still_healthy_on_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge ended cleanly but the leader is still up — exit, do not spawn replacement."""
    port = _free_port()
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=port,
        mcp_url=f"http://127.0.0.1:{port}/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def always_alive(_info: Any, timeout: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", always_alive)

    log: list[str] = []

    def fake_spawn(**_kwargs: Any) -> int:
        log.append("spawn-daemon")
        return 99999

    async def fake_wait(timeout: float = 10.0, poll_seconds: float = 0.2) -> singleton.LeaderInfo | None:
        return info

    async def clean_follower(_url: str) -> None:
        log.append("follower")
        # clean exit, no exception

    from octowright import daemonize as _daemon

    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn)
    monkeypatch.setattr(_daemon, "wait_for_daemon", fake_wait)
    monkeypatch.setattr(_serve, "_run_follower", clean_follower)

    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert log == ["follower"]  # NO daemon spawn
