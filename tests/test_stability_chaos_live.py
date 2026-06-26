# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live chaos regression tests for the stability fixes.

These drive a REAL browser and induce the actual failures, because the bugs they
guard against were invisible to mocked unit tests: the crash-recovery shipped
green (100% unit coverage) while `page.reload()` silently failed to heal a real
`chrome-headless-shell` renderer crash. Mock the failure and you can't catch it —
so here we crash a real renderer (CDP `Page.crash`) and kill a real driver
(`Playwright.stop()`) and assert the pool actually recovers.

Marked `live_browser`; skipped where no engine is installed.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from tests._pool_invariants import assert_pool_consistent

pytestmark = pytest.mark.live_browser

_NO_ENGINE = (
    "executable doesn't exist",
    "missing x server",
    "no protocol specified",
    "playwright install",
)


def _skip_if_no_engine(exc: Exception) -> None:
    if any(s in str(exc).lower() for s in _NO_ENGINE):
        pytest.skip(f"live browser engine unavailable: {exc}")
    raise exc


async def _launch(pool: object, **kw: object) -> dict:
    try:
        return await pool.launch(kind="chromium", headed=False, **kw)  # type: ignore[attr-defined]
    except Exception as exc:
        _skip_if_no_engine(exc)
        raise  # unreachable


async def test_real_renderer_crash_recovers_and_stays_usable(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """REGRESSION: SIGSEGV-class renderer crash → the session self-heals (new page
    in the surviving context) and is usable again. This is the test that would
    have caught the broken `page.reload()` recovery."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool, crash_recovery
    from octowright.browser_pool import incidents as _incidents
    from octowright.browser_pool import pool as _pool

    rec = tmp_path / "rec"  # type: ignore[operator]
    rec.mkdir()
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    crash_recovery.reset_stats()
    _incidents.reset()

    pool = BrowserPool()
    try:
        result = await _launch(pool, url="data:text/html,<h1>before</h1>", label="chaos-crash")
        session = pool.get(result["instance_id"])

        # CDP Page.crash kills the renderer deterministically (no chrome:// quirks).
        cdp = await session.context.new_cdp_session(session.page)
        with contextlib.suppress(Exception):
            await cdp.send("Page.crash")

        # Wait for the async recovery (crash listener → replace page → goto).
        for _ in range(100):
            if crash_recovery.recovery_stats()["recoveries"] >= 1:
                break
            await asyncio.sleep(0.1)

        stats = crash_recovery.recovery_stats()
        if stats["crashes"] == 0:
            pytest.skip("CDP Page.crash did not deliver page.on('crash') on this build")
        assert stats["recoveries"] >= 1, f"crash was not recovered: {stats}"
        assert session._crashed is False
        # The session is usable again: a navigation on the replaced page succeeds.
        nav = await session.navigate("data:text/html,<h1>after</h1>")
        assert nav.get("url", "").startswith("data:text/html")
        # And an incident record articulates what happened.
        inc = _incidents.recent(category=_incidents.CATEGORY_RENDERER_CRASH)
        assert inc and inc[-1]["outcome"] == "recovered"
        # The pool is internally consistent after recovery: the replaced page is
        # the session's active page and a member of its page list, counts agree,
        # the eviction/incident rings are bounded.
        assert_pool_consistent(pool)
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()


async def test_dead_driver_self_heals_on_next_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """REGRESSION: the shared Playwright driver dying no longer bricks the pool —
    the next launch detects it, rebuilds the driver, and succeeds."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool
    from octowright.browser_pool import pool as _pool

    rec = tmp_path / "rec"  # type: ignore[operator]
    rec.mkdir()
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)

    pool = BrowserPool()
    try:
        first = await _launch(pool, url="about:blank", label="chaos-driver-1")
        assert first["instance_id"]
        assert pool.driver_restart_count() == 0

        # Kill the shared driver — every browser's pipe is now dead.
        await pool._pw.stop()  # type: ignore[union-attr]

        # The next launch must self-heal: detect driver death, rebuild, succeed.
        second = await _launch(pool, url="about:blank", label="chaos-driver-2")
        assert second["instance_id"]
        assert pool.driver_restart_count() == 1, "driver should have been rebuilt exactly once"
        # After the driver self-heals: the session lost with the dead driver was
        # evicted (not a zombie in the registry) and only the fresh one is live,
        # with the pool's consistency invariants intact.
        assert_pool_consistent(pool)
        assert pool.active_count() == 1
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()
