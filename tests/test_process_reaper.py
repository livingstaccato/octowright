# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``octowright.process_reaper``.

The reaper runs ``ps`` and sends signals, both side-effecting. These tests
monkeypatch the subprocess and signal entry points so we exercise the
filter/descendant/sigterm-then-sigkill logic without touching real processes.
"""

from __future__ import annotations

import signal
import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import process_reaper


@pytest.mark.anyio
async def test_shutdown_reaper_force_closes_pool_before_process_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MagicMock()
    pool.close_all = AsyncMock()
    log = MagicMock()
    monkeypatch.setattr(
        process_reaper,
        "reap_orphan_browsers",
        lambda **_kw: {"killed": [], "still_alive": [], "errors": []},
    )

    await process_reaper.reap_descendant_browsers_on_shutdown(pool, log=log)

    pool.close_all.assert_awaited_once_with(force=True)


def _ps_completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["ps"], returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def fake_ps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace ``subprocess.run`` so successive calls return scripted output.

    Pins the platform shim to POSIX so the fixture's ``ps`` output is parsed
    by ``_list_processes_posix`` even when these tests run on Windows
    runners. The Windows code path has its own dedicated tests below.
    """
    scripts: list[str] = []

    def _run(args: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return _ps_completed(scripts.pop(0) if scripts else "")

    monkeypatch.setattr(process_reaper, "_is_windows", lambda: False)
    monkeypatch.setattr(process_reaper.subprocess, "run", _run)
    return scripts


def test_find_browser_pids_all_filters_to_ms_playwright(fake_ps: list[str]) -> None:
    fake_ps.append(
        "1000 1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
        "2000 1 /tmp/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --headless\n"
        "2001 2000 /tmp/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --type=renderer\n"
        "3000 1 /tmp/puppeteer-cache/chrome-headless-shell --type=gpu-process\n"
    )
    pids = process_reaper.find_browser_pids("all")
    assert pids == [2000, 2001]


def test_find_browser_pids_descendants_excludes_other_trees(fake_ps: list[str]) -> None:
    # Two daemon processes (4000, 5000); each owns one chromium tree.
    fake_ps.append(
        "4000 1 python octowright serve\n"
        "4001 4000 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
        "4002 4001 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --type=renderer\n"
        "5000 1 python octowright serve\n"
        "5001 5000 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
    )
    pids = process_reaper.find_browser_pids("descendants", root_pid=4000)
    assert set(pids) == {4001, 4002}


def test_find_browser_pids_descendants_requires_root_pid() -> None:
    with pytest.raises(ValueError, match="root_pid"):
        process_reaper.find_browser_pids("descendants")


def test_is_orphaned_browser_logic() -> None:
    live = frozenset({1, 100, 200})
    # init / kernel reparent targets and a vanished parent are all orphans.
    assert process_reaper._is_orphaned_browser(1, live) is True
    assert process_reaper._is_orphaned_browser(0, live) is True
    assert process_reaper._is_orphaned_browser(999, live) is True
    # A browser whose driver (100) is still alive is NOT an orphan.
    assert process_reaper._is_orphaned_browser(100, live) is False


def test_find_browser_pids_orphaned_flags_only_dead_driver_browsers(fake_ps: list[str]) -> None:
    # 4000 live daemon -> 4001 live driver -> 4002 healthy browser (NOT orphan).
    # 2000 reparented to init (ppid 1) and 2001 whose parent 9999 is gone ARE orphans.
    fake_ps.append(
        "4000 1 python octowright serve\n"
        "4001 4000 node playwright driver\n"
        "4002 4001 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
        "2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
        "2001 9999 ms-playwright/firefox-1234/firefox\n"
    )
    pids = process_reaper.find_browser_pids("orphaned")
    assert set(pids) == {2000, 2001}


def test_find_browser_pids_orphaned_empty_when_driver_alive(fake_ps: list[str]) -> None:
    fake_ps.append(
        "4000 1 python octowright serve\n"
        "4001 4000 node playwright driver\n"
        "4002 4001 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
    )
    assert process_reaper.find_browser_pids("orphaned") == []


def test_reap_orphan_browsers_orphaned_scope_reaps_reparented(
    fake_ps: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A live daemon's browser (4002) sits beside an orphan (2000, ppid 1).
    healthy_tree = (
        "4000 1 python octowright serve\n"
        "4001 4000 node playwright driver\n"
        "4002 4001 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
    )
    orphan = "2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
    fake_ps.append(healthy_tree + orphan)  # initial scan
    fake_ps.append(healthy_tree)  # after SIGTERM grace: orphan gone
    fake_ps.append(healthy_tree)  # final scan

    sent: list[int] = []
    monkeypatch.setattr(process_reaper.os, "kill", lambda pid, _signum: sent.append(pid))
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)

    out = process_reaper.reap_orphan_browsers("orphaned", grace_seconds=0.0)
    # Only the orphan was signalled; the live daemon's browser was left alone.
    assert set(sent) == {2000}
    assert out["killed"] == [2000]


def test_reap_orphan_browsers_dry_run_does_not_kill(
    fake_ps: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")
    killed = MagicMock()
    monkeypatch.setattr(process_reaper.os, "kill", killed)
    out = process_reaper.reap_orphan_browsers("all", dry_run=True)
    killed.assert_not_called()
    assert out["killed"] == []
    assert out["still_alive"] == [2000]


def test_reap_orphan_browsers_sigterm_then_sigkill(
    fake_ps: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Round 1 (initial scan): two browsers found.
    fake_ps.append(
        "2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n"
        "2001 2000 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --type=renderer\n"
    )
    # Round 2 (after SIGTERM grace): pid 2000 still alive — needs SIGKILL.
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")
    # Round 3 (after SIGKILL): everything gone.
    fake_ps.append("")

    sent: list[tuple[int, int]] = []

    def _kill(pid: int, signum: int) -> None:
        sent.append((pid, signum))

    monkeypatch.setattr(process_reaper.os, "kill", _kill)
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)

    out = process_reaper.reap_orphan_browsers("all", grace_seconds=0.0)

    # Survivor (2000) gets two signals (SIGTERM then KILL_SIGNAL); the pid
    # that died on SIGTERM (2001) gets one. On Windows KILL_SIGNAL == SIGTERM
    # so we count signals per pid rather than asserting exact tuples.
    signals_per_pid = {pid: [s for p, s in sent if p == pid] for pid in (2000, 2001)}
    assert signals_per_pid[2000] == [signal.SIGTERM, process_reaper.KILL_SIGNAL]
    assert signals_per_pid[2001] == [signal.SIGTERM]
    assert set(out["killed"]) == {2000, 2001}
    assert out["still_alive"] == []
    assert out["errors"] == []


def test_reap_orphan_browsers_records_kill_errors(
    fake_ps: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")

    def _kill(_pid: int, _signum: int) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr(process_reaper.os, "kill", _kill)
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)

    out = process_reaper.reap_orphan_browsers("all", grace_seconds=0.0)
    stages = {err["stage"] for err in out["errors"]}
    assert stages == {"sigterm", "sigkill"}
    assert out["still_alive"] == [2000]


def test_reap_orphan_browsers_handles_already_dead(
    fake_ps: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ps.append("2000 1 ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome\n")
    fake_ps.append("")
    fake_ps.append("")

    def _kill(_pid: int, _signum: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(process_reaper.os, "kill", _kill)
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)

    out = process_reaper.reap_orphan_browsers("all", grace_seconds=0.0)
    # ProcessLookupError is treated as success — pid already gone.
    assert out["killed"] == [2000]
    assert out["errors"] == []


def test_is_browser_command_matches_windows_backslash_paths() -> None:
    cmd = r"C:\Users\tim\AppData\Local\ms-playwright\chromium-1217\chrome-win\chrome.exe --headless"
    assert process_reaper._is_browser_command(cmd) is True


def test_is_browser_command_matches_case_insensitively() -> None:
    assert process_reaper._is_browser_command("MS-Playwright/Chromium-1217/foo") is True


def test_list_processes_windows_parses_csv_output(monkeypatch: pytest.MonkeyPatch) -> None:
    csv_stdout = (
        '"ProcessId","ParentProcessId","CommandLine"\r\n'
        '"100","1","C:\\Windows\\System32\\services.exe"\r\n'
        '"2000","100","\\"C:\\Users\\tim\\ms-playwright\\chromium-1217\\chrome.exe\\" --headless"\r\n'
        '"2001","2000","chrome.exe --type=renderer"\r\n'
        '"3000","1",""\r\n'
    )
    monkeypatch.setattr(process_reaper, "_is_windows", lambda: True)

    def _fake_run(args: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        assert args[0] == "powershell"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=csv_stdout, stderr="")

    monkeypatch.setattr(process_reaper.subprocess, "run", _fake_run)

    rows = process_reaper._list_processes()
    pids = {row[0] for row in rows}
    assert pids == {100, 2000, 2001, 3000}
    # Browser-path detection should still find the Playwright chrome process
    pids_browser = process_reaper.find_browser_pids("all")
    assert pids_browser == [2000]


def test_kill_pid_windows_treats_not_found_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_reaper, "_is_windows", lambda: True)
    monkeypatch.setattr(
        process_reaper.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr=""),
    )
    ok, err = process_reaper._kill_pid(9999, signum=signal.SIGTERM)
    assert ok is True
    assert err is None


def test_kill_pid_windows_returns_error_on_nonzero_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_reaper, "_is_windows", lambda: True)
    monkeypatch.setattr(
        process_reaper.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Access denied"),
    )
    ok, err = process_reaper._kill_pid(9999, signum=signal.SIGTERM)
    assert ok is False
    assert err is not None and "Access denied" in err


