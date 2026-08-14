# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that ``SessionOperationGate`` correctly serializes real
Playwright operations -- the fakes-based proofs in ``test_operation_gate_
integration.py`` (Task 13) exercise the state machine in isolation, but only
a real browser can show the gate actually blocks/admits/rejects real CDP
calls rather than a mocked stand-in that happens to agree with the design.

One focused test drives two headless Chromium sessions through all four
acceptance behaviors from the design spec:

1. A manual action queues FIFO behind a running macro and lands after it.
2. The gate is per-session: holding session A does not block session B.
3. A rejection (queue timeout) does not poison the browser, driver, event
   loop, or MCP tool registry -- both sessions keep working afterward.
4. A close cutoff lets an already-queued operation finish before closing,
   but rejects a later arrival outright once the gate reports "closing".

Marked ``live_browser`` (real Chromium) and ``integration_local`` (drives the
local demo playground server); skipped where no engine is installed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool
from octowright.macros import storage as macro_storage
from octowright.macros.execution import run_macro
from octowright.session.operation_gate import SessionBusyTimeoutError, SessionClosingError

pytestmark = [pytest.mark.live_browser, pytest.mark.integration_local]

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


async def _launch(pool: BrowserPool, **kw: object) -> dict[str, Any]:
    try:
        return await pool.launch(kind="chromium", headed=False, **kw)
    except Exception as exc:
        _skip_if_no_engine(exc)
        raise  # unreachable


async def _wait_for_snapshot(
    session: Any,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Bounded poll on ``operation_snapshot()`` -- never infer gate-state
    ordering from a bare sleep; this is the one seam that observes it."""
    async with asyncio.timeout(timeout):
        while True:
            snapshot = session.operation_snapshot()
            if predicate(snapshot):
                return snapshot
            await asyncio.sleep(0.01)


def _configure_runtime_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    prof = tmp_path / "profiles"
    rec.mkdir()
    prof.mkdir()
    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(rec))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(prof))

    import octowright.browser_pool.launch_helpers as _launch_helpers
    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import engine_profiles as _profiles
    from octowright import personas as _personas

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_launch_helpers, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", prof)
    monkeypatch.setattr(_personas, "PROFILES_DIR", prof)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", prof)


def _write_gate_order_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    macros_dir = tmp_path / "macros"
    monkeypatch.setattr(macro_storage, "MACROS_DIR", macros_dir)
    macro_storage.write_macro(
        name="gate-order",
        macro={
            "actions": [
                {
                    "action": "evaluate",
                    "expression": (
                        "() => { window.__gate_order = window.__gate_order || []; "
                        "window.__gate_order.push('macro-1'); }"
                    ),
                },
                {
                    "action": "evaluate",
                    "expression": "() => window.__gate_order.push('macro-2')",
                },
            ],
        },
    )


async def test_operation_gate_serializes_real_chromium_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    integration_local_base_url: str,
    playground_server: object,
) -> None:
    pytest.importorskip("playwright")
    _ = playground_server
    _configure_runtime_paths(monkeypatch, tmp_path)
    _write_gate_order_macro(monkeypatch, tmp_path)

    pool = BrowserPool(operation_queue_timeout_seconds=0.5)
    try:
        launch_a = await _launch(
            pool,
            url=f"{integration_local_base_url}/",
            label="gate-a",
            viewport_w=800,
            viewport_h=600,
        )
        launch_b = await _launch(
            pool,
            url=f"{integration_local_base_url}/",
            label="gate-b",
            viewport_w=800,
            viewport_h=600,
        )
        session_a = pool.get(launch_a["instance_id"])
        session_b = pool.get(launch_b["instance_id"])

        # ── Behavior 1: a manual action queues FIFO behind a running macro ──
        macro_task = asyncio.create_task(run_macro(session_a, "gate-order", slowmo_ms=40))
        await _wait_for_snapshot(session_a, lambda s: s["active_operation"] == "macro_run")
        manual_task = asyncio.create_task(session_a.evaluate("() => window.__gate_order.push('manual')"))
        await macro_task
        await manual_task
        final_order = await session_a.evaluate("window.__gate_order")
        assert final_order == ["macro-1", "macro-2", "manual"]

        # ── Behavior 2: the gate is per-session, not a global lock ──
        hold_task = asyncio.create_task(session_a.evaluate("() => new Promise(resolve => setTimeout(resolve, 1000))"))
        await _wait_for_snapshot(session_a, lambda s: s["active_operation"] == "browser_evaluate")
        b_result = await asyncio.wait_for(session_b.evaluate("1 + 1"), timeout=2.0)
        assert b_result == 2
        assert not hold_task.done(), "session B's evaluate must not wait behind session A's hold"
        await hold_task  # let the 1s hold finish cleanly before the next behavior

        # ── Behavior 3: a rejection doesn't poison the browser/driver/tools ──
        hold_task_2 = asyncio.create_task(session_a.evaluate("() => new Promise(resolve => setTimeout(resolve, 1000))"))
        await _wait_for_snapshot(session_a, lambda s: s["active_operation"] == "browser_evaluate")
        with pytest.raises(SessionBusyTimeoutError):
            await session_a.evaluate("1 + 1")
        await hold_task_2

        from octowright.server.browser import inspect as _inspect_tools

        monkeypatch.setattr(_inspect_tools, "pool", pool)
        result_a = await _inspect_tools.browser_evaluate(session_a.instance_id, "2 + 2")
        result_b = await _inspect_tools.browser_evaluate(session_b.instance_id, "3 + 3")
        assert result_a["result"] == 4
        assert result_b["result"] == 6

        # ── Behavior 4: close cutoff admits the earlier queued op, rejects later ──
        # The hold here must be well under the pool's 0.5s queue timeout --
        # unlike behaviors 2/3, "early_task" below is itself a plain queued
        # evaluate bound by that same default timeout, so it must be admitted
        # (hold released) before ITS OWN 500ms admission window expires. Kept
        # at 200ms, leaving 300ms of margin rather than the original
        # 150ms/250ms pairing's 100ms -- that margin flaked on contended
        # macOS CI runners (observed timing out at ~252-253ms, just over the
        # old 250ms ceiling). 0.5s (not the 1.0s first tried) keeps behavior
        # 3's 1000ms holds comfortably ABOVE the timeout too -- a 1.0s pool
        # timeout made that a coin flip instead of a reliable timeout.
        hold_task_3 = asyncio.create_task(session_a.evaluate("() => new Promise(resolve => setTimeout(resolve, 200))"))
        await _wait_for_snapshot(session_a, lambda s: s["active_operation"] == "browser_evaluate")
        early_task = asyncio.create_task(session_a.evaluate("5 + 5"))
        await _wait_for_snapshot(session_a, lambda s: s["queue_depth"] >= 1)
        close_task = asyncio.create_task(pool.close(session_a.instance_id))
        await _wait_for_snapshot(session_a, lambda s: s["state"] == "closing")

        with pytest.raises(SessionClosingError):
            await session_a.evaluate("6 + 6")

        await hold_task_3
        assert await early_task == 10
        close_result = await close_task
        assert close_result["closed"] is True
        assert session_a.instance_id not in pool._sessions
        assert session_a.instance_id not in pool._closing_sessions

        assert await session_b.evaluate("7 + 7") == 14
    finally:
        await pool.shutdown()
