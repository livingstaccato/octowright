# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live regression for the crash-recovery GIVE-UP paths + their LLM signal.

The happy path (crash → recovered) is covered elsewhere. These drive the two ways
recovery stops helping, against a REAL renderer, and assert the agent learns it:

* exhaustion — crashes past CRASH_RECOVERY_MAX → recovery gives up, a
  ``SessionRecoveredEvent(outcome="exhausted")`` is published (→ the LLM's
  ``browser_recovered`` notification), an ``exhausted`` incident is recorded, and
  ``health.assess`` rolls up to ``degraded``.
* failure — the dead page can't be replaced → ``outcome="failed"`` +
  ``recovery_failures`` increments.

Marked ``live_browser`` (real browser + induced crashes → macOS dialogs), so it's
deselected from the fast ``make test``.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest

pytestmark = pytest.mark.live_browser

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")


async def _launch(pool: Any) -> dict[str, Any]:
    try:
        return await pool.launch(
            kind="chromium", headed=False, url="data:text/html,<h1>x</h1>", label="giveup", ephemeral=True
        )
    except Exception as exc:
        if any(s in str(exc).lower() for s in _NO_ENGINE):
            pytest.skip(f"live browser engine unavailable: {exc}")
        raise


async def _crash(session: Any) -> None:
    # Page.crash kills the renderer and the CDP command never ACKs. On the give-up
    # paths nothing replaces the page (the happy path's replacement is what
    # incidentally unblocks the send elsewhere), so awaiting the response would hang
    # forever — bound it. The command still reaches the renderer, so the real
    # page.on("crash") fires regardless.
    with contextlib.suppress(Exception):
        cdp = await session.context.new_cdp_session(session.page)
        with anyio.move_on_after(3):
            await cdp.send("Page.crash")


async def _wait(predicate: Any, timeout: float) -> bool:
    with anyio.move_on_after(timeout):
        while not predicate():
            await anyio.sleep(0.1)
        return True
    return predicate()


def _recovered_outcomes(events: list[Any]) -> list[str]:
    return [e.outcome for e in events if type(e).__name__ == "SessionRecoveredEvent"]


@pytest.mark.anyio
async def test_crash_loop_exhaustion_signals_giveup(tmp_path: Any) -> None:
    pytest.importorskip("playwright")
    from octowright import defaults as _d
    from octowright.browser_pool import BrowserPool, crash_recovery
    from octowright.browser_pool import health as _health
    from octowright.browser_pool import incidents as _incidents
    from octowright.browser_pool import pool as _pool
    from octowright.browser_pool import session_event_bus as _bus

    mp = pytest.MonkeyPatch()
    rec = tmp_path / "rec"
    rec.mkdir()
    mp.setattr(_d, "RECORDINGS_DIR", rec)
    mp.setattr(_pool, "RECORDINGS_DIR", rec)
    mp.setattr(_d, "CRASH_RECOVERY_MAX", 0)  # give up on the FIRST crash — no recovery attempt
    crash_recovery.reset_stats()
    _incidents.reset()
    events: list[Any] = []
    mp.setattr(_bus.session_event_bus, "publish_nowait", events.append)

    pool = BrowserPool()
    try:
        r = await _launch(pool)
        await _crash(pool.get(r["instance_id"]))  # one real renderer crash → cap(0) hit → exhausted
        if not await _wait(lambda: "exhausted" in _recovered_outcomes(events), 12):
            if crash_recovery.recovery_stats()["crashes"] == 0:
                pytest.skip("CDP Page.crash did not deliver page.on('crash') on this build")
            pytest.fail("give-up was not signalled")

        # The LLM learns it via browser_recovered(outcome=exhausted).
        assert "exhausted" in _recovered_outcomes(events)
        # An incident records it, and health rolls up to degraded.
        counts = _incidents.counts(category=_incidents.CATEGORY_RENDERER_CRASH)
        assert counts.get("exhausted", 0) >= 1
        verdict = _health.assess(driver_restarts=0, recovery_failures=0, recovery_exhausted=counts["exhausted"])
        assert verdict["status"] == "degraded"
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()
        mp.undo()


@pytest.mark.anyio
async def test_recovery_failure_signals_failed(tmp_path: Any) -> None:
    pytest.importorskip("playwright")
    from octowright import defaults as _d
    from octowright.browser_pool import BrowserPool, crash_recovery
    from octowright.browser_pool import incidents as _incidents
    from octowright.browser_pool import pool as _pool
    from octowright.browser_pool import session_event_bus as _bus

    mp = pytest.MonkeyPatch()
    rec = tmp_path / "rec"
    rec.mkdir()
    mp.setattr(_d, "RECORDINGS_DIR", rec)
    mp.setattr(_pool, "RECORDINGS_DIR", rec)
    crash_recovery.reset_stats()
    _incidents.reset()
    events: list[Any] = []
    mp.setattr(_bus.session_event_bus, "publish_nowait", events.append)

    pool = BrowserPool()
    try:
        r = await _launch(pool)
        session = pool.get(r["instance_id"])
        # The dead page can't be replaced — new_page in the (surviving) context fails.
        mp.setattr(session.context, "new_page", AsyncMock(side_effect=RuntimeError("cannot create page")))
        await _crash(session)
        ok = await _wait(lambda: "failed" in _recovered_outcomes(events), 10)
        if not ok and crash_recovery.recovery_stats()["crashes"] == 0:
            pytest.skip("CDP Page.crash did not deliver page.on('crash') on this build")
        assert "failed" in _recovered_outcomes(events), "recovery failure was not signalled"
        assert crash_recovery.recovery_stats()["recovery_failures"] >= 1
        assert session._crashed is True  # left crashed → the LLM is told to relaunch
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()
        mp.undo()