def _powershell_csv(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    """Pin platform to Windows + scripted PowerShell stdout."""
    monkeypatch.setattr(process_reaper, "_is_windows", lambda: True)
    monkeypatch.setattr(
        process_reaper.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=""),
    )


def test_list_processes_windows_empty_stdout_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """If PowerShell prints nothing (no header, no rows) the parser must
    fail closed with an empty list instead of raising StopIteration."""
    _powershell_csv(monkeypatch, "")
    assert process_reaper._list_processes() == []


def test_list_processes_windows_missing_header_columns_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header without ProcessId/ParentProcessId/CommandLine → bail out."""
    _powershell_csv(monkeypatch, '"Name","Status"\r\n"chrome","Running"\r\n')
    assert process_reaper._list_processes() == []


def test_list_processes_windows_skips_short_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSV rows shorter than the max column index must be skipped, not crash."""
    csv_stdout = (
        '"ProcessId","ParentProcessId","CommandLine"\r\n'
        '"100","1"\r\n'  # short row — missing CommandLine cell
        '"2000","1","ms-playwright/chromium-1217/chrome --headless"\r\n'
    )
    _powershell_csv(monkeypatch, csv_stdout)
    pids = {pid for pid, _ppid, _cmd in process_reaper._list_processes()}
    assert pids == {2000}


