# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reusable "nothing leaked or corrupted" invariant check for a BrowserPool.

After any chaos/recovery operation (renderer crash, driver death, mass
close/relaunch) — or after each cycle of the memory-leak harness — these global
invariants must still hold. Bundling them in one helper means every reliability
test asserts the same consistency contract instead of re-deriving it ad hoc.

Cheap and side-effect-free. ``check_orphans`` is opt-in because it inspects
host-global process state (every ms-playwright browser on the box), which other
concurrent test pools could transiently perturb.
"""

from __future__ import annotations

from typing import Any


def orphan_browser_pids() -> list[int]:
    """Live ms-playwright browsers whose owning driver died (reparented to init /
    stale parent). Multi-daemon-safe (see ``process_reaper``); empty on a clean
    host. A non-empty result after a recovery op means a browser leaked."""
    from octowright import process_reaper

    return process_reaper.find_browser_pids("orphaned")


def assert_pool_consistent(pool: Any, *, check_orphans: bool = False) -> None:
    """Assert the pool's global consistency invariants, raising AssertionError on
    the first violation.

    Invariants:
      * ``active_count()`` agrees with ``iter_sessions()``
      * instance_ids are unique
      * ``_recently_evicted`` is within its cap (bounded eviction memory)
      * each session's active page is a member of its ``pages`` and ``page_count``
        matches (no dangling page after a crash/replace)
      * the incident and lost-session rings are within their bounds
      * (opt-in) no orphaned ms-playwright browser processes
    """
    sessions = list(pool.iter_sessions())
    ids = [s.instance_id for s in sessions]
    assert pool.active_count() == len(sessions), f"active_count={pool.active_count()} but {len(sessions)} sessions"
    assert len(ids) == len(set(ids)), f"duplicate instance_ids: {sorted(ids)}"

    evicted = getattr(pool, "_recently_evicted", {})
    cap = getattr(pool, "_RECENTLY_EVICTED_CAP", None)
    if cap is not None:
        assert len(evicted) <= cap, f"_recently_evicted holds {len(evicted)} > cap {cap}"

    for session in sessions:
        pages = getattr(session, "pages", None)
        if pages is None:
            continue
        assert session.page in pages, f"{session.instance_id}: active page not in pages"
        page_count = getattr(session, "page_count", None)
        if page_count is not None:
            assert page_count == len(pages), f"{session.instance_id}: page_count {page_count} != {len(pages)} pages"

    from octowright.browser_pool import driver_relaunch as _dr
    from octowright.browser_pool import incidents as _inc

    assert len(_inc.recent()) <= _inc._RING_SIZE, f"incidents ring exceeded its bound ({_inc._RING_SIZE})"
    assert len(_dr.recent_lost()) <= _dr._LOST_SIZE, f"lost-session ring exceeded its bound ({_dr._LOST_SIZE})"

    if check_orphans:
        orphans = orphan_browser_pids()
        assert not orphans, f"orphaned ms-playwright browsers (ppid==1): {orphans}"
