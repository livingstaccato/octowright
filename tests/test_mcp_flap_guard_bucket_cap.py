# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The limiter's own bookkeeping must be bounded, not just what it limits.

``NewSessionRateLimiter`` buckets by ``source_key`` -- the follower's
self-reported ``X-Octowright-Follower`` pid, else the TCP peer. Both are
client-chosen, and ``allow`` did ``self._events.setdefault(key, deque())``, so
every distinct key seen inside one window allocated a tracker. Emptied keys are
swept, but the sweep runs at most once per window, so the map grows unbounded
*within* it.

Scope, stated honestly: this is a memory bound on the guard, NOT a way to stop
a determined local process from side-stepping the rate limit by rotating the
header. It cannot be -- ``/mcp`` requires the capability token from the 0600
lockfile, so anything able to reach it is a same-user process that already has
RCE-equivalent access, which CLAUDE.md names as the trust boundary. What the
guard actually defends against is a BUGGY or OLD follower storming the leader,
and that one reports a stable pid and buckets correctly. The cap just means a
key flood cannot turn the defense into the leak it was built to prevent.

Refusing at the cap (rather than evicting to make room) is deliberate: being at
the cap means a flood is already underway, which is exactly when the guard is
supposed to shed load.
"""

from __future__ import annotations

from octowright.http import mcp_flap_guard as guard


def _limiter(max_sources: int) -> guard.NewSessionRateLimiter:
    return guard.NewSessionRateLimiter(max_events=5, window_seconds=10.0, max_sources=max_sources)


def test_distinct_keys_do_not_grow_the_map_without_bound() -> None:
    limiter = _limiter(4)

    for i in range(500):
        limiter.allow(f"spoofed-{i}", now=1.0)

    assert len(limiter._events) <= 4


def test_a_new_key_is_refused_once_the_map_is_full() -> None:
    limiter = _limiter(2)

    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("b", now=1.0) is True
    assert limiter.allow("c", now=1.0) is False, "a new bucket past the cap must not be allocated"
    assert "c" not in limiter._events


def test_an_already_tracked_key_keeps_working_at_the_cap() -> None:
    """A legit follower that got a bucket before the flood must not be starved
    out of it -- its own window still governs it."""
    limiter = _limiter(2)
    limiter.allow("legit", now=1.0)
    limiter.allow("other", now=1.0)

    for _ in range(4):  # 1 (above) + 4 == max_events
        assert limiter.allow("legit", now=1.0) is True
    assert limiter.allow("legit", now=1.0) is False  # its own rate limit, not the cap


def test_capacity_is_reclaimed_once_a_window_passes() -> None:
    """The cap must not wedge the limiter shut forever after one flood."""
    limiter = _limiter(2)
    limiter.allow("old-a", now=1.0)
    limiter.allow("old-b", now=1.0)
    assert limiter.allow("fresh", now=1.0) is False

    later = 1.0 + 10.0 + 0.1
    assert limiter.allow("fresh", now=later) is True


def test_the_default_cap_is_on() -> None:
    limiter = guard.NewSessionRateLimiter(max_events=5, window_seconds=10.0)
    assert limiter._max_sources == guard._MAX_TRACKED_SOURCES
    assert guard._MAX_TRACKED_SOURCES > 0
