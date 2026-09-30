# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Telling a browser-PROCESS crash from a window the user closed.

Playwright reports both the same way: every page fires ``close``, the context
fires ``close``, a pending call rejects with ``TargetClosedError``. It never
forwards the exit code or signal to the client (only ``launchServer``'s
``browserServer`` sees them), so octowright reads the answer from the OS.

What these tests pin, each measured live (N=8 per engine and path, headed,
persistent context, real ``WM_DELETE_WINDOW`` for the user close):

* On a user close the browser is still ALIVE when its last page's ``close``
  reaches Python (it closes its pages, then exits). After SIGTRAP/SIGSEGV/SIGKILL
  it is already DEAD (zombie or reaped) -- the page closes are Playwright
  reacting to the dropped pipe.
* A headed Chromium removes its ``SingletonLock`` on an orderly exit and leaves
  it after a signal death, so once the process is gone the lock settles the
  question independent of timing. The headless shell never writes one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import incidents, process_crash
from octowright.browser_pool.events import SessionCrashedEvent
from octowright.browser_pool.process_crash import BrowserProcess

# ─── a fake /proc ────────────────────────────────────────────────────────────


def _proc(root: Path, pid: int, ppid: int, argv: list[str], state: str = "S") -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    # comm may contain spaces and parens; the parser must split on the LAST ')'.
    (d / "stat").write_text(f"{pid} (chrome (x) y) {state} {ppid} 1 1 0 -1 4194560\n")


def test_chromium_browser_is_the_root_most_process_naming_the_profile(tmp_path: Path) -> None:
    udd = tmp_path / "profile"
    proc = tmp_path / "proc"
    _proc(proc, 100, 1, ["node", "cli.js", "run-driver"])
    _proc(proc, 200, 100, ["chrome", "--no-sandbox", f"--user-data-dir={udd}", "about:blank"])
    _proc(proc, 201, 200, ["chrome", "--type=zygote", f"--user-data-dir={udd}"])
    _proc(proc, 202, 201, ["chrome", "--type=renderer"])

    found = process_crash.find_browser_process("chromium", udd, proc_root=proc)

    assert found is not None
    assert found.pid == 200


def test_chromium_rewritten_cmdline_is_matched(tmp_path: Path) -> None:
    """Chromium rewrites its /proc cmdline into ONE space-joined string (measured
    on chrome-headless-shell), so NUL-splitting alone finds no flag at all."""
    udd = tmp_path / "profile"
    proc = tmp_path / "proc"
    d = proc / "200"
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(
        f"chrome --no-sandbox --user-data-dir={udd} --remote-debugging-pipe about:blank".encode()
    )
    (d / "stat").write_text("200 (chrome) S 1 1 1\n")
    other = proc / "201"
    other.mkdir()
    (other / "cmdline").write_bytes(f"chrome --user-data-dir={udd}-other".encode())
    (other / "stat").write_text("201 (chrome) S 1 1 1\n")

    found = process_crash.find_browser_process("chromium", udd, proc_root=proc)

    assert found is not None
    assert found.pid == 200


def test_webkit_resolves_to_its_wrapper_script(tmp_path: Path) -> None:
    """``pw_run.sh`` holds the pipe as well, and exits when MiniBrowser does."""
    udd = tmp_path / "wk"
    proc = tmp_path / "proc"
    _proc(proc, 300, 1, ["bash", "pw_run.sh", "--inspector-pipe", f"--user-data-dir={udd}"])
    _proc(proc, 301, 300, ["MiniBrowser", "--inspector-pipe", f"--user-data-dir={udd}"])

    found = process_crash.find_browser_process("webkit", udd, proc_root=proc)

    assert found is not None
    assert found.pid == 300


def test_firefox_matches_the_profile_argument(tmp_path: Path) -> None:
    udd = tmp_path / "ff"
    proc = tmp_path / "proc"
    _proc(proc, 400, 1, ["firefox", "-no-remote", "-profile", str(udd), "-juggler-pipe"])
    _proc(proc, 401, 400, ["firefox", "-contentproc", "-isForBrowser"])

    found = process_crash.find_browser_process("firefox", udd, proc_root=proc)

    assert found is not None
    assert found.pid == 400


def test_a_path_that_only_shares_a_prefix_does_not_match(tmp_path: Path) -> None:
    udd = tmp_path / "profile"
    proc = tmp_path / "proc"
    _proc(proc, 500, 1, ["chrome", f"--user-data-dir={udd}-other"])

    assert process_crash.find_browser_process("chromium", udd, proc_root=proc) is None


