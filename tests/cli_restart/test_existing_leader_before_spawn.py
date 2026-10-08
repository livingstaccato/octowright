# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``restart`` looks for a live leader again just before it spawns one.

Restart holds the election lock from before the kill until its new daemon is
healthy, and a client that starts meanwhile waits on that lock. If the wait
outlasts the client's budget (lock timeout plus a readiness wait), the client
falls back to an inline leader, which binds a port and writes the lockfile --
on the canonical port if it is free by then, else the next one. Restart then
spawned its own daemon regardless, and two leaders ran; ``_health_candidates``
also probes the lockfile endpoint, so it could even report the inline leader as
its own daemon. So after waiting for the port, and before spawning, restart
re-runs the split-brain guard the follower respawn uses (lockfile + canonical
port) and never starts a second leader beside one it finds.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from click.testing import CliRunner

from octowright import singleton
from octowright.cli import _leader_election
from octowright.cli import restart as restart_mod
from octowright.cli._root import cli


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    found: tuple[str, int | None] | None,
    port_free: bool = True,
) -> None:
    @contextlib.contextmanager
    def _lock(*_a: Any, **_kw: Any) -> Any:
        events.append("lock_acquire")
        try:
            yield
        finally:
            events.append("lock_release")

    monkeypatch.setattr(restart_mod.singleton, "election_lock", _lock)
    monkeypatch.setattr(restart_mod, "_stop_leader", lambda *_a, **_kw: (1, 0, []))
    monkeypatch.setattr(restart_mod, "_reap_browsers", lambda *_a: None)

    def _port(*_a: Any) -> bool:
        events.append("port_wait")
        return port_free

    def _probe(*_a: Any) -> tuple[str, int | None] | None:
        events.append("probe")
        return found

    monkeypatch.setattr(restart_mod, "_wait_for_port_free", _port)
    monkeypatch.setattr(restart_mod, "_live_leader_before_spawn", _probe)
    monkeypatch.setattr(restart_mod, "_spawn_daemon", lambda *_a: (events.append("spawn"), 4242)[1])
    monkeypatch.setattr(restart_mod, "_wait_for_health", lambda *_a: "http://127.0.0.1:6286/")


def test_a_leader_found_elsewhere_is_reported_and_not_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The inline fallback took the next port: spawning on the canonical one
    would be the second leader. Restart says what it found and fails, since it
    did not get a daemon on the port it was asked for."""
    events: list[str] = []
    _stub(monkeypatch, events, found=("http://127.0.0.1:6287/", 555))

    result = CliRunner().invoke(cli, ["restart", "--http-port", "6286", "--timeout", "1"])

    assert "spawn" not in events, result.output
    assert result.exit_code == 1, result.output
    assert "http://127.0.0.1:6287/" in result.output
    assert "555" in result.output
    assert "not spawning" in result.output


def test_a_leader_found_on_the_requested_port_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _stub(monkeypatch, events, found=("http://127.0.0.1:6286/", 777))

    result = CliRunner().invoke(cli, ["restart", "--http-host", "127.0.0.1", "--http-port", "6286", "--timeout", "1"])

    assert "spawn" not in events, result.output
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:6286/" in result.output
    assert "not spawning" in result.output


def test_a_busy_port_held_by_a_live_leader_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The port never read free because a leader took it: that is not the
    'still busy' failure, and the message must name the leader instead."""
    events: list[str] = []
    _stub(monkeypatch, events, found=("http://127.0.0.1:6286/", 777), port_free=False)

    result = CliRunner().invoke(cli, ["restart", "--http-host", "127.0.0.1", "--http-port", "6286", "--timeout", "1"])

    assert "spawn" not in events, result.output
    assert result.exit_code == 0, result.output
    assert "still busy" not in result.output


def test_the_probe_runs_after_the_port_wait_and_under_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the wait, because that wait is the window a client gives up in;
    under the lock, so nothing the guard rules out can start before the spawn."""
    events: list[str] = []
    _stub(monkeypatch, events, found=None)

    result = CliRunner().invoke(cli, ["restart", "--timeout", "1"])

    assert result.exit_code == 0, result.output
    assert events.index("port_wait") < events.index("probe") < events.index("spawn")
    assert events.index("probe") < events.index("lock_release")


# ── the probe itself ─────────────────────────────────────────────────────────


def _info(port: int, pid: int) -> singleton.LeaderInfo:
    return singleton.LeaderInfo(
        pid=pid, http_host="127.0.0.1", http_port=port, mcp_url=f"http://127.0.0.1:{port}/mcp/", started_at=1.0
    )


def _probes(monkeypatch: pytest.MonkeyPatch, *, lock: singleton.LeaderInfo | None, canonical: bool) -> list[Any]:
    seen: list[Any] = []

    async def _alive(_sn: Any) -> singleton.LeaderInfo | None:
        return lock

    async def _canonical(host: str | None, port: int | None) -> bool:
        seen.append((host, port))
        return canonical

    monkeypatch.setattr(_leader_election, "_probe_alive_leader", _alive)
    monkeypatch.setattr(_leader_election, "_canonical_port_serves_octowright", _canonical)
    return seen


def test_probe_returns_the_lockfile_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, lock=_info(6287, 555), canonical=False)
    assert restart_mod._live_leader_before_spawn("127.0.0.1", 6286) == ("http://127.0.0.1:6287/", 555)


def test_probe_falls_back_to_the_canonical_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader that has not published a readable lockfile still answers on the
    port: the same case the follower respawn refuses to spawn beside."""
    seen = _probes(monkeypatch, lock=None, canonical=True)
    assert restart_mod._live_leader_before_spawn("127.0.0.1", 6286) == ("http://127.0.0.1:6286/", None)
    assert seen == [("127.0.0.1", 6286)]


def test_probe_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _probes(monkeypatch, lock=None, canonical=False)
    assert restart_mod._live_leader_before_spawn("127.0.0.1", 6286) is None
