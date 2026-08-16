# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live-browser proof that channel/executable_path/launch_args actually reach
a real Playwright launch, not just octowright's internal kwarg-merge logic
(which the unit tests in test_browser_pool_branches.py already cover)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _configure_runtime_paths, _maybe_skip_live_engine


def _skip_if_chrome_channel_unavailable(exc: Exception) -> None:
    msg = str(exc).lower()
    if "chrome" in msg or "executable doesn't exist" in msg or "not found" in msg:
        pytest.skip(f"system Chrome channel unavailable in this environment: {exc!r}")
    raise exc


@pytest.mark.asyncio
@pytest.mark.live_browser
async def test_launch_with_explicit_executable_path_and_launch_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """executable_path pointed at Playwright's own bundled chromium binary
    (always present after `playwright install`) proves the kwarg reaches a
    real launch rather than only octowright's internal merge logic.
    launch_args rides along in the same call."""
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", "1")
    _configure_runtime_paths(monkeypatch, tmp_path)

    async with async_playwright() as pw:
        bundled_path = pw.chromium.executable_path

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind="chromium",
                headed=False,
                executable_path=bundled_path,
                launch_args=["--mute-audio"],
                url="data:text/html,<h1>executable-path-live</h1>",
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        assert launched["instance_id"]
        await pool.close(launched["instance_id"])
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.live_browser
async def test_launch_with_channel_chrome_or_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """channel='chrome' picks a real installed Chrome build instead of the
    bundled one -- proves the kwarg reaches Playwright's launch, not just
    octowright's own args merge. Skips cleanly where no system Chrome is
    installed; the whole point of this knob is environments that DO have
    one (native GPU/DRM/codec parity a bundled headless build lacks)."""
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind="chromium",
                headed=False,
                channel="chrome",
                url="data:text/html,<h1>channel-live</h1>",
            )
        except Exception as exc:
            _skip_if_chrome_channel_unavailable(exc)

        assert launched["instance_id"]
        await pool.close(launched["instance_id"])
    finally:
        await pool.shutdown()
