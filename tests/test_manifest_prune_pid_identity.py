# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A recycled pid must not strand a manifest entry forever.

``prune_dead_daemon_entries`` decided orphanhood on pid LIVENESS alone: an
entry survived whenever some process still held its recorded ``daemon_pid``.
That is conservative in the safe direction — it can never delete a live
daemon's entry — but it is only half the test, because pids get recycled.

Observed live: an entry from a daemon that died on 2026-07-21 was still in the
manifest four weeks later, because the OS had since handed its pid to a
``-zsh``. Liveness said "alive", the prune kept it, and nothing would ever
remove it. On a busy machine those accumulate.

The repo already had the missing half. ``restart._locked_pid_is_octowright``
verifies a recorded pid's COMMAND LINE before acting on it, for exactly this
reason, and 0.15.0 extended the same reasoning to the browser sweep. Applying
it here makes the prune both safer and more effective: an entry goes when its
pid is dead OR when the pid is alive but demonstrably not an octowright daemon.

The conservative direction is preserved everywhere it matters — the running
daemon's own entries are skipped by pid, a live octowright daemon is
recognised, and an unreadable process table keeps the entry rather than
guessing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octowright import process_reaper
from octowright import session_manifest as sm


def _entry(session_id: str, daemon_pid: int) -> dict[str, object]:
    return {
        "session_id": session_id,
        "kind": "chromium",
        "label": None,
        "profile": None,
        "user_data_dir": None,
        "log_path": f"/tmp/{session_id}.jsonl",
        "launched_at": "2026-07-21T03:31:19Z",
        "updated_at": "2026-07-21T03:31:19Z",
        "state": "open",
        "daemon_pid": daemon_pid,
    }


def _write(path: Path, entries: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": 1, "sessions": entries}), encoding="utf-8")


def _stub_table(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[int, int, str]]) -> None:
    monkeypatch.setattr(process_reaper, "_list_processes", lambda: rows)


def _alive(monkeypatch: pytest.MonkeyPatch, pids: set[int]) -> None:
    monkeypatch.setattr(sm, "_pid_alive", lambda pid: pid in pids)


OTHER = 4242
DAEMON_CMD = "/opt/venv/bin/python /opt/venv/bin/octowright serve"


def test_recycled_pid_no_longer_strands_the_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live case: the daemon died, the OS gave its pid to a shell."""
    path = tmp_path / "manifest.json"
    _write(path, {"jul21": _entry("jul21", OTHER)})
    _alive(monkeypatch, {OTHER})
    _stub_table(monkeypatch, [(OTHER, 1, "-zsh")])

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == ["jul21"]
    assert sm.read_manifest(path)["sessions"] == {}


def test_a_live_octowright_daemon_keeps_its_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The case the liveness check exists for: another daemon sharing this
    manifest (e.g. --no-singleton) must not be pruned by one booting."""
    path = tmp_path / "manifest.json"
    _write(path, {"other": _entry("other", OTHER)})
    _alive(monkeypatch, {OTHER})
    _stub_table(monkeypatch, [(OTHER, 1, DAEMON_CMD)])

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == []
    assert "other" in sm.read_manifest(path)["sessions"]


def test_a_dead_pid_is_still_pruned_without_consulting_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-existing behaviour, unchanged."""
    path = tmp_path / "manifest.json"
    _write(path, {"dead": _entry("dead", OTHER)})
    _alive(monkeypatch, set())
    _stub_table(monkeypatch, [])

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == ["dead"]


def test_an_unreadable_process_table_keeps_the_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without proof, keep — the same harmless direction the liveness probe takes."""
    path = tmp_path / "manifest.json"
    _write(path, {"unknown": _entry("unknown", OTHER)})
    _alive(monkeypatch, {OTHER})

    def _boom() -> list[tuple[int, int, str]]:
        raise OSError("ps unavailable")

    monkeypatch.setattr(process_reaper, "_list_processes", _boom)

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == []
    assert "unknown" in sm.read_manifest(path)["sessions"]


def test_a_pid_absent_from_the_table_keeps_the_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness says alive but ps does not list it (a scan race, or a process
    this user cannot see). Two disagreeing signals is not proof; keep."""
    path = tmp_path / "manifest.json"
    _write(path, {"racy": _entry("racy", OTHER)})
    _alive(monkeypatch, {OTHER})
    _stub_table(monkeypatch, [(9999, 1, DAEMON_CMD)])

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == []


def test_the_running_daemons_own_entries_are_never_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipped by pid before any identity check, so the daemon doing the prune
    cannot delete its own live sessions even though pytest is not octowright."""
    path = tmp_path / "manifest.json"
    _write(path, {"mine": _entry("mine", os.getpid())})
    _alive(monkeypatch, {os.getpid()})
    _stub_table(monkeypatch, [(os.getpid(), 1, "pytest")])

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "mine" in sm.read_manifest(path)["sessions"]


@pytest.mark.parametrize(
    "command",
    [
        "/venv/bin/python /venv/bin/octowright cleanup",
        "/venv/bin/python /venv/bin/octowright restart",
        "uv run octowright scenario list",
    ],
)
def test_a_short_lived_octowright_cli_is_not_a_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """The match is on `octowright serve`, not on `octowright`.

    A recycled pid landing on any other octowright subcommand -- `cleanup`,
    `restart`, a scenario CLI -- is still a recycled pid, and a looser substring
    would read those as the owning daemon and strand the entry exactly as
    liveness-only did.
    """
    path = tmp_path / "manifest.json"
    _write(path, {"recycled": _entry("recycled", OTHER)})
    _alive(monkeypatch, {OTHER})
    _stub_table(monkeypatch, [(OTHER, 1, command)])

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == ["recycled"]


def test_the_process_table_is_read_once_for_the_whole_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One `ps` per prune, not one per entry — the same lesson the browser
    sweep learned when it rebuilt its descendant index per leader pid."""
    path = tmp_path / "manifest.json"
    _write(path, {f"s{i}": _entry(f"s{i}", OTHER + i) for i in range(6)})
    _alive(monkeypatch, {OTHER + i for i in range(6)})

    calls = {"n": 0}

    def _counting() -> list[tuple[int, int, str]]:
        calls["n"] += 1
        return [(OTHER, 1, DAEMON_CMD)]

    monkeypatch.setattr(process_reaper, "_list_processes", _counting)

    sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path)

    assert calls["n"] == 1


def test_no_table_read_when_there_is_nothing_to_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty manifest must not shell out to `ps` at leader boot."""
    path = tmp_path / "manifest.json"
    _write(path, {})

    def _boom() -> list[tuple[int, int, str]]:
        raise AssertionError("must not read the process table")

    monkeypatch.setattr(process_reaper, "_list_processes", _boom)

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid(), path=path) == []
