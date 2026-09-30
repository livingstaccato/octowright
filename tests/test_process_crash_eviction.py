# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A dead browser process goes through the crash path, not an ordinary close.

Driven through the real listeners and close coordinator with the in-memory
Playwright stub from ``test_pool_disconnect``; only the OS view of the browser
process (``process_crash.PROC_ROOT``) is faked. The live proof on all three
engines is ``test_process_crash_live.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool, driver_relaunch, incidents, process_crash
from octowright.browser_pool.events import SessionClosedEvent, SessionCrashedEvent
from octowright.browser_pool.process_crash import BrowserProcess
from tests.test_pool_disconnect import (
    _capture_session_events,
    _close_handlers,
    _install_playwright_stub,
    _page_close_handlers,
    _wait_until,
)

# The stubbed driver still goes through _ensure_pw, which conftest's leak guard
# counts as a launch -- the same reason test_pool_disconnect carries the mark.
pytestmark = pytest.mark.live_browser

_PID = 424242


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep the manifest and recordings out of the developer's real state tree."""
    from octowright import session_manifest

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "manifest.json")
    incidents.reset()
    driver_relaunch.reset()
    return tmp_path


def _fake_proc(root: Path, *, alive: bool) -> Path:
    proc = root / "proc"
    proc.mkdir()
    if alive:
        (proc / str(_PID)).mkdir()
        (proc / str(_PID) / "stat").write_text(f"{_PID} (chrome) S 1 1 1\n")
    return proc


async def _launch(
    monkeypatch: pytest.MonkeyPatch, root: Path, *, alive: bool, lock_left: bool = False
) -> tuple[BrowserPool, Any, list[Any]]:
    """``lock_left``: a headed Chromium that died on a signal, lock still there
    -- the ``singleton_lock`` evidence; otherwise the verdict is liveness only."""
    _install_playwright_stub(monkeypatch)
    events = _capture_session_events(monkeypatch)
    monkeypatch.setattr(process_crash, "PROC_ROOT", _fake_proc(root, alive=alive))
    pool = BrowserPool(recordings_dir=root / "rec")
    result = await pool.launch(kind="chromium", url="https://octowright.com", headed=False, label="pc")
    session = pool._sessions[result["instance_id"]]
    udd = root / "udd"
    if lock_left:
        udd.mkdir(exist_ok=True)
        (udd / "SingletonLock").symlink_to(f"host-{_PID}")
    session._browser_process = BrowserProcess(pid=_PID, user_data_dir=udd, singleton_lock=lock_left)
    return pool, session, events


