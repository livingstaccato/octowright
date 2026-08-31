# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Restart must reclaim the spawn port from a split-brain leader.

Split-brain: two ``octowright serve`` daemons alive, on different ports (e.g. the
lockfile leader bumped to 6287 while another holds the canonical 6286). Restart
kills the lockfile leader but then spawns on the canonical port, which the *other*
leader still holds -> the bind fails and the daemon stays down. Worse, that other
leader's command line can lack ``--http-port`` (it bound the default), so the
port-scoped pgrep can't identify its port and never targets it.

The fix reclaims the spawn port by the actual listening socket, not by parsing
``--http-port`` from command lines. These tests pin that.

``TestSplitBrainPrevention`` covers the other half: restart used to be the one
spawner that never took the leader-election lock, so it could CREATE the very
split-brain the tests above recover from. See ``_spawn_election_lock``.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from click.testing import CliRunner

from octowright.cli import port_owner
from octowright.cli import restart as restart_mod
from octowright.cli._root import cli


def _procs() -> list[tuple[int, str]]:
    # A = lockfile leader on 6287 (bumped). B = split-brain leader on canonical
    # 6286 with NO --http-port in its command line (bound the default).
    return [
        (111, "/x/.venv/bin/python /x/.venv/bin/octowright serve --daemon-mode --http-port 6287"),
        (222, "/x/.venv/bin/python /x/.venv/bin/octowright serve --daemon-mode"),
        (333, "/x/.venv/bin/python /x/.venv/bin/octowright serve"),  # bare follower, never killed
    ]


def test_collect_target_pids_includes_canonical_port_squatter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lockfile points at the bumped leader on 6287.
    monkeypatch.setattr(restart_mod, "_leader_pid_from_lock", lambda: 111)
    monkeypatch.setattr(restart_mod, "_restart_target_port", lambda: 6287)
    monkeypatch.setattr(restart_mod, "_list_process_commands", _procs)
    # The canonical spawn port (6286) is actually held by the split-brain leader 222.
    monkeypatch.setattr(port_owner, "_pid_listening_on_port", lambda _port: 222)

    pids = restart_mod._collect_target_pids(kill_followers=False, spawn_port=6286)

    assert 111 in pids, "lockfile leader (6287) must be killed"
    assert 222 in pids, "canonical-port squatter (6286) must be killed even without --http-port in cmdline"
    assert 333 not in pids, "bare follower must be spared"


def test_octowright_leader_on_port_verifies_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(port_owner, "_pid_listening_on_port", lambda _port: 222)
    assert port_owner.octowright_leader_on_port(6286, _procs) == 222


def test_octowright_leader_on_port_ignores_non_octowright_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    # Something else holds the port -> must NOT be returned for killing.
    monkeypatch.setattr(port_owner, "_pid_listening_on_port", lambda _port: 999)
    procs = [(999, "/usr/bin/some-other-server --port 6286")]
    assert port_owner.octowright_leader_on_port(6286, lambda: procs) is None


def test_octowright_leader_on_port_none_when_port_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(port_owner, "_pid_listening_on_port", lambda _port: None)
    assert port_owner.octowright_leader_on_port(6286, _procs) is None


class TestSplitBrainPrevention:
    """Restart must hold the election lock across kill -> spawn -> confirm.

    Observed live 2026-08-30: two healthy leaders 15s apart. Restart SIGKILLs
    the leader, every follower's bridge drops, each runs
    ``_respawn_if_leader_gone`` -- which DOES take the lock, correctly sees no
    leader, and spawns one on the canonical port. Restart, holding no lock,
    then spawns its own, which port-walks to a bumped port. Two leaders, and
    ``_health_candidates`` also probes the lockfile endpoint so restart printed
    "daemon healthy" and exited 0.
    """

    def _stub(self, monkeypatch: pytest.MonkeyPatch, events: list[str], *, lock_times_out: bool = False) -> None:
        @contextlib.contextmanager
        def _lock(*_a: Any, **_kw: Any) -> Any:
            if lock_times_out:
                raise TimeoutError("held by a peer")
            events.append("lock_acquire")
            try:
                yield
            finally:
                events.append("lock_release")

        monkeypatch.setattr(restart_mod.singleton, "election_lock", _lock)

        def _stop(*_a: Any, **_kw: Any) -> tuple[int, int, list[int]]:
            events.append("stop_leader")
            return (1, 0, [])

        monkeypatch.setattr(restart_mod, "_stop_leader", _stop)
        monkeypatch.setattr(restart_mod, "_reap_browsers", lambda *_a: events.append("reap"))
        monkeypatch.setattr(restart_mod, "_wait_for_port_free", lambda *_a: (events.append("port_free"), True)[1])
        monkeypatch.setattr(restart_mod, "_spawn_daemon", lambda *_a: (events.append("spawn"), 4242)[1])
        monkeypatch.setattr(
            restart_mod,
            "_wait_for_health",
            lambda *_a: (events.append("health"), "http://127.0.0.1:6286/")[1],
        )

    def test_the_lock_is_taken_before_the_kill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Load-bearing ordering: acquiring AFTER the kill leaves the exact
        window the followers spawn in, so the lock would prevent nothing."""
        events: list[str] = []
        self._stub(monkeypatch, events)
        result = CliRunner().invoke(cli, ["restart", "--timeout", "1"])
        assert result.exit_code == 0, result.output
        assert events.index("lock_acquire") < events.index("stop_leader")

    def test_the_lock_is_held_until_the_new_daemon_is_confirmed_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Releasing after spawn but before health would let a waiting follower
        acquire the lock, still see no leader, and spawn the competitor."""
        events: list[str] = []
        self._stub(monkeypatch, events)
        result = CliRunner().invoke(cli, ["restart", "--timeout", "1"])
        assert result.exit_code == 0, result.output
        assert events.index("spawn") < events.index("lock_release")
        assert events.index("health") < events.index("lock_release")

    def test_no_start_does_not_take_the_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing of ours spawns, so there is nothing to serialize -- and a
        follower replacing the stopped leader is that path doing its job."""
        events: list[str] = []
        self._stub(monkeypatch, events)
        result = CliRunner().invoke(cli, ["restart", "--no-start", "--timeout", "1"])
        assert result.exit_code == 0, result.output
        assert "lock_acquire" not in events
        assert "stop_leader" in events

    def test_lock_contention_warns_but_still_restarts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Restart is the recovery command, reached when the daemon is wedged.
        Refusing to run because a peer is mid-election would make it useless
        exactly when it is needed, so it degrades to the old behaviour loudly.
        """
        events: list[str] = []
        self._stub(monkeypatch, events, lock_times_out=True)
        result = CliRunner().invoke(cli, ["restart", "--timeout", "1"])
        assert result.exit_code == 0, result.output
        assert "election lock" in result.output
        assert "spawn" in events, "a contended lock must not abort the restart"
