# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live: a browser process killed by a signal is a crash; closing its pages is not.

Real engines through a real ``BrowserPool`` on a persistent profile. The window
close is every page being closed while the browser keeps running (what a user
closing the last window looks like at the moment octowright decides); the crash
is a signal sent to the browser process itself. Headless by default; the
headed Chromium variant exercises the ``SingletonLock`` evidence and runs only
where a display is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.live_browser,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="process detection reads /proc"),
]

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Any]:
    from octowright import session_manifest
    from octowright.browser_pool import driver_relaunch, incidents
    from octowright.browser_pool.session_event_bus import session_event_bus

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "off")
    incidents.reset()
    driver_relaunch.reset()
    captured: list[Any] = []
    monkeypatch.setattr(session_event_bus, "publish_nowait", captured.append)
    return captured


def _crash_target(kind: str, root_pid: int) -> int:
    """The real browser: WebKit's MiniBrowser runs under a ``pw_run.sh`` wrapper."""
    if kind != "webkit":
        return root_pid
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            ppid = int((entry / "stat").read_text().rpartition(")")[2].split()[1])
            if ppid == root_pid and b"MiniBrowser" in (entry / "cmdline").read_bytes():
                return int(entry.name)
    raise AssertionError("no MiniBrowser under the webkit wrapper")


async def _launch(pool: Any, kind: str, *, headed: bool) -> Any:
    try:
        result = await pool.launch(kind=kind, url="about:blank", headed=headed, label=f"pcl-{kind}", protected=False)
    except Exception as exc:
        if any(s in str(exc).lower() for s in _NO_ENGINE):
            pytest.skip(f"{kind} unavailable: {exc}")
        raise
    return pool.get(result["instance_id"])


async def _until(predicate: Any, timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.05)


def _close_reason(session: Any) -> str | None:
    rows = [json.loads(line) for line in Path(session.log_path).read_text().splitlines() if line.strip()]
    closes = [r for r in rows if r["action"] == "close"]
    return closes[-1].get("reason") if closes else None


def _closed_reasons(events: list[Any]) -> list[str]:
    from octowright.browser_pool.events import SessionClosedEvent

    return [e.reason for e in events if isinstance(e, SessionClosedEvent)]


def _crash_scopes(events: list[Any]) -> list[str]:
    from octowright.browser_pool.events import SessionCrashedEvent

    return [e.scope for e in events if isinstance(e, SessionCrashedEvent)]


async def _signal_death(kind: str, sig: int, tmp_path: Path, events: list[Any], *, headed: bool = False) -> None:
    from octowright.browser_pool import BrowserPool, incidents

    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        session = await _launch(pool, kind, headed=headed)
        proc = session._browser_process
        assert proc is not None, "the browser process was not resolved at launch"
        assert f"{session.user_data_dir}".encode() in Path(f"/proc/{proc.pid}/cmdline").read_bytes()

        os.kill(_crash_target(kind, proc.pid), sig)

        await _until(lambda: _closed_reasons(events))
        assert _closed_reasons(events) == ["crashed"]
        assert _crash_scopes(events) == ["process"]
        await _until(lambda: _close_reason(session) is not None)
        assert _close_reason(session) == "crashed"
        assert "crashed" in pool._missing_session_message(session.instance_id)
        (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
        assert inc["instance_id"] == session.instance_id
    finally:
        await pool.shutdown()


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
# By name: parametrize runs at collection, before the Linux-only skip, and
# Windows has no SIGTRAP or SIGKILL.
@pytest.mark.parametrize("sig", ["SIGSEGV", "SIGTRAP", "SIGKILL"], ids=["SEGV", "TRAP", "KILL"])
async def test_a_signal_death_is_reported_as_a_process_crash(
    kind: str, sig: str, tmp_path: Path, events: list[Any]
) -> None:
    await _signal_death(kind, getattr(signal, sig), tmp_path, events)


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
async def test_closing_every_page_of_a_live_browser_is_a_user_close(
    kind: str, tmp_path: Path, events: list[Any]
) -> None:
    from octowright.browser_pool import BrowserPool, incidents

    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        session = await _launch(pool, kind, headed=False)
        assert session._browser_process is not None

        for page in list(session.pages):
            await page.close()

        await _until(lambda: _closed_reasons(events))
        assert _closed_reasons(events) == ["user_close"]
        assert _crash_scopes(events) == []
        await _until(lambda: _close_reason(session) is not None)
        assert _close_reason(session) == "external"
        assert incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH) == []
    finally:
        await pool.shutdown()


