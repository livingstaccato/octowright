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


@pytest.fixture
def call_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace _run_leader / _run_follower with stubs that just record their name."""
    log: list[str] = []

    async def fake_leader(**_kwargs: Any) -> None:
        log.append("leader")

    async def fake_follower(_url: str) -> None:
        log.append("follower")

    monkeypatch.setattr(_serve, "_run_leader", fake_leader)
    monkeypatch.setattr(_serve, "_run_follower", fake_follower)
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
async def test_no_lock_means_leader(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    monkeypatch.setattr(singleton, "read_lock", lambda: None)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["leader"]


@pytest.mark.asyncio
async def test_no_singleton_skips_lock_check(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """--no-singleton: skip every lock check and go straight to leader mode."""

    def boom() -> None:
        raise AssertionError("read_lock must not be called when no_singleton=True")

    monkeypatch.setattr(singleton, "read_lock", boom)
    await _serve._serve_async(no_singleton=True, **_kwargs())
    assert call_log == ["leader"]


@pytest.mark.asyncio
async def test_stale_pid_takes_over(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """A lock with a dead PID is treated as absent — we boot as leader."""
    info = singleton.LeaderInfo(
        pid=2_000_000_000,
        http_host="127.0.0.1",
        http_port=18900,
        mcp_url="http://127.0.0.1:18900/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def never(_info: Any, timeout: float = 2.0) -> bool:
        raise AssertionError("probe_http_alive should not run when PID is stale")

    monkeypatch.setattr(singleton, "probe_http_alive", never)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["leader"]


@pytest.mark.asyncio
async def test_live_pid_but_dead_http_takes_over(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """PID alive but HTTP probe fails — leader is wedged, take over."""
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=18901,
        mcp_url="http://127.0.0.1:18901/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
        return False

    monkeypatch.setattr(singleton, "probe_http_alive", fake_probe)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["leader"]


@pytest.mark.asyncio
async def test_healthy_leader_means_follower(monkeypatch: pytest.MonkeyPatch, call_log: list[str]) -> None:
    """Live PID + responsive HTTP = follower mode, no leader spawn."""
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=18902,
        mcp_url="http://127.0.0.1:18902/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", fake_probe)
    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert call_log == ["follower"]


@pytest.mark.asyncio
async def test_follower_promotes_when_leader_dies_during_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial probe says leader is alive; bridge raises; recheck shows leader gone → promote."""
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=18903,
        mcp_url="http://127.0.0.1:18903/mcp/",
        started_at=0.0,
    )

    # State machine: first read returns the live leader; second (recheck) returns None.
    reads: list[singleton.LeaderInfo | None] = [info, None]
    monkeypatch.setattr(singleton, "read_lock", lambda: reads.pop(0))

    async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
        # Only called for the FIRST read (initial decision). The recheck path
        # short-circuits on read_lock() returning None and never probes again.
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", fake_probe)

    log: list[str] = []

    async def fake_leader(**_kwargs: Any) -> None:
        log.append("leader")

    async def dying_follower(_url: str) -> None:
        log.append("follower")
        raise ConnectionError("leader vanished")

    monkeypatch.setattr(_serve, "_run_leader", fake_leader)
    monkeypatch.setattr(_serve, "_run_follower", dying_follower)

    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert log == ["follower", "leader"]


@pytest.mark.asyncio
async def test_follower_does_not_promote_if_leader_still_healthy_on_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge ended cleanly but the leader is still up — exit, do not promote."""
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=18904,
        mcp_url="http://127.0.0.1:18904/mcp/",
        started_at=0.0,
    )
    monkeypatch.setattr(singleton, "read_lock", lambda: info)

    async def always_alive(_info: Any, timeout: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(singleton, "probe_http_alive", always_alive)

    log: list[str] = []

    async def fake_leader(**_kwargs: Any) -> None:
        log.append("leader")

    async def clean_follower(_url: str) -> None:
        log.append("follower")
        # clean exit, no exception

    monkeypatch.setattr(_serve, "_run_leader", fake_leader)
    monkeypatch.setattr(_serve, "_run_follower", clean_follower)

    await _serve._serve_async(no_singleton=False, **_kwargs())
    assert log == ["follower"]  # NO promotion
