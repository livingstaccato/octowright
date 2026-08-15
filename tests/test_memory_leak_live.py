# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Closed-loop memory-leak harness for the browser pool (live).

The rigorous way to find a leak in a long-lived daemon is NOT to eyeball RSS —
it's to drive a CLOSED loop (launch -> navigate -> close, back to an empty pool)
many times and measure the Python heap at the SAME quiescent phase each cycle.
Per-cycle noise cancels; a real leak accumulates monotonically.

This drives a REAL headless pool, diffs ``tracemalloc`` snapshots taken at the
quiescent point (pool empty) before/after K cycles, and asserts the NET heap
growth stays within an empirically-derived band. It also asserts the pool
consistency invariants after every cycle. On failure it prints the top
allocation growers so the leak is actionable, not just "number too big".

Two properties keep it honest rather than merely quiet:

* **Net, not gross.** Growth sums every ``size_diff``, including negatives, so
  transient churn that frees as much as it allocates cancels out. Summing only
  the positive diffs measures allocation traffic, not retained memory, and
  inflates on a busy runner with nothing actually leaked.
* **Confirm before failing.** An over-band window is re-measured, and the test
  fails only if the SECOND window is also over. A leak is per-cycle so it
  reproduces every window; a late-warming cache or a scheduling spike does not.
  This removes the flake without widening the band — a band loose enough never
  to false-positive would also be loose enough to miss a real leak.

Marked ``live_browser``; skipped where no engine is installed. Also runnable as a
standalone investigation harness (bump ``_CYCLES`` and read the printed report).

Also marked ``memory_isolated``: the assertion is a process-wide ``tracemalloc``
heap diff, so running it interleaved with the other ~5000 tests in one pytest
process contaminates the measurement — a full-suite CI run consistently showed
700KB-2MB "growth" here (band: 500KB) while every standalone run of this file
alone passed clean at the ~20KB the band was calibrated against. That is not
this test's confirm-before-failing mechanism catching a real leak reproducing
across two windows; it is thousands of unrelated tests' retained state (import
caches, log-capture buffers, etc.) still live in the same process when this
file's window opens. CI runs ``memory_isolated`` tests in their own pytest
invocation (see ``ci/run_integration_and_main.sh``) for exactly this reason.
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from tests._pool_invariants import assert_pool_consistent

pytestmark = [pytest.mark.live_browser, pytest.mark.memory_isolated]

# Warmup cycles excluded from the measurement window: the first launches fill
# one-time import caches, lru_caches, and lazy singletons that are NOT leaks.
_WARMUP = 3
# Measured cycles. Enough to make a real per-cycle leak visible above noise while
# keeping the test fast (~6s). A leak grows ~linearly with cycles while the noise
# floor stays roughly constant, so bump this when using the file as a standalone
# investigation harness to amplify a subtle leak.
_CYCLES = 20
# NET heap-growth band over one measured window. Empirically derived: observed-clean
# runs grow ~22KB / 20 cycles (~1.1KB/cycle of tracemalloc frame/string noise, not
# accumulating objects), so this band has >20x headroom against that noise while
# still tripping on a real accumulating leak — a leaked session/page or a growing
# collection runs hundreds of KB to MB over _CYCLES. For subtler leaks, raise
# _CYCLES and read the printed top-growers report.
#
# The band is deliberately NOT widened to absorb CI noise; a band loose enough to
# never false-positive is also loose enough to miss a real leak. Outliers are
# rejected by re-measuring instead (see the confirmation window in the test).
_MAX_HEAP_GROWTH_BYTES = 500_000

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")


def _skip_if_no_engine(exc: Exception) -> None:
    if any(s in str(exc).lower() for s in _NO_ENGINE):
        pytest.skip(f"live browser engine unavailable: {exc}")
    raise exc


