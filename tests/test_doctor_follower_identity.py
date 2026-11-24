# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Doctor details and bridge summaries must agree about a follower's identity."""

from __future__ import annotations

import pytest

from octowright import bridge_state, doctor_daemon, process_identity


@pytest.mark.parametrize(
    ("namespace", "recorded_start", "host_alive", "live", "owner"),
    [
        (222, 100, False, True, None),  # foreign PID absent on host
        (222, 100, True, True, None),  # foreign PID collides with host process
        (111, 100, True, False, None),  # same namespace, reused PID
        (111, 200, False, False, None),  # same namespace, dead PID
        (111, 200, True, True, "Claude Code"),
        (None, None, True, True, "Claude Code"),  # legacy snapshot
    ],
)
async def test_doctor_preserves_identity_partition_and_only_attributes_host_followers(
    monkeypatch: pytest.MonkeyPatch,
    namespace: int | None,
    recorded_start: int | None,
    host_alive: bool,
    live: bool,
    owner: str | None,
) -> None:
    snap = {
        "follower_pid": 100,
        "follower_version": "0.27.0",
        "last_error": "leader throttled this follower HTTP 429",
        "pid_namespace": namespace,
        "process_start_ticks": recorded_start,
        "ts": 1.0,
    }
    state = {"followers": {"100": snap}, "events": []}

    async def leader() -> str:
        return "0.27.0"

    monkeypatch.setattr(doctor_daemon, "_running_leader_version", leader)
    monkeypatch.setattr(bridge_state, "read_state", lambda _path: state)
    monkeypatch.setattr(process_identity, "pid_namespace", lambda: 111)
    monkeypatch.setattr(process_identity, "process_start_ticks", lambda _pid: 200)
    monkeypatch.setattr("octowright.singleton.pid_is_alive", lambda _pid: host_alive)
    monkeypatch.setattr(doctor_daemon, "pid_is_alive", lambda _pid: host_alive)
    monkeypatch.setattr(
        "octowright.process_reaper.list_processes",
        lambda: [(100, 50, "/usr/bin/octowright serve"), (50, 1, "/usr/bin/claude")],
    )

    summary = bridge_state.summarize_state(state)
    check = await doctor_daemon.check_followers()
    assert summary["follower_count"] == int(live)
    assert check.data["live_followers"] == int(live)
    assert check.data["dead_followers_ignored"] == int(not live)
    rows = check.data["followers"]
    assert len(rows) == int(live)
    assert check.data["throttled_followers"] == int(live)
    assert check.status == ("warn" if live else "ok")
    if live:
        assert rows[0]["client"] == owner
        assert rows[0]["throttled"] is True
        assert "reconnect" in rows[0]["reconnect_step"].lower()
        assert "pid 100" in check.detail
        assert "429" in check.detail