def test_list_processes_windows_skips_non_numeric_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row with a non-integer PID is skipped (ValueError → continue)."""
    csv_stdout = (
        '"ProcessId","ParentProcessId","CommandLine"\r\n'
        '"abc","1","junk"\r\n'
        '"2000","1","ms-playwright/chromium-1217/chrome"\r\n'
    )
    _powershell_csv(monkeypatch, csv_stdout)
    pids = {pid for pid, _ppid, _cmd in process_reaper._list_processes()}
    assert pids == {2000}


def test_reap_meters_killed_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._metric_recorders import RecordingCounter

    rec = RecordingCounter()
    monkeypatch.setattr(process_reaper, "_ORPHAN_REAPED", rec)
    # find_browser_pids: [123] while present (initial + survivor checks), then []
    # once "killed" — so killed == [123].
    calls = {"n": 0}

    def fake_find(_scope: str, *, root_pid: int | None = None) -> list[int]:
        calls["n"] += 1
        return [123] if calls["n"] <= 2 else []

    monkeypatch.setattr(process_reaper, "find_browser_pids", fake_find)
    monkeypatch.setattr(process_reaper, "_signal_pids", lambda _pids, _signum, _stage: [])
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)

    summary = process_reaper.reap_orphan_browsers(scope="orphaned")
    assert summary["killed"] == [123]
    assert rec.total() == 1
    assert rec.attrs_for("scope") == ["orphaned"]


def test_reap_does_not_meter_when_nothing_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._metric_recorders import RecordingCounter

    rec = RecordingCounter()
    monkeypatch.setattr(process_reaper, "_ORPHAN_REAPED", rec)
    monkeypatch.setattr(process_reaper, "find_browser_pids", lambda _scope, *, root_pid=None: [])
    summary = process_reaper.reap_orphan_browsers(scope="orphaned")
    assert summary["killed"] == []
    assert rec.total() == 0
