# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Idempotency wait window vs. the heartbeat ceiling, and abandoned-entry reclaim.

Two review findings against the batch-B idempotency rework:

1. The in-progress wait window (95s) was far SHORTER than the longest call the
   progress heartbeat is designed to keep alive (``HEARTBEAT_MAX_SECONDS``,
   600s). A reconnect resend of a legitimately slow but still-running call
   therefore failed hard with ``IdempotencyOutcomeUnknownError`` even though the
   producer went on to succeed — and the error text invites the agent to
   re-issue a mutation, which is exactly the double-execute this module exists
   to prevent. The window must cover any call the heartbeat would sustain.

2. Nothing reclaims an in-progress entry whose producer never resolves:
   ``_evict_expired_locked`` only evicts done entries and ``_enforce_bound_locked``
   deliberately skips in-progress ones. A handler wedged in an uncancellable
   await therefore pins its slot forever, so every resend of that key returns
   "unknown" with no recovery path and the cache grows past its bound.
"""

from __future__ import annotations

import pytest

from octowright import defaults
from octowright.server import _idempotency
from octowright.server._heartbeat import HEARTBEAT_MAX_SECONDS


def test_wait_window_covers_the_heartbeat_ceiling() -> None:
    """A call the heartbeat keeps alive must not out-live the resend's wait."""
    assert defaults.IDEMPOTENCY_INPROGRESS_WAIT_SECONDS > HEARTBEAT_MAX_SECONDS, (
        "a resend gives up before the heartbeat stops sustaining the producer, "
        "so a still-running call is reported as unknown-outcome"
    )


def test_abandoned_in_progress_entry_is_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-progress slot older than the abandon threshold must be evictable."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["now"])
    _idempotency._cache.clear()

    entry = _idempotency._Entry(owner="wedged")
    _idempotency._cache["k"] = entry  # never completes

    # Still within the threshold → kept (a slow producer must not lose its slot).
    clock["now"] += _idempotency._abandon_threshold_seconds() / 2
    _idempotency._evict_expired_locked()
    assert "k" in _idempotency._cache

    # Past it → the producer is definitively gone; reclaim the slot.
    clock["now"] += _idempotency._abandon_threshold_seconds()
    _idempotency._evict_expired_locked()
    assert "k" not in _idempotency._cache, "wedged in-progress entry pinned the cache forever"

    _idempotency._cache.clear()


def test_done_entries_still_evict_on_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the existing done-entry TTL path is unchanged."""
    clock = {"now": 500.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["now"])
    _idempotency._cache.clear()

    entry = _idempotency._Entry(owner="done")
    entry.done = True
    entry.done_at = clock["now"]
    _idempotency._cache["d"] = entry

    clock["now"] += defaults.IDEMPOTENCY_TTL_SECONDS + 1
    _idempotency._evict_expired_locked()
    assert "d" not in _idempotency._cache

    _idempotency._cache.clear()