def test_two_unrelated_roots_are_ambiguous_and_resolve_to_nothing(tmp_path: Path) -> None:
    """Refuse to guess: the wrong pid would misreport someone else's exit."""
    udd = tmp_path / "profile"
    proc = tmp_path / "proc"
    _proc(proc, 600, 1, ["chrome", f"--user-data-dir={udd}"])
    _proc(proc, 601, 1, ["chrome", f"--user-data-dir={udd}"])

    assert process_crash.find_browser_process("chromium", udd, proc_root=proc) is None


def test_stat_is_read_only_for_a_process_naming_the_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan visits every process on the host; only a match costs a second read."""
    udd = tmp_path / "profile"
    proc = tmp_path / "proc"
    _proc(proc, 200, 1, ["chrome", f"--user-data-dir={udd}"])
    for pid in range(300, 310):
        _proc(proc, pid, 1, ["bash", "-c", "sleep 1"])
    stat_reads: list[str] = []
    real = process_crash._stat_fields

    def _counting(pid_dir: Path) -> list[str] | None:
        stat_reads.append(pid_dir.name)
        return real(pid_dir)

    monkeypatch.setattr(process_crash, "_stat_fields", _counting)

    found = process_crash.find_browser_process("chromium", udd, proc_root=proc)

    assert found is not None and found.pid == 200
    assert stat_reads == ["200"]


async def test_resolving_at_launch_scans_off_the_event_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []
    sentinel = BrowserProcess(pid=1, user_data_dir=tmp_path, singleton_lock=False)

    def _find(kind: str, user_data_dir: Path | str, **_: Any) -> BrowserProcess:
        seen.append(threading.get_ident())
        return sentinel

    monkeypatch.setattr(process_crash, "find_browser_process", _find)

    assert await process_crash.resolve_browser_process("chromium", tmp_path) is sentinel
    assert seen and seen[0] != loop_thread


def test_no_proc_filesystem_resolves_to_nothing(tmp_path: Path) -> None:
    assert process_crash.find_browser_process("chromium", tmp_path, proc_root=tmp_path / "absent") is None


def test_chromium_records_whether_it_wrote_a_singleton_lock(tmp_path: Path) -> None:
    udd = tmp_path / "profile"
    udd.mkdir()
    proc = tmp_path / "proc"
    _proc(proc, 700, 1, ["chrome", f"--user-data-dir={udd}"])

    assert process_crash.find_browser_process("chromium", udd, proc_root=proc).singleton_lock is False  # type: ignore[union-attr]
    if os.name != "nt":
        (udd / "SingletonLock").symlink_to("host-700")
        assert process_crash.find_browser_process("chromium", udd, proc_root=proc).singleton_lock is True  # type: ignore[union-attr]


def test_only_chromium_is_judged_by_its_lock(tmp_path: Path) -> None:
    """Firefox's ``lock`` measured INVERTED (kept on a clean close, removed by its
    own SIGSEGV handler), so it must never be read as evidence."""
    udd = tmp_path / "ff"
    udd.mkdir()
    (udd / "SingletonLock").write_text("")
    proc = tmp_path / "proc"
    _proc(proc, 800, 1, ["firefox", "-profile", str(udd)])

    assert process_crash.find_browser_process("firefox", udd, proc_root=proc).singleton_lock is False  # type: ignore[union-attr]


# ─── process state ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("state", "expected"), [("S", "alive"), ("R", "alive"), ("D", "alive"), ("Z", "dead")])
def test_process_state_reads_the_stat_state(tmp_path: Path, state: str, expected: str) -> None:
    _proc(tmp_path, 900, 1, ["chrome"], state=state)
    assert process_crash.process_state(900, proc_root=tmp_path) == expected


def test_a_reaped_process_is_dead(tmp_path: Path) -> None:
    tmp_path.joinpath("unrelated").mkdir()
    assert process_crash.process_state(901, proc_root=tmp_path) == "dead"


def test_no_proc_filesystem_is_unknown(tmp_path: Path) -> None:
    assert process_crash.process_state(1, proc_root=tmp_path / "absent") == "unknown"


def test_the_real_proc_sees_this_process_alive() -> None:
    if not Path("/proc/self/stat").exists():
        pytest.skip("no /proc on this platform")
    assert process_crash.process_state(os.getpid()) == "alive"


# ─── the verdict ─────────────────────────────────────────────────────────────


def _bp(tmp_path: Path, *, lock: bool, pid: int = 1000) -> BrowserProcess:
    return BrowserProcess(pid=pid, user_data_dir=tmp_path / "udd", singleton_lock=lock)


def test_alive_at_the_close_signal_is_a_close(tmp_path: Path) -> None:
    _proc(tmp_path / "proc", 1000, 1, ["chrome"], state="R")
    assert process_crash.exit_verdict(_bp(tmp_path, lock=False), proc_root=tmp_path / "proc") == "closed"


def test_dead_without_a_lock_to_consult_is_a_crash(tmp_path: Path) -> None:
    (tmp_path / "proc").mkdir()
    assert process_crash.exit_verdict(_bp(tmp_path, lock=False), proc_root=tmp_path / "proc") == "crashed"


def test_dead_with_the_lock_still_there_is_a_crash(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("no singleton lock on Windows")
    (tmp_path / "proc").mkdir()
    (tmp_path / "udd").mkdir()
    (tmp_path / "udd" / "SingletonLock").symlink_to("host-1000")
    assert process_crash.exit_verdict(_bp(tmp_path, lock=True), proc_root=tmp_path / "proc") == "crashed"


def test_dead_with_the_lock_removed_is_a_clean_exit(tmp_path: Path) -> None:
    """The case the lock exists for: an orderly exit the event loop saw late.

    A stalled loop can deliver the last page's ``close`` after an orderly
    Chromium has already exited; liveness alone would call that a crash. The
    lock Chromium removed on the way out says otherwise.
    """
    (tmp_path / "proc").mkdir()
    (tmp_path / "udd").mkdir()
    assert process_crash.exit_verdict(_bp(tmp_path, lock=True), proc_root=tmp_path / "proc") == "closed"


def test_an_unknown_process_gives_no_verdict(tmp_path: Path) -> None:
    assert process_crash.exit_verdict(None, proc_root=tmp_path) is None
    assert process_crash.exit_verdict(_bp(tmp_path, lock=False), proc_root=tmp_path / "absent") is None


# ─── classification and its side effects ─────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))


class _Session:
    def __init__(self, tmp_path: Path, browser_process: BrowserProcess | None) -> None:
        self.instance_id = "abc123"
        self.kind = "chromium"
        self.label = "lbl"
        self.profile = "persona"
        self.url = "https://example.test/page"
        self.log_path = tmp_path / "s.jsonl"
        self.recorder = _Recorder()
        self._crashed = False
        self._browser_process = browser_process
        self._exit_verdict: str | None = None
        self._exit_verdict_event = asyncio.Event()
        self._process_crash_incident: dict[str, Any] | None = None


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    from octowright.browser_pool.session_event_bus import session_event_bus

    events: list[object] = []
    monkeypatch.setattr(session_event_bus, "publish_nowait", events.append)
    incidents.reset()
    return events


def _dead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "proc").mkdir(exist_ok=True)
    monkeypatch.setattr(process_crash, "PROC_ROOT", tmp_path / "proc")


def test_a_process_crash_is_classified_crashed_and_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    _dead(tmp_path, monkeypatch)
    session = _Session(tmp_path, _bp(tmp_path, lock=False))

    reason = process_crash.classify_external_close(session)

    assert reason == "crashed"
    assert session._crashed is True
    assert session._exit_verdict == "crashed"
    assert session._exit_verdict_event.is_set()
    crash_events = [e for e in published if isinstance(e, SessionCrashedEvent)]
    assert len(crash_events) == 1
    assert crash_events[0].scope == "process"
    assert crash_events[0].recovering is False
    recs = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert len(recs) == 1
    assert recs[0]["instance_id"] == "abc123"
    assert recs[0]["url"] == "https://example.test/page"
    assert recs[0]["evidence"] == "liveness"
    assert ("browser_crash", {"scope": "process", "evidence": "liveness"}) in session.recorder.rows


def test_classification_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]) -> None:
    """Several close signals fire for one death; exactly one crash is reported."""
    _dead(tmp_path, monkeypatch)
    session = _Session(tmp_path, _bp(tmp_path, lock=False))

    for _ in range(3):
        assert process_crash.classify_external_close(session) == "crashed"

    assert len([e for e in published if isinstance(e, SessionCrashedEvent)]) == 1
    assert len(incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)) == 1


def test_a_live_browser_is_an_ordinary_user_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    _proc(tmp_path / "proc", 1000, 1, ["chrome"], state="S")
    monkeypatch.setattr(process_crash, "PROC_ROOT", tmp_path / "proc")
    session = _Session(tmp_path, _bp(tmp_path, lock=False))

    assert process_crash.classify_external_close(session) == "user_close"
    assert session._crashed is False
    assert session._exit_verdict == "closed"
    assert published == []


def test_no_known_process_keeps_todays_behaviour(tmp_path: Path, published: list[object]) -> None:
    """Ephemeral contexts and non-Linux hosts have no pid: stay a user close."""
    session = _Session(tmp_path, None)

    assert process_crash.classify_external_close(session) == "user_close"
    assert session._exit_verdict == "closed"
    assert published == []


def test_a_renderer_crash_already_on_record_stays_a_crash(tmp_path: Path, published: list[object]) -> None:
    session = _Session(tmp_path, None)
    session._crashed = True

    assert process_crash.classify_external_close(session) == "crashed"
    assert session._exit_verdict == "crashed"
    assert published == []  # the renderer path already published its own event


def _locked_dead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BrowserProcess:
    """A dead headed Chromium whose SingletonLock is still there: lock evidence."""
    if os.name == "nt":
        pytest.skip("no singleton lock on Windows")
    _dead(tmp_path, monkeypatch)
    (tmp_path / "udd").mkdir(exist_ok=True)
    (tmp_path / "udd" / "SingletonLock").symlink_to("host-1000")
    return _bp(tmp_path, lock=True)


def test_relaunching_mode_marks_a_lock_evidenced_crash_as_recovering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    from octowright.browser_pool import driver_relaunch

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "keep-id")
    session = _Session(tmp_path, _locked_dead(tmp_path, monkeypatch))

    process_crash.classify_external_close(session)

    (event,) = [e for e in published if isinstance(e, SessionCrashedEvent)]
    assert event.recovering is True
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["evidence"] == "singleton_lock"
    assert inc["outcome"] == "relaunching"
    assert ("browser_crash", {"scope": "process", "evidence": "singleton_lock"}) in session.recorder.rows


def test_a_liveness_only_crash_is_never_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    """Liveness cannot tell a crash from an orderly close the loop saw late, so
    it may label the session crashed but must never bring the window back."""
    from octowright.browser_pool import driver_relaunch

    _dead(tmp_path, monkeypatch)
    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "keep-id")
    session = _Session(tmp_path, _bp(tmp_path, lock=False))

    assert process_crash.classify_external_close(session) == "crashed"

    (event,) = [e for e in published if isinstance(e, SessionCrashedEvent)]
    assert event.recovering is False
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["evidence"] == "liveness"
    assert inc["outcome"] == "lost"


def test_a_crash_whose_close_is_already_owned_is_never_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    from octowright.browser_pool import driver_relaunch

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "keep-id")
    session = _Session(tmp_path, _locked_dead(tmp_path, monkeypatch))

    assert process_crash.classify_external_close(session, relaunch_allowed=False) == "crashed"

    (event,) = [e for e in published if isinstance(e, SessionCrashedEvent)]
    assert event.recovering is False
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["outcome"] == "lost"


def test_a_classifier_failure_still_publishes_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    """A waiting download save must not sit out its timeout on a classifier bug."""

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("proc read exploded")

    monkeypatch.setattr(process_crash, "exit_verdict", _boom)
    session = _Session(tmp_path, _bp(tmp_path, lock=False))

    with pytest.raises(RuntimeError, match="proc read exploded"):
        process_crash.classify_external_close(session)

    assert session._exit_verdict == "closed"
    assert session._exit_verdict_event.is_set()


def test_a_failing_recorder_or_bus_is_logged_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: list[object]
) -> None:
    from octowright.browser_pool.session_event_bus import session_event_bus

    _dead(tmp_path, monkeypatch)
    logged: list[str] = []

    class _Log:
        def debug(self, event: str, **_: Any) -> None:
            logged.append(event)

        def warning(self, event: str, **_: Any) -> None:
            logged.append(event)

    class _BrokenRecorder:
        def record(self, *_: Any, **__: Any) -> None:
            raise OSError("disk full")

    def _broken_publish(_: object) -> None:
        raise RuntimeError("bus closed")

    monkeypatch.setattr(process_crash, "log", _Log())
    monkeypatch.setattr(session_event_bus, "publish_nowait", _broken_publish)
    session = _Session(tmp_path, _bp(tmp_path, lock=False))
    session.recorder = _BrokenRecorder()  # type: ignore[assignment]

    assert process_crash.classify_external_close(session) == "crashed"

    assert "octowright.browser.process_crash_record_failed" in logged
    assert "octowright.browser.process_crash_publish_failed" in logged


# ─── waiting for the verdict (the download path) ─────────────────────────────


async def test_wait_returns_the_verdict_once_set(tmp_path: Path) -> None:
    session = _Session(tmp_path, None)

    async def _later() -> None:
        await asyncio.sleep(0.01)
        session._exit_verdict = "crashed"
        session._exit_verdict_event.set()

    task = asyncio.create_task(_later())
    assert await process_crash.wait_for_exit_verdict(session, timeout=2.0) == "crashed"
    await task


async def test_wait_gives_up_without_a_verdict(tmp_path: Path) -> None:
    """Only the page closed and the browser lives on: no eviction, no verdict."""
    session = _Session(tmp_path, None)
    assert await process_crash.wait_for_exit_verdict(session, timeout=0.01) is None
