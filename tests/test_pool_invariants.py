# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for the reusable pool-consistency invariant helper.

``assert_pool_consistent`` is the "nothing leaked or corrupted" check meant to
run after any chaos/recovery op or each memory-leak cycle. These tests pin every
invariant it enforces using a fake pool (no real browsers needed)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests._pool_invariants import assert_pool_consistent, orphan_browser_pids


def _session(instance_id: str, *, pages: list[Any] | None = None, page_count: int | None = None) -> SimpleNamespace:
    page = object()
    pgs = pages if pages is not None else [page]
    return SimpleNamespace(
        instance_id=instance_id,
        page=page if pages is None else (pgs[0] if pgs else page),
        pages=pgs,
        page_count=page_count if page_count is not None else len(pgs),
    )


class _FakePool:
    _RECENTLY_EVICTED_CAP = 64

    def __init__(self, sessions: list[SimpleNamespace], *, evicted: int = 0) -> None:
        self._sessions = {s.instance_id: s for s in sessions}
        self._recently_evicted = {f"ev{i}": False for i in range(evicted)}

    def iter_sessions(self) -> tuple[SimpleNamespace, ...]:
        return tuple(self._sessions.values())

    def active_count(self) -> int:
        return len(self._sessions)


def test_consistent_pool_passes() -> None:
    pool = _FakePool([_session("a"), _session("b")])
    assert_pool_consistent(pool)  # no raise


def test_active_count_mismatch_raises() -> None:
    pool = _FakePool([_session("a")])
    pool.active_count = lambda: 5  # type: ignore[method-assign]
    with pytest.raises(AssertionError, match="active_count"):
        assert_pool_consistent(pool)


def test_duplicate_instance_ids_raise() -> None:
    pool = _FakePool([_session("a")])
    pool._sessions["a2"] = _session("a")  # same instance_id, different key
    with pytest.raises(AssertionError, match="duplicate instance_ids"):
        assert_pool_consistent(pool)


def test_recently_evicted_over_cap_raises() -> None:
    pool = _FakePool([_session("a")], evicted=65)  # cap is 64
    with pytest.raises(AssertionError, match="_recently_evicted"):
        assert_pool_consistent(pool)


def test_active_page_not_in_pages_raises() -> None:
    s = _session("a")
    s.page = object()  # active page no longer a member of pages
    pool = _FakePool([s])
    with pytest.raises(AssertionError, match="active page not in pages"):
        assert_pool_consistent(pool)


def test_page_count_mismatch_raises() -> None:
    p = object()
    s = _session("a", pages=[p, object()], page_count=1)  # 2 pages, stale count
    s.page = p
    pool = _FakePool([s])
    with pytest.raises(AssertionError, match="page_count"):
        assert_pool_consistent(pool)


def test_incidents_ring_overflow_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import incidents as _inc

    monkeypatch.setattr(_inc, "_RING_SIZE", 2)
    monkeypatch.setattr(_inc, "recent", lambda **_k: [{} for _ in range(3)])
    with pytest.raises(AssertionError, match="incidents ring"):
        assert_pool_consistent(_FakePool([_session("a")]))


def test_lost_ring_overflow_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import driver_relaunch as _dr

    monkeypatch.setattr(_dr, "_LOST_SIZE", 1)
    monkeypatch.setattr(_dr, "recent_lost", lambda **_k: [{}, {}])
    with pytest.raises(AssertionError, match="lost-session ring"):
        assert_pool_consistent(_FakePool([_session("a")]))


def test_check_orphans_flags_orphaned_browsers(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests._pool_invariants as _pi

    monkeypatch.setattr(_pi, "orphan_browser_pids", lambda: [4242])
    with pytest.raises(AssertionError, match="orphaned ms-playwright"):
        assert_pool_consistent(_FakePool([_session("a")]), check_orphans=True)


def test_check_orphans_passes_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests._pool_invariants as _pi

    monkeypatch.setattr(_pi, "orphan_browser_pids", lambda: [])
    assert_pool_consistent(_FakePool([_session("a")]), check_orphans=True)  # no raise


def test_orphan_browser_pids_delegates_to_reaper(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import process_reaper

    monkeypatch.setattr(process_reaper, "find_browser_pids", lambda scope: [1] if scope == "orphaned" else [])
    assert orphan_browser_pids() == [1]
