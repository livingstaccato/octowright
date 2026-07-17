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
"""

from __future__ import annotations

import pytest

from octowright.cli import port_owner
from octowright.cli import restart as restart_mod


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
