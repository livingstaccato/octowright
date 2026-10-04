# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``restart`` removes the lockfile only if it names a process it stopped.

Between the kill and the removal a follower's respawn (or any other spawner)
may already have written a successor's lock. Unlinking it unconditionally made
that live leader invisible: the next client found no leader and a free
canonical port and spawned another beside it.
"""

from __future__ import annotations

import pytest

from octowright import singleton
from octowright.cli import restart as restart_mod


def _lock(pid: int) -> singleton.LeaderInfo:
    return singleton.LeaderInfo(
        pid=pid, http_host="127.0.0.1", http_port=6299, mcp_url="http://127.0.0.1:6299/mcp/", started_at=1.0
    )


@pytest.fixture
def stopped_pids(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    removed: list[str] = []
    monkeypatch.setattr(restart_mod, "_collect_target_pids", lambda *a, **kw: {100, 101})
    monkeypatch.setattr(restart_mod, "browser_pids_owned_by", lambda _pids: [])
    monkeypatch.setattr(restart_mod, "_send_signal", lambda _pid, _sig: None)
    monkeypatch.setattr(restart_mod, "_escalate_survivors", lambda _pids, _timeout: [])
    # Both module globals the removal reads are stubbed, so nothing here can
    # reach the developer's real lockfile.
    monkeypatch.setattr(singleton, "remove_lock", lambda *_a, **_kw: removed.append("removed"))
    return removed


def test_a_successors_lock_survives_the_stop(monkeypatch: pytest.MonkeyPatch, stopped_pids: list[str]) -> None:
    monkeypatch.setattr(singleton, "read_lock", lambda *_a, **_kw: _lock(555))

    restart_mod._stop_leader(1.0)

    assert stopped_pids == []


def test_the_stopped_leaders_lock_is_removed(monkeypatch: pytest.MonkeyPatch, stopped_pids: list[str]) -> None:
    monkeypatch.setattr(singleton, "read_lock", lambda *_a, **_kw: _lock(101))

    restart_mod._stop_leader(1.0)

    assert stopped_pids == ["removed"]
