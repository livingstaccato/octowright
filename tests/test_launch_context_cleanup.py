# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A launch that fails or is cancelled AFTER the browser opened must close it.

``_open_browser_context`` returns the context only once every setup step has
finished, so a failure in any later step -- picking the page, opening one,
installing the SSRF guard or the scoped header routes, or the launch deadline
cancelling the task during any of them -- left the caller's cleanup holding
``context=None``. The browser stayed running, and on a persistent profile it
kept the ``SingletonLock``, so every later launch of that profile failed "in
use". Each await after the engine launch is driven to fail here, both by an
exception and by cancelling the task while it waits.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import launch_helpers

#: Every await ``_open_browser_context`` makes after the engine launched.
_PERSISTENT_STEPS = ("new_page", "navigation_guard", "header_routes")
_EPHEMERAL_STEPS = ("new_context", "new_page", "navigation_guard", "header_routes")


class _Script:
    """Which step misbehaves, and how: ``raise`` an error or ``hang`` until cancelled."""

    def __init__(self, step: str, mode: str) -> None:
        self.step = step
        self.mode = mode
        self.reached = asyncio.Event()
        self.closed: list[str] = []

    async def at(self, step: str) -> None:
        if step != self.step:
            return
        self.reached.set()
        if self.mode == "raise":
            raise RuntimeError(f"{step} failed")
        await asyncio.Event().wait()


class _FakeContext:
    def __init__(self, script: _Script, *, browser: Any = None) -> None:
        self._script = script
        self.pages: list[Any] = []
        self.browser = browser

    async def new_page(self) -> object:
        await self._script.at("new_page")
        page = object()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self._script.closed.append("context")


class _FakeBrowser:
    def __init__(self, script: _Script) -> None:
        self._script = script

    async def new_context(self, **_: Any) -> _FakeContext:
        await self._script.at("new_context")
        return _FakeContext(self._script, browser=self)

    async def close(self) -> None:
        self._script.closed.append("browser")


class _FakeBrowserType:
    def __init__(self, script: _Script) -> None:
        self._script = script

    async def launch_persistent_context(self, user_data_dir: str, **_: Any) -> _FakeContext:
        return _FakeContext(self._script)

    async def launch(self, **_: Any) -> _FakeBrowser:
        return _FakeBrowser(self._script)


def _install_route_steps(monkeypatch: pytest.MonkeyPatch, script: _Script) -> None:
    async def guard(_context: Any) -> None:
        await script.at("navigation_guard")

    async def header_routes(_context: Any, _headers: Any, _patterns: Any) -> None:
        await script.at("header_routes")

    monkeypatch.setattr(launch_helpers, "install_navigation_guard", guard)
    monkeypatch.setattr(launch_helpers, "install_scoped_header_routes", header_routes)


def _open(script: _Script, *, persistent: bool, tmp_path: Path) -> Any:
    return launch_helpers._open_browser_context(
        browser_type=_FakeBrowserType(script),
        kind="firefox",
        profile=None,
        session_user_data_dir=str(tmp_path) if persistent else None,
        headless=True,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={},
    )


def _expected_closed(persistent: bool, step: str) -> list[str]:
    if persistent:
        return ["context"]
    # The ephemeral browser exists from the engine launch on; its context only
    # once new_context returned.
    return ["browser"] if step == "new_context" else ["context", "browser"]


_CASES = [(True, step) for step in _PERSISTENT_STEPS] + [(False, step) for step in _EPHEMERAL_STEPS]


@pytest.mark.parametrize(("persistent", "step"), _CASES)
async def test_a_failed_setup_step_closes_what_was_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, persistent: bool, step: str
) -> None:
    script = _Script(step, "raise")
    _install_route_steps(monkeypatch, script)

    with pytest.raises(RuntimeError, match=f"{step} failed"):
        await _open(script, persistent=persistent, tmp_path=tmp_path)

    assert script.closed == _expected_closed(persistent, step)


@pytest.mark.parametrize(("persistent", "step"), _CASES)
async def test_a_cancelled_setup_step_closes_what_was_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, persistent: bool, step: str
) -> None:
    """The launch deadline (``OCTOWRIGHT_BROWSER_LAUNCH_TIMEOUT_SECONDS``) and a
    client disconnect both arrive as a cancellation of the launching task."""
    script = _Script(step, "hang")
    _install_route_steps(monkeypatch, script)

    task = asyncio.ensure_future(_open(script, persistent=persistent, tmp_path=tmp_path))
    await asyncio.wait_for(script.reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert script.closed == _expected_closed(persistent, step)


async def test_a_successful_open_closes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = _Script("none", "raise")
    _install_route_steps(monkeypatch, script)

    _browser, context, page, _udd = await _open(script, persistent=True, tmp_path=tmp_path)

    assert script.closed == []
    assert page in context.pages


def _processes_using(path: Path) -> list[int]:
    """Pids whose command line names ``path`` (Linux /proc)."""
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if str(path).encode() in cmdline:
            pids.append(int(entry.name))
    return pids


@pytest.mark.live_browser
@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="reads /proc")
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_live_a_failed_route_install_releases_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, anyio_backend: str
) -> None:
    """Real Chromium, headless: a launch that fails after the browser opened
    must not leave it running on the user-data-dir. Measured without the fix:
    the browser stayed up, and HEADED (under xvfb-run) the next launch of the
    same session label was refused -- "Opening in existing browser session
    ... the profile is already in use by another instance of Chromium"."""
    from octowright.browser_pool.lifecycle import shutdown_pool
    from octowright.browser_pool.pool import BrowserPool

    real_guard = launch_helpers.install_navigation_guard
    calls = {"n": 0}

    async def guard_fails_once(context: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("route install failed")
        await real_guard(context)

    monkeypatch.setattr(launch_helpers, "install_navigation_guard", guard_fails_once)
    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    page = "data:text/html,<p>ok</p>"
    try:
        try:
            with pytest.raises(RuntimeError, match="route install failed"):
                await pool.launch(kind="chromium", headed=False, session=True, label="w2-live", url=page)
        except Exception as exc:  # pragma: no cover - host without chromium
            pytest.skip(f"chromium unavailable: {exc}")
        session_dir = pool._session_profile_dirs[("w2-live", "chromium")]
        assert _processes_using(session_dir) == []
        result = await asyncio.wait_for(
            pool.launch(kind="chromium", headed=False, session=True, label="w2-live", url=page), timeout=60
        )
        assert result["instance_id"]
    finally:
        await shutdown_pool(pool)
