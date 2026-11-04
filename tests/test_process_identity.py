# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A follower recorded from another pid namespace, or whose pid was reused,
must not be judged by pid liveness alone (dead-follower reaper, bridge-state
pruning)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import bridge_state, process_identity
from octowright.process_identity import PID_NAMESPACE_KEY, PROCESS_START_KEY, follower_verdict

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")

_DEAD_PID = 1_000_000_000  # no such process


def _never_called(_pid: int) -> bool:
    raise AssertionError("a foreign-namespace pid must not be looked up")


def test_parse_start_ticks_survives_parentheses_in_comm() -> None:
    fields_after_comm = ["S"] + [str(n) for n in range(4, 22)] + ["987654", "0", "0"]
    raw = "4242 (we(ird) name)) " + " ".join(fields_after_comm) + "\n"
    assert process_identity.parse_start_ticks(raw) == 987654


@pytest.mark.parametrize("raw", ["", "no paren here", "1 (x) S 2 3"])
def test_parse_start_ticks_rejects_malformed(raw: str) -> None:
    assert process_identity.parse_start_ticks(raw) is None


@linux_only
def test_self_identity_matches_proc_lookup_by_pid() -> None:
    identity = process_identity.self_identity()
    assert identity[PID_NAMESPACE_KEY] == process_identity.pid_namespace(os.getpid())
    assert identity[PROCESS_START_KEY] == process_identity.process_start_ticks(os.getpid())


def test_non_linux_records_nothing_and_ignores_recorded_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_identity, "_linux", lambda: False)
    assert process_identity.self_identity() == {}
    snap = {PID_NAMESPACE_KEY: 1, PROCESS_START_KEY: 1}
    assert follower_verdict(_DEAD_PID, snap, lambda _pid: False) == "dead"
    assert follower_verdict(os.getpid(), snap, lambda _pid: True) == "alive"


def test_legacy_snapshot_is_judged_by_pid_alone() -> None:
    assert follower_verdict(_DEAD_PID, {}, lambda _pid: False) == "dead"
    assert follower_verdict(_DEAD_PID, {}, lambda _pid: True) == "alive"


@linux_only
def test_foreign_namespace_is_never_looked_up() -> None:
    own = process_identity.pid_namespace()
    assert own is not None
    snap = {PID_NAMESPACE_KEY: own + 1, PROCESS_START_KEY: 1}
    assert follower_verdict(_DEAD_PID, snap, _never_called) == "foreign"


@linux_only
def test_reused_pid_is_dead_and_same_process_is_alive() -> None:
    identity = process_identity.self_identity()
    assert follower_verdict(os.getpid(), identity, lambda _pid: True) == "alive"
    reused = dict(identity, **{PROCESS_START_KEY: identity[PROCESS_START_KEY] + 1})
    assert follower_verdict(os.getpid(), reused, lambda _pid: True) == "dead"


@linux_only
def test_record_snapshot_carries_identity_only_for_own_pid(tmp_path) -> None:
    path = tmp_path / "bridge-state.json"
    for pid in (os.getpid(), _DEAD_PID):
        bridge_state.record_snapshot(
            path=path,
            follower_pid=pid,
            remote_url=None,
            remote_session_id=None,
            last_error=None,
            in_flight=0,
            reconnect_attempts=0,
            request_timeouts=0,
        )
    followers = json.loads(path.read_text())["followers"]
    assert followers[str(os.getpid())][PID_NAMESPACE_KEY] == process_identity.pid_namespace()
    # The fake pid was written by a writer that is not that pid: no identity.
    assert PID_NAMESPACE_KEY not in followers[str(_DEAD_PID)]


@linux_only
def test_writer_does_not_prune_a_foreign_namespace_follower(tmp_path) -> None:
    own = process_identity.pid_namespace()
    assert own is not None
    path = tmp_path / "bridge-state.json"
    foreign = {"remote_session_id": "sandboxed", PID_NAMESPACE_KEY: own + 1}
    path.write_text(json.dumps({"followers": {str(_DEAD_PID): foreign}, "events": []}))
    bridge_state.record_snapshot(
        path=path,
        follower_pid=os.getpid(),
        remote_url=None,
        remote_session_id=None,
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    assert str(_DEAD_PID) in json.loads(path.read_text())["followers"]


# --- the dead-follower reaper -------------------------------------------------


def _reap(monkeypatch: pytest.MonkeyPatch, tmp_path, followers: dict, sessions: dict, *, active: set | None = None):
    import octowright.http.app as app_mod
    import octowright.server as server_mod
    from octowright import defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    path.write_text(json.dumps({"followers": followers, "events": []}))
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    manager = MagicMock()
    manager._server_instances = sessions
    fake_mcp = MagicMock()
    fake_mcp.session_manager = manager
    monkeypatch.setattr(server_mod, "mcp", fake_mcp)
    tracker = None
    if active is not None:
        tracker = MagicMock()
        tracker.active_ids.return_value = set(active)
    monkeypatch.setattr(app_mod, "get_mcp_session_tracker", lambda: tracker)
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=MagicMock()))
    return json.loads(path.read_text())["followers"]


def _transport() -> MagicMock:
    transport = MagicMock()
    transport.is_terminated = False
    transport.terminate = AsyncMock()
    return transport


@linux_only
def test_reaper_leaves_a_foreign_namespace_follower_alone(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    own = process_identity.pid_namespace()
    assert own is not None
    transport = _transport()
    snap = {"remote_session_id": "sandboxed-sid", PID_NAMESPACE_KEY: own + 1, PROCESS_START_KEY: 5}
    followers = _reap(monkeypatch, tmp_path, {str(_DEAD_PID): snap}, {"sandboxed-sid": transport})
    transport.terminate.assert_not_awaited()
    assert str(_DEAD_PID) in followers


@linux_only
def test_reaper_reaps_a_reused_pid(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    identity = process_identity.self_identity()
    snap = dict(identity, remote_session_id="old-sid")
    snap[PROCESS_START_KEY] = identity[PROCESS_START_KEY] + 1  # an earlier holder of this pid
    transport = _transport()
    sessions = {"old-sid": transport}
    followers = _reap(monkeypatch, tmp_path, {str(os.getpid()): snap}, sessions)
    transport.terminate.assert_awaited_once()
    assert "old-sid" not in sessions
    assert str(os.getpid()) not in followers


def test_reaper_spares_a_session_that_is_still_sending(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A legacy snapshot (no identity) whose pid looks dead -- an older follower
    in a sandbox -- is not reaped while its session is active."""
    transport = _transport()
    snap = {"remote_session_id": "busy-sid"}
    followers = _reap(monkeypatch, tmp_path, {str(_DEAD_PID): snap}, {"busy-sid": transport}, active={"busy-sid"})
    transport.terminate.assert_not_awaited()
    assert str(_DEAD_PID) in followers


def test_reaper_still_reaps_a_quiet_dead_follower_with_a_tracker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    transport = _transport()
    sessions = {"dead-sid": transport}
    followers = _reap(
        monkeypatch, tmp_path, {str(_DEAD_PID): {"remote_session_id": "dead-sid"}}, sessions, active={"x"}
    )
    transport.terminate.assert_awaited_once()
    assert followers == {}
