# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pruning manifest entries stranded by a dead daemon generation.

``remove_session`` only runs on a graceful close, so every entry open when a
daemon is SIGKILLed (``octowright restart``, a crash, an OOM kill) is stranded
forever — nothing reaps them. Observed live: 16 entries, of which 10 belonged
to five dead daemons, one of them a pid an ``octowright restart`` had killed
that same day.

"Orphaned" is decided by the recorded ``daemon_pid``, NOT by absence from the
live pool. At leader boot the pool is empty, so pool-absence alone would flag
every entry including ones a concurrently-live daemon owns. The pid test is
also deliberately conservative in the safe direction: if the recorded pid is
still alive the entry is KEPT, so a recycled pid can at worst leave a stale
entry, never delete a live one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octowright import session_manifest as sm


def _entry(session_id: str, daemon_pid: int) -> dict[str, object]:
    return {
        "session_id": session_id,
        "kind": "chromium",
        "label": None,
        "profile": None,
        "user_data_dir": None,
        "log_path": f"/tmp/{session_id}.jsonl",
        "launched_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
        "state": "open",
        "daemon_pid": daemon_pid,
    }


def _write(path: Path, entries: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": 1, "sessions": entries}), encoding="utf-8")


def _dead_pid() -> int:
    """A pid that is provably not running.

    Probes through ``singleton.pid_is_alive`` rather than ``os.kill(pid, 0)``
    directly: on Windows a dead pid raises ``OSError`` (WinError 87) instead of
    ``ProcessLookupError``, so a raw probe both crashes this helper and would
    mask the very bug these tests exist to catch.
    """
    from octowright.singleton import pid_is_alive

    for candidate in range(4_194_300, 4_194_000, -1):
        if not pid_is_alive(candidate):
            return candidate
    pytest.skip("could not find a provably-dead pid")
    raise AssertionError  # unreachable


def test_prunes_entries_from_a_dead_daemon(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    dead = _dead_pid()
    _write(path, {"a": _entry("a", dead), "b": _entry("b", dead)})

    removed = sm.prune_dead_daemon_entries(path=path)

    assert sorted(removed) == ["a", "b"]
    assert sm.read_manifest(path)["sessions"] == {}


def test_keeps_entries_owned_by_the_current_process(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write(path, {"mine": _entry("mine", os.getpid())})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "mine" in sm.read_manifest(path)["sessions"]


def test_keeps_entries_whose_daemon_is_still_alive(tmp_path: Path) -> None:
    # A concurrently-live daemon (e.g. --no-singleton sharing this manifest path)
    # must not have its entries pruned by another process booting.
    path = tmp_path / "manifest.json"
    _write(path, {"other": _entry("other", os.getpid())})

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid() + 1, path=path) == []
    assert "other" in sm.read_manifest(path)["sessions"]


def test_mixed_manifest_prunes_only_the_dead(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    dead = _dead_pid()
    _write(path, {"live": _entry("live", os.getpid()), "stale": _entry("stale", dead)})

    assert sm.prune_dead_daemon_entries(path=path) == ["stale"]
    assert list(sm.read_manifest(path)["sessions"]) == ["live"]


def test_keeps_entries_with_no_recorded_pid(tmp_path: Path) -> None:
    # Pre-schema entries carry no daemon_pid; without proof they are dead, keep
    # them rather than guess (the conservative direction).
    path = tmp_path / "manifest.json"
    entry = _entry("legacy", 1)
    del entry["daemon_pid"]
    _write(path, {"legacy": entry})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "legacy" in sm.read_manifest(path)["sessions"]


def test_no_write_when_nothing_to_prune(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write(path, {"mine": _entry("mine", os.getpid())})
    before = path.stat().st_mtime_ns

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert path.stat().st_mtime_ns == before, "manifest rewritten despite no changes"


def test_missing_manifest_is_a_noop(tmp_path: Path) -> None:
    assert sm.prune_dead_daemon_entries(path=tmp_path / "absent.json") == []


def test_liveness_probe_routes_through_the_canonical_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the Windows break: an ad-hoc ``os.kill(pid, 0)`` probe reports a
    dead pid as ALIVE on Windows (it raises OSError/WinError 87, not
    ProcessLookupError), so nothing would ever be pruned there. Asserting we go
    through ``singleton.pid_is_alive`` catches that on any platform."""
    import octowright.singleton as singleton_mod

    calls: list[int] = []

    def fake_pid_is_alive(pid: int) -> bool:
        calls.append(pid)
        return False

    monkeypatch.setattr(singleton_mod, "pid_is_alive", fake_pid_is_alive)
    path = tmp_path / "manifest.json"
    _write(path, {"s": _entry("s", 424242)})

    assert sm.prune_dead_daemon_entries(path=path) == ["s"]
    assert calls == [424242], "liveness was not probed via singleton.pid_is_alive"


def test_unprobeable_pid_is_treated_as_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If liveness can't be determined, keep the entry — a stale entry is
    harmless, deleting a live one is not."""
    import octowright.singleton as singleton_mod

    def boom(_pid: int) -> bool:
        raise OSError("cannot probe")

    monkeypatch.setattr(singleton_mod, "pid_is_alive", boom)
    path = tmp_path / "manifest.json"
    _write(path, {"s": _entry("s", 424242)})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "s" in sm.read_manifest(path)["sessions"]
