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
from unittest.mock import MagicMock

import pytest

from octowright import process_reaper


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
        "2000 1 /Users/tim/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --headless\n"
        "2001 2000 /Users/tim/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome --type=renderer\n"
        "3000 1 /Users/tim/.cache/puppeteer/chrome-headless-shell --type=gpu-process\n"
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
