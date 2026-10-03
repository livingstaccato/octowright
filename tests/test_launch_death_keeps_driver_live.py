# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One browser dying at launch must not take the shared driver -- and every
other browser -- down with it. Real Chromium, headless only.

The dying browser is an ``executable_path`` that exits at once: deterministic on
any host, headless or not, and it produces exactly the error that used to be
read as driver death (``TargetClosedError: BrowserType.launch: Target page,
context or browser has been closed``). The probe tests run against a real
Playwright driver, so a Playwright upgrade that moves the internals
``driver_health.driver_confirmed_dead`` reads fails here rather than silently
degrading to the old reset-on-text behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright.browser_pool import driver_health
from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

PAGE = "data:text/html,<p id=x>alive</p>"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _exits_at_once(tmp_path: Path) -> Path:
    script = tmp_path / "dies-at-launch.sh"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    return script


async def _launch_healthy(pool: BrowserPool) -> str:
    try:
        result = await pool.launch(kind="chromium", headed=False, ephemeral=True, url=PAGE)
    except Exception as exc:
        pytest.skip(f"chromium unavailable: {exc}")
    return str(result["instance_id"])


@pytest.mark.anyio
@pytest.mark.skipif(os.name == "nt", reason="the dying executable is a POSIX shell script")
async def test_a_browser_dying_at_launch_leaves_the_others_alive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", "1")
    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        survivor = await _launch_healthy(pool)

        with pytest.raises(Exception) as excinfo:
            await pool.launch(
                kind="chromium",
                headed=False,
                ephemeral=True,
                url=PAGE,
                executable_path=str(_exits_at_once(tmp_path)),
            )
        # The precondition that made this a bug: the text alone says "driver dead".
        assert driver_health.is_driver_dead_error(excinfo.value), str(excinfo.value)

        assert pool.driver_restart_count() == 0
        page = pool.get(survivor).page
        assert await page.locator("#x").inner_text() == "alive"
        assert pool.engine_health()["chromium"]["outcome"] == "error"
    finally:
        await pool.shutdown()


@pytest.mark.anyio
async def test_a_genuinely_dead_driver_is_still_reset_and_the_launch_retried(tmp_path: Path) -> None:
    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        await _launch_healthy(pool)
        assert pool._pw is not None
        # Process.kill(), not os.kill(pid, SIGKILL): signal.SIGKILL does not
        # exist on Windows, and the Windows legs run live_browser tests.
        proc = pool._pw._impl_obj._connection._transport._proc
        proc.kill()
        await proc.wait()

        result = await pool.launch(kind="chromium", headed=False, ephemeral=True, url=PAGE)

        assert pool.driver_restart_count() == 1
        page = pool.get(str(result["instance_id"])).page
        assert await page.locator("#x").inner_text() == "alive"
    finally:
        await pool.shutdown()


@pytest.mark.anyio
async def test_probe_against_a_real_driver() -> None:
    """Alive while running; dead once killed; dead once stopped."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        assert await driver_health.driver_confirmed_dead(pw) is False
        proc = pw._impl_obj._connection._transport._proc
        proc.kill()  # portable: SIGKILL on POSIX, TerminateProcess on Windows
        await proc.wait()
        assert await driver_health.driver_confirmed_dead(pw) is True
    finally:
        try:
            await pw.stop()
        except Exception:
            pass
    assert await driver_health.driver_confirmed_dead(pw) is True

    stopped = await async_playwright().start()
    await stopped.stop()
    assert await driver_health.driver_confirmed_dead(stopped) is True


@pytest.mark.anyio
@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP is POSIX-only")
async def test_a_hung_driver_is_not_confirmed_dead_and_its_stop_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured: a SIGSTOPped driver shows no local flag, never answers, and
    ``Playwright.stop()`` against it does not return. The probe must not call
    that death; a reset that does happen must still return, killing it."""
    import signal

    from playwright.async_api import async_playwright

    monkeypatch.setattr(driver_health, "DRIVER_STOP_TIMEOUT_SECONDS", 0.5)
    pw = await async_playwright().start()
    proc = pw._impl_obj._connection._transport._proc
    os.kill(proc.pid, signal.SIGSTOP)
    try:
        assert await driver_health.driver_confirmed_dead(pw, timeout=0.3) is False
        await driver_health.stop_driver(pw)
        assert proc.returncode is not None or await proc.wait() is not None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
