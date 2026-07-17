# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Split-brain must not FORM when a leader dies.

When a leader dies, every follower runs ``_respawn_if_leader_gone``. The election
flock serialises the spawn *decision*, but the fix pins a stronger property: the
lock is held until the spawned daemon is confirmed up. If ``wait_for_daemon``
ran *after* releasing the lock (the bug), a second follower would acquire the
lock, still see no leader, and spawn a competitor that port-walks to a bumped
port — two leaders. Holding the lock across the wait makes the next follower see
the healthy leader and defer.

The same property is required on the initial-serve election path.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from octowright import daemonize as _daemon
from octowright import singleton
from octowright.cli import _leader_election as _election
from octowright.cli import serve as _serve

_PORT = 6286
_INFO = singleton.LeaderInfo(
    pid=1, http_host="127.0.0.1", http_port=_PORT, mcp_url=f"http://127.0.0.1:{_PORT}/mcp/", started_at=0.0
)


@pytest.fixture
def lock_tracer(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], dict[str, int]]:
    """Trace election-lock hold depth and record spawn/wait against it."""
    events: list[str] = []
    depth = {"v": 0}

    @contextlib.asynccontextmanager
    async def fake_lock(*_a: Any, **_k: Any) -> Any:
        depth["v"] += 1
        events.append("lock-acquire")
        try:
            yield
        finally:
            depth["v"] -= 1
            events.append("lock-release")

    def fake_spawn(**_k: Any) -> int:
        events.append(f"spawn@depth={depth['v']}")
        return 1

    async def fake_wait(timeout: float = 10.0, poll_seconds: float = 0.2) -> Any:
        events.append(f"wait@depth={depth['v']}")
        return _INFO

    monkeypatch.setattr(singleton, "async_election_lock", fake_lock)
    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn)
    monkeypatch.setattr(_daemon, "wait_for_daemon", fake_wait)
    return events, depth


async def _no_leader(_sn: Any) -> None:
    return None


async def _canonical_free(*_a: Any, **_k: Any) -> bool:
    return False


@pytest.mark.anyio
async def test_respawn_waits_for_daemon_while_holding_election_lock(
    lock_tracer: tuple[list[str], dict[str, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    events, _ = lock_tracer
    monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader)
    monkeypatch.setattr(_election, "_canonical_port_serves_octowright", _canonical_free)

    await _serve._respawn_if_leader_gone(http_host="127.0.0.1", http_port=_PORT, idle_grace=None)

    # Both spawn and the wait must happen while the lock is held (depth==1),
    # and the wait must precede the release — else a racing follower spawns a competitor.
    assert "spawn@depth=1" in events, events
    assert "wait@depth=1" in events, events
    assert events.index("wait@depth=1") < events.index("lock-release"), events


@pytest.mark.anyio
async def test_ensure_leader_waits_for_daemon_while_holding_election_lock(
    lock_tracer: tuple[list[str], dict[str, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    events, _ = lock_tracer

    async def _adopt_none(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader)
    monkeypatch.setattr(_election, "_adopt_canonical_leader", _adopt_none)

    result = await _serve._ensure_leader_or_inline(
        {"keep_alive": False}, http_host="127.0.0.1", http_port=_PORT, idle_grace=None
    )

    assert result is _INFO
    assert "wait@depth=1" in events, events
    assert events.index("wait@depth=1") < events.index("lock-release"), events


@pytest.mark.anyio
async def test_respawn_defers_on_election_lock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the election lock times out, another follower holds it (spawning) — this
    one must defer quietly, not crash or spawn a competitor."""
    spawned = {"v": False}

    def timeout_lock(*_a: Any, **_k: Any) -> Any:
        # ``async with timeout_lock()`` calls this, which raises before returning a
        # context manager — modelling a flock acquisition that times out.
        raise TimeoutError("held by another follower")

    def fake_spawn(**_k: Any) -> int:
        spawned["v"] = True
        return 1

    monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader)
    monkeypatch.setattr(singleton, "async_election_lock", timeout_lock)
    monkeypatch.setattr(_daemon, "spawn_daemon", fake_spawn)

    # Must not raise.
    await _serve._respawn_if_leader_gone(http_host="127.0.0.1", http_port=_PORT, idle_grace=None)
    assert spawned["v"] is False, "must not spawn a competitor when the lock is held elsewhere"
