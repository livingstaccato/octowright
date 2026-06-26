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
quiescent point (pool empty) before/after K cycles, and asserts the heap growth
stays within an empirically-derived band. It also asserts the pool consistency
invariants after every cycle. On failure it prints the top allocation growers so
the leak is actionable, not just "number too big".

Marked ``live_browser``; skipped where no engine is installed. Also runnable as a
standalone investigation harness (bump ``_CYCLES`` and read the printed report).
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from tests._pool_invariants import assert_pool_consistent

pytestmark = pytest.mark.live_browser

# Warmup cycles excluded from the measurement window: the first launches fill
# one-time import caches, lru_caches, and lazy singletons that are NOT leaks.
_WARMUP = 3
# Measured cycles. Enough to make a real per-cycle leak visible above noise while
# keeping the test fast (~6s). A leak grows ~linearly with cycles while the noise
# floor stays roughly constant, so bump this when using the file as a standalone
# investigation harness to amplify a subtle leak.
_CYCLES = 20
# Heap-growth band over the measured window. Empirically derived: an observed-clean
# run grows ~19KB / 15 cycles (~1.3KB/cycle of tracemalloc frame/string noise, not
# accumulating objects), so this band has >20x headroom against that noise while
# still tripping on a real accumulating leak — a leaked session/page or a growing
# collection runs hundreds of KB to MB over _CYCLES. For subtler leaks, raise
# _CYCLES and read the printed top-growers report.
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


def _top_growers(before: tracemalloc.Snapshot, after: tracemalloc.Snapshot, n: int = 8) -> tuple[int, str]:
    diff = after.compare_to(before, "lineno")
    grew = sum(st.size_diff for st in diff if st.size_diff > 0)
    top = "\n".join(f"  +{st.size_diff / 1024:7.1f}KB  {st}" for st in sorted(diff, key=lambda s: -s.size_diff)[:n])
    return grew, top


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

        gc.collect()
        tracemalloc.start()
        before = tracemalloc.take_snapshot()

        for _ in range(_CYCLES):
            await _cycle(pool)
            assert_pool_consistent(pool)  # invariants hold at every quiescent point

        gc.collect()
        after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        assert pool.active_count() == 0, "pool not quiescent after the cycle loop"
        grew, top = _top_growers(before, after)
        print(f"\n[leak-harness] heap grew {grew / 1024:.1f}KB over {_CYCLES} cycles\nTop growers:\n{top}")
        assert grew < _MAX_HEAP_GROWTH_BYTES, (
            f"pool heap grew {grew / 1024:.1f}KB over {_CYCLES} launch/close cycles "
            f"(band {_MAX_HEAP_GROWTH_BYTES / 1024:.0f}KB) — likely a leak:\n{top}"
        )
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            await pool.shutdown()