async def _cycle(pool: object) -> None:
    """One launch -> navigate -> close round-trip, ending with the pool empty."""
    try:
        result = await pool.launch(  # type: ignore[attr-defined]
            kind="chromium", headed=False, url="data:text/html,<h1>before</h1>", label="leak", ephemeral=True
        )
    except Exception as exc:
        _skip_if_no_engine(exc)
        raise
    iid = result["instance_id"]
    await pool.get(iid).navigate("data:text/html,<h2>after</h2>")  # type: ignore[attr-defined]
    await pool.close(iid)  # type: ignore[attr-defined]


def _growth(before: tracemalloc.Snapshot, after: tracemalloc.Snapshot, n: int = 8) -> tuple[int, int, str]:
    """Return ``(net, gross, top_growers)`` for one measurement window.

    ``net`` sums EVERY size_diff, so a site that freed as much as another
    allocated cancels out — that is what "retained memory" means and it is the
    leak signal we assert on. ``gross`` sums only the positive diffs; it is
    useful context in the failure report but must never be the assertion,
    because transient churn (a dict resize, a cache turning over) inflates it
    without a single byte being retained.
    """
    diff = after.compare_to(before, "lineno")
    net = sum(st.size_diff for st in diff)
    gross = sum(st.size_diff for st in diff if st.size_diff > 0)
    top = "\n".join(f"  +{st.size_diff / 1024:7.1f}KB  {st}" for st in sorted(diff, key=lambda s: -s.size_diff)[:n])
    return net, gross, top


async def _measure_window(pool: object, cycles: int) -> tuple[int, int, str]:
    """Run ``cycles`` closed loops and report heap growth across them.

    Snapshots are taken at the same quiescent phase (pool empty) on both ends,
    with a gc pass before each so pending garbage is not counted as growth.
    """
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(cycles):
        await _cycle(pool)
        assert_pool_consistent(pool)  # invariants hold at every quiescent point
    gc.collect()
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    return _growth(before, after)


async def test_launch_close_cycle_does_not_leak(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool, crash_recovery, driver_relaunch
    from octowright.browser_pool import incidents as _incidents
    from octowright.browser_pool import pool as _pool

    rec = tmp_path / "rec"  # type: ignore[operator]
    rec.mkdir()
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    crash_recovery.reset_stats()
    _incidents.reset()
    driver_relaunch.reset()

    pool = BrowserPool()
    try:
        for _ in range(_WARMUP):
            await _cycle(pool)

        net, gross, top = await _measure_window(pool, _CYCLES)
        assert pool.active_count() == 0, "pool not quiescent after the cycle loop"
        print(
            f"\n[leak-harness] window 1: net {net / 1024:.1f}KB (gross +{gross / 1024:.1f}KB) "
            f"over {_CYCLES} cycles\nTop growers:\n{top}"
        )

        if net >= _MAX_HEAP_GROWTH_BYTES:
            # Confirm before failing. A real leak accumulates in EVERY window —
            # it is per-cycle by definition — whereas a one-time lazy allocation
            # that warmed late (slow/cold CI runner) or a scheduling-noise spike
            # does not reproduce. Observed live: this test reported 3.9MB once on
            # macos arm64 CI and passed on rerun, while three back-to-back local
            # windows sat at ~22KB each. Confirming costs nothing on a clean run
            # (this branch is not taken) and, unlike widening the band, it does
            # not blunt the detector: sustained growth still fails, twice over.
            net2, gross2, top2 = await _measure_window(pool, _CYCLES)
            assert pool.active_count() == 0, "pool not quiescent after the confirmation loop"
            print(
                f"\n[leak-harness] window 2 (confirmation): net {net2 / 1024:.1f}KB "
                f"(gross +{gross2 / 1024:.1f}KB)\nTop growers:\n{top2}"
            )
            assert net2 < _MAX_HEAP_GROWTH_BYTES, (
                f"pool heap grew {net / 1024:.1f}KB then {net2 / 1024:.1f}KB (net) over two "
                f"independent windows of {_CYCLES} launch/close cycles (band "
                f"{_MAX_HEAP_GROWTH_BYTES / 1024:.0f}KB) — sustained growth, a real leak:\n{top2}"
            )
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            await pool.shutdown()