_HEADED = pytest.mark.skipif(
    not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"), reason="needs a display"
)

# Long enough that the browser has certainly exited -- and been seen dead --
# before octowright's loop gets to its first close signal. An orderly headed
# Chromium exits 55-142 ms after its last page close (measured).
_LOOP_STALL_SECONDS = 3.0


async def _exit_behind_a_stalled_loop(kind: str, how: str, tmp_path: Path, events: list[Any]) -> Any:
    """Make the browser exit while octowright's event loop is blocked.

    The first close signal is then judged after the process is already gone --
    the case liveness cannot read (an orderly close delivered late looks like a
    crash) and the only one where the ``SingletonLock`` changes the verdict.
    ``how``: ``orderly`` asks Chromium to shut down (CDP ``Browser.close``,
    which removes the lock on the way out -- measured 3/3 with the loop stalled
    3 s, lock gone, process dead); ``segv`` kills it (lock left, 3/3).
    """
    import time

    from octowright.browser_pool import BrowserPool

    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        session = await _launch(pool, kind, headed=True)
        proc = session._browser_process
        assert proc is not None and proc.singleton_lock is True, "headed Chromium must have written its lock"
        if how == "orderly":
            cdp = await session.context.new_cdp_session(session.page)
            pending = asyncio.ensure_future(cdp.send("Browser.close"))
            # Let the command reach the driver before the loop stops turning.
            for _ in range(5):
                await asyncio.sleep(0)
        else:
            pending = None
            os.kill(proc.pid, signal.SIGSEGV)
        time.sleep(_LOOP_STALL_SECONDS)  # the stall: nothing on the loop runs
        await _until(lambda: _closed_reasons(events))
        if pending is not None:
            with contextlib.suppress(Exception):
                await pending
        await _until(lambda: _close_reason(session) is not None)
        return session
    finally:
        await pool.shutdown()


@_HEADED
async def test_headed_chromium_orderly_exit_seen_late_is_a_close_by_its_lock(tmp_path: Path, events: list[Any]) -> None:
    """The lock path for real: dead at the first signal, lock removed -> a close.

    Liveness alone reads this as a crash (the process is gone), so this test
    fails if ``exit_verdict`` stops consulting the lock or inverts it.
    """
    from octowright.browser_pool import incidents

    session = await _exit_behind_a_stalled_loop("chromium", "orderly", tmp_path, events)

    assert not (session.user_data_dir / "SingletonLock").is_symlink(), "an orderly exit removes the lock"
    assert _closed_reasons(events) == ["user_close"]
    assert _crash_scopes(events) == []
    assert _close_reason(session) == "external"
    assert incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH) == []


@_HEADED
async def test_headed_chromium_signal_death_seen_late_is_a_crash_by_its_lock(tmp_path: Path, events: list[Any]) -> None:
    """Same stall, a signal instead: the lock stays, so the verdict is a crash on
    ``singleton_lock`` evidence -- the only evidence allowed to reopen a window."""
    from octowright.browser_pool import incidents

    session = await _exit_behind_a_stalled_loop("chromium", "segv", tmp_path, events)

    assert _closed_reasons(events) == ["crashed"]
    assert _crash_scopes(events) == ["process"]
    assert _close_reason(session) == "crashed"
    (inc,) = incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH)
    assert inc["evidence"] == "singleton_lock"
