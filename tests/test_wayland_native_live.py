# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Native Wayland against a real Chromium. Opt-in: ``OCTOWRIGHT_LIVE_WAYLAND=1``.

Both tests open a HEADED browser, so they are opt-in on top of ``live_browser``
-- otherwise ``make test`` on a developer's Wayland desktop would put windows
on it, and a Linux CI runner (no compositor) has nothing to test natively.

- ``test_auto_runs_natively_in_a_wayland_session`` needs a real compositor
  socket. It proves Chromium STARTS with the flags; whether a pinch then
  reaches the page needs a human with a trackpad.
- ``test_auto_falls_back_to_x11_when_the_socket_is_dead`` points
  ``WAYLAND_DISPLAY`` at a plain file (exists, so auto chooses Wayland; not a
  socket, so Chromium cannot connect) and needs an X server for the retry.
  Run it under ``xvfb-run`` so no window reaches a real desktop:
  ``OCTOWRIGHT_LIVE_WAYLAND=1 xvfb-run -a uv run pytest tests/test_wayland_native_live.py -k falls_back``.
- ``test_explicit_true_with_a_dead_socket_fails_loudly_without_a_driver_reset``
  opens no window at all (Chromium exits during platform init).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from octowright import defaults
from octowright.browser_pool import wayland

pytestmark = [
    pytest.mark.live_browser,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Wayland is Linux-only"),
    pytest.mark.skipif(os.environ.get("OCTOWRIGHT_LIVE_WAYLAND") != "1", reason="opt-in: OCTOWRIGHT_LIVE_WAYLAND=1"),
]


def _make_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    from octowright.browser_pool import pool as _pool
    from octowright.browser_pool.pool import BrowserPool

    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    return BrowserPool()


async def test_auto_runs_natively_in_a_wayland_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    monkeypatch.delenv(wayland.WAYLAND_NATIVE_ENV, raising=False)
    socket = wayland.wayland_socket_path()
    if socket is None or not socket.exists():
        pytest.skip("no Wayland compositor socket")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await pool.launch(kind="chromium", url="about:blank", headed=True, ephemeral=True)  # type: ignore[attr-defined]
        assert res["wayland_native"]["effective"] is True, res["wayland_native"]
        assert "wayland_warning" not in res
    finally:
        # shutdown, not close_all: close_all leaves the Playwright driver
        # running, and its subprocess transport is then collected after the
        # test loop has closed ("Event loop is closed", unclosed transport).
        await pool.shutdown()  # type: ignore[attr-defined]


async def test_auto_falls_back_to_x11_when_the_socket_is_dead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    if not os.environ.get("DISPLAY"):
        pytest.skip("needs an X server for the X11 retry (run under xvfb-run)")
    dead = tmp_path / "not-a-socket"
    dead.touch()
    monkeypatch.delenv(wayland.WAYLAND_NATIVE_ENV, raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", str(dead))
    # The real-desktop condition: without an ozone switch Chromium picks the
    # platform from this, so a retry that merely dropped the Wayland flag
    # chose Wayland again and died the same way.
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await pool.launch(kind="chromium", url="about:blank", headed=True, ephemeral=True)  # type: ignore[attr-defined]
        report = res["wayland_native"]
        assert report["effective"] is False
        assert report["reason"] == "fallback_x11"
        assert "wayland" in report["fallback_reason"].lower()
        assert pool.engine_health()["chromium"]["outcome"] == "ok"  # type: ignore[attr-defined]
        assert pool.driver_restart_count() == 0  # type: ignore[attr-defined]
    finally:
        # shutdown, not close_all: close_all leaves the Playwright driver
        # running, and its subprocess transport is then collected after the
        # test loop has closed ("Event loop is closed", unclosed transport).
        await pool.shutdown()  # type: ignore[attr-defined]


async def test_explicit_true_with_a_dead_socket_fails_loudly_without_a_driver_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chromium exits before opening any window, so this needs no display."""
    pytest.importorskip("playwright")
    dead = tmp_path / "not-a-socket"
    dead.touch()
    monkeypatch.setenv("WAYLAND_DISPLAY", str(dead))
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        with pytest.raises(wayland.WaylandLaunchError, match="Failed to connect to Wayland display"):
            await pool.launch(kind="chromium", url="about:blank", headed=True, ephemeral=True, wayland_native=True)  # type: ignore[attr-defined]
        # Read as a dead driver, this would have stopped the shared driver.
        assert pool.driver_restart_count() == 0  # type: ignore[attr-defined]
    finally:
        # shutdown, not close_all: close_all leaves the Playwright driver
        # running, and its subprocess transport is then collected after the
        # test loop has closed ("Event loop is closed", unclosed transport).
        await pool.shutdown()  # type: ignore[attr-defined]