def _close_row(session: Any) -> dict[str, Any]:
    rows = [json.loads(line) for line in Path(session.log_path).read_text().splitlines() if line.strip()]
    return [r for r in rows if r["action"] == "close"][-1]


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_context_close_after_process_death_is_a_crash(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    pool, session, events = await _launch(monkeypatch, isolated, alive=False)
    iid = session.instance_id

    for cb in _close_handlers(session):
        cb()

    assert iid not in pool._sessions
    assert "crashed" in pool._missing_session_message(iid)
    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    (crash,) = [e for e in events if isinstance(e, SessionCrashedEvent)]
    assert crash.scope == "process"
    assert [e.reason for e in events if isinstance(e, SessionClosedEvent)] == ["crashed"]
    assert _close_row(session)["reason"] == "crashed"
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["instance_id"] == iid
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_last_page_close_after_process_death_is_a_crash(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """The download crash evicts through the last-page path, not context close."""
    pool, session, events = await _launch(monkeypatch, isolated, alive=False)

    session.page.mark_closed()
    for cb in _page_close_handlers(session.page):
        cb()

    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    assert [e.reason for e in events if isinstance(e, SessionClosedEvent)] == ["crashed"]
    assert any(isinstance(e, SessionCrashedEvent) and e.scope == "process" for e in events)
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_window_closed_while_the_browser_lives_is_a_user_close(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    pool, session, events = await _launch(monkeypatch, isolated, alive=True)

    for cb in _close_handlers(session):
        cb()

    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    assert [e.reason for e in events if isinstance(e, SessionClosedEvent)] == ["user_close"]
    assert not any(isinstance(e, SessionCrashedEvent) for e in events)
    assert _close_row(session)["reason"] == "external"
    assert incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH) == []
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_an_agent_close_is_never_reclassified(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """Close events Playwright fires for OUR close must not be judged at all --
    by then the process is gone, and that is not a crash."""
    pool, session, events = await _launch(monkeypatch, isolated, alive=False)

    await pool.close(session.instance_id, force=True)
    for cb in _close_handlers(session):
        cb()

    assert not any(isinstance(e, SessionCrashedEvent) for e in events)
    assert [e.reason for e in events if isinstance(e, SessionClosedEvent)] == ["agent_close"]
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_process_crash_is_surfaced_as_a_lost_session(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    pool, session, events = await _launch(monkeypatch, isolated, alive=False)
    iid = session.instance_id

    for cb in _close_handlers(session):
        cb()

    (lost,) = driver_relaunch.recent_lost()
    assert lost["instance_id"] == iid
    assert lost["reason"] == "browser_process_crashed"
    assert lost["url"] == session.url
    assert lost["relaunched_to"] is None
    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_relaunch_mode_reopens_the_crashed_session(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """Same opt-in, same machinery as a dead driver: OCTOWRIGHT_DRIVER_RELAUNCH."""
    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    pool, session, events = await _launch(monkeypatch, isolated, alive=False, lock_left=True)
    old = session.instance_id

    for cb in _close_handlers(session):
        cb()

    await _wait_until(lambda: driver_relaunch.recent_lost()[0]["relaunched_to"] is not None)
    (lost,) = driver_relaunch.recent_lost()
    new = lost["relaunched_to"]
    assert new != old
    assert pool.maybe_get(new) is not None
    assert pool.maybe_get(new)._auto_relaunched is True
    (crash,) = [e for e in events if isinstance(e, SessionCrashedEvent)]
    assert crash.recovering is True
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_relaunch_mode_never_reopens_a_liveness_only_crash(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """Liveness alone can read a late orderly close as a crash: label it, never reopen it."""
    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    pool, session, events = await _launch(monkeypatch, isolated, alive=False, lock_left=False)

    for cb in _close_handlers(session):
        cb()

    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    (crash,) = [e for e in events if isinstance(e, SessionCrashedEvent)]
    assert crash.recovering is False
    (lost,) = driver_relaunch.recent_lost()
    assert lost["relaunched_to"] is None
    assert not driver_relaunch._TASKS
    assert list(pool.iter_sessions()) == []
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_crash_during_an_explicit_close_is_never_reopened(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """browser_close is draining the gate when the process dies: the user asked
    for it gone (and a handoff would be replacing it itself), so no relaunch."""
    import asyncio

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    pool, session, events = await _launch(monkeypatch, isolated, alive=False, lock_left=True)
    iid = session.instance_id
    held = asyncio.Event()
    release = asyncio.Event()

    async def _hold_the_gate() -> None:
        async with session._operation_gate.operation("probe_hold"):
            held.set()
            await release.wait()

    holder = asyncio.create_task(_hold_the_gate())
    await held.wait()
    closer = asyncio.create_task(pool.close(iid, force=True))
    await _wait_until(lambda: session._operation_gate.close_reserved)
    assert iid in pool._sessions, "the close must still be draining for this to test the window"

    for cb in _close_handlers(session):
        cb()
    release.set()
    await asyncio.gather(holder, closer, return_exceptions=True)

    (crash,) = [e for e in events if isinstance(e, SessionCrashedEvent)]
    assert crash.recovering is False
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["outcome"] == "lost"
    assert not driver_relaunch._TASKS
    assert list(pool.iter_sessions()) == []
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_classifier_failure_still_evicts(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """Judging how the browser went is diagnostics; evicting it is correctness."""
    pool, session, events = await _launch(monkeypatch, isolated, alive=False)
    iid = session.instance_id

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(process_crash, "exit_verdict", _boom)

    for cb in _close_handlers(session):
        cb()

    assert iid not in pool._sessions
    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    assert [e.reason for e in events if isinstance(e, SessionClosedEvent)] == ["user_close"]
    assert session._exit_verdict_event.is_set()
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_failed_crash_relaunch_is_reported_not_left_relaunching(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """The client was told recovering=True; a relaunch that fails must correct it."""
    from octowright.browser_pool.events import SessionRecoveredEvent

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    pool, session, events = await _launch(monkeypatch, isolated, alive=False, lock_left=True)
    iid = session.instance_id

    async def _refuse(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("the profile is still locked")

    monkeypatch.setattr(pool, "launch", _refuse)

    for cb in _close_handlers(session):
        cb()

    await _wait_until(lambda: any(isinstance(e, SessionRecoveredEvent) for e in events))
    (recovered,) = [e for e in events if isinstance(e, SessionRecoveredEvent)]
    assert recovered.instance_id == iid
    assert recovered.outcome == "failed"
    assert recovered.scope == "process"
    assert recovered.log_path == str(session.log_path)
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["outcome"] == "failed"
    assert "the profile is still locked" in inc["error"]
    (lost,) = driver_relaunch.recent_lost()
    assert lost["relaunched_to"] is None
    assert "the profile is still locked" in lost["relaunch_error"]
    await pool.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_launch_resolves_the_browser_process(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """A persistent launch asks /proc for its browser; the answer is kept."""
    _install_playwright_stub(monkeypatch)
    seen: list[tuple[str, Path]] = []
    sentinel = BrowserProcess(pid=_PID, user_data_dir=isolated, singleton_lock=True)

    def _find(kind: str, user_data_dir: Path | str, **_: Any) -> BrowserProcess:
        seen.append((kind, Path(user_data_dir)))
        return sentinel

    monkeypatch.setattr(process_crash, "find_browser_process", _find)
    pool = BrowserPool(recordings_dir=isolated / "rec")
    result = await pool.launch(kind="chromium", url="https://octowright.com", headed=False, label="pc")
    session = pool._sessions[result["instance_id"]]

    assert session._browser_process is sentinel
    assert seen and seen[0][0] == "chromium"
    assert seen[0][1] == session.user_data_dir
    await pool.shutdown()
