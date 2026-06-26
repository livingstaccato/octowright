# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Chaos-monkey soak: randomized CONCURRENT pool ops + invariants after each round.

The targeted live tests each induce ONE failure in isolation. Real breakage hides
in the SEAMS — a close racing a crash-recovery, a driver-death mid-launch, a
navigate on a session another op is evicting. This fires a random mix of
launch/close/crash/navigate (with an occasional driver-death) CONCURRENTLY each
round and asserts the pool's consistency invariants still hold afterward. A race
that corrupts pool state (zombie session, count drift, unbounded ring) trips
``assert_pool_consistent``; a leaked browser trips the end-of-run orphan sweep.

Individual ops may FAIL (an op on a session another op just closed is expected) —
only the invariants must hold. Seeded for reproducibility (the seed is printed).
Marked ``live_browser`` (real browsers, induces crashes → macOS dialogs) so it's
deselected from the fast ``make test``.
"""

from __future__ import annotations

import contextlib
import random

import pytest

from tests._pool_invariants import assert_pool_consistent, orphan_browser_pids

pytestmark = pytest.mark.live_browser

_ROUNDS = 6
_OPS_PER_ROUND = 6
_SEED = 1337  # fixed so a failure reproduces; printed below
_DRIVER_DEATH_ROUNDS = {2, 4}  # induce the shared-driver SPOF mid-soak

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")


async def _op_launch(pool: object, _rng: random.Random) -> None:
    with contextlib.suppress(Exception):  # cap / transient failures are tolerated
        await pool.launch(  # type: ignore[attr-defined]
            kind="chromium", headed=False, url="data:text/html,<h1>chaos</h1>", label="monkey", ephemeral=True
        )


async def _op_close(pool: object, rng: random.Random) -> None:
    ids = [s.instance_id for s in pool.iter_sessions()]  # type: ignore[attr-defined]
    if ids:
        with contextlib.suppress(Exception):
            await pool.close(rng.choice(ids))  # type: ignore[attr-defined]


async def _op_crash(pool: object, rng: random.Random) -> None:
    sessions = list(pool.iter_sessions())  # type: ignore[attr-defined]
    if not sessions:
        return
    with contextlib.suppress(Exception):
        s = rng.choice(sessions)
        cdp = await s.context.new_cdp_session(s.page)
        await cdp.send("Page.crash")


async def _op_navigate(pool: object, rng: random.Random) -> None:
    sessions = list(pool.iter_sessions())  # type: ignore[attr-defined]
    if sessions:
        with contextlib.suppress(Exception):
            await rng.choice(sessions).navigate("data:text/html,<h2>nav</h2>")


_OPS = {"launch": _op_launch, "close": _op_close, "crash": _op_crash, "navigate": _op_navigate}


async def _run_round(pool: object, rng: random.Random, *, kill_driver: bool) -> None:
    import anyio

    picks = rng.choices(list(_OPS), weights=[4, 2, 2, 2], k=_OPS_PER_ROUND)
    async with anyio.create_task_group() as tg:
        for name in picks:
            tg.start_soon(_OPS[name], pool, rng)
        if kill_driver:
            with contextlib.suppress(Exception):
                await pool._pw.stop()  # type: ignore[union-attr]  # the SPOF — every browser dies at once

    # Let async crash-recovery / driver-relaunch settle, then a launch to exercise
    # the driver self-heal if it died this round.
    await anyio.sleep(0.4)
    await _op_launch(pool, rng)
    await anyio.sleep(0.3)


@pytest.mark.anyio
async def test_concurrent_chaos_keeps_pool_consistent(tmp_path: object) -> None:
    pytest.importorskip("playwright")
    import anyio

    from octowright import defaults as _defaults
    from octowright import process_reaper
    from octowright.browser_pool import BrowserPool, crash_recovery, driver_relaunch
    from octowright.browser_pool import incidents as _incidents
    from octowright.browser_pool import pool as _pool

    rec = tmp_path / "rec"  # type: ignore[operator]
    rec.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setattr(_defaults, "RECORDINGS_DIR", rec)
    mp.setattr(_pool, "RECORDINGS_DIR", rec)
    crash_recovery.reset_stats()
    _incidents.reset()
    driver_relaunch.reset()

    pool = BrowserPool()
    # Warm up once: this is also the no-engine skip gate.
    try:
        await pool.launch(kind="chromium", headed=False, url="about:blank", label="warmup", ephemeral=True)
    except Exception as exc:
        if any(s in str(exc).lower() for s in _NO_ENGINE):
            with contextlib.suppress(Exception):
                await pool.shutdown()
            pytest.skip(f"live browser engine unavailable: {exc}")
        raise

    print(f"\n[chaos-monkey] seed={_SEED} rounds={_ROUNDS} ops/round={_OPS_PER_ROUND}")
    rng = random.Random(_SEED)
    try:
        for round_no in range(_ROUNDS):
            await _run_round(pool, rng, kill_driver=round_no in _DRIVER_DEATH_ROUNDS)
            # The contract: no matter what raced, the pool is internally consistent.
            assert_pool_consistent(pool)
            print(f"  round {round_no}: live={pool.active_count()} consistent")
    finally:
        with contextlib.suppress(Exception):
            await pool.shutdown()
        # Driver-deaths orphan their browsers (reparented to init); sweep them so
        # the soak leaves the host clean.
        with contextlib.suppress(Exception):
            await anyio.to_thread.run_sync(lambda: process_reaper.reap_orphan_browsers(scope="orphaned"))
        mp.undo()

    leftover = orphan_browser_pids()
    assert leftover == [], f"chaos soak leaked orphaned browsers: {leftover}"
