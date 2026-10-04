# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The limiter's own bookkeeping must be bounded, not just what it limits.

``NewSessionRateLimiter`` buckets by ``source_key`` -- the follower's
self-reported ``X-Octowright-Follower`` pid, else one shared ``anonymous``
bucket. The header is client-chosen, and ``allow`` did ``self._events.setdefault(key, deque())``, so
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

At the cap the limiter makes room by dropping the least-recently-used bucket
rather than refusing the unseen key. Refusing used to look like load shedding,
but what it shed was every follower that had not yet been seen: once the map
was full, a legitimate follower connecting (or reconnecting with a new pid
after its client restarted) got a 429 on its first session for as long as the
flood lasted, while the flood's own keys kept their buckets. Evicting the
stalest bucket keeps the bound and admits the newcomer; a flood's keys, each
touched once, are what ages out first.
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


def test_a_new_key_at_the_cap_is_admitted_by_evicting_the_stalest_bucket() -> None:
    limiter = _limiter(2)

    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("b", now=2.0) is True
    assert limiter.allow("c", now=3.0) is True, "an unseen follower must not be refused because the map is full"
    assert set(limiter._events) == {"b", "c"}, "the least-recently-used bucket makes room"


def test_a_key_flood_does_not_lock_out_a_legitimate_newcomer() -> None:
    limiter = _limiter(8)
    for i in range(100):
        limiter.allow(f"spoofed-{i}", now=1.0 + i * 0.001)

    assert limiter.allow("legit-follower", now=2.0) is True
    assert len(limiter._events) <= 8


def test_a_recently_active_bucket_survives_a_flood() -> None:
    """Eviction is least-recently-USED: a follower still creating sessions keeps
    its bucket (and so its own rate limit) while a flood churns past it."""
    limiter = _limiter(4)
    for i in range(50):
        if i % 2 == 0:
            limiter.allow("legit", now=1.0 + i * 0.01)
        limiter.allow(f"spoofed-{i}", now=1.0 + i * 0.01)
    assert "legit" in limiter._events


def test_an_already_tracked_key_keeps_working_at_the_cap() -> None:
    """A legit follower that got a bucket before the flood must not be starved
    out of it -- its own window still governs it."""
    limiter = _limiter(2)
    limiter.allow("legit", now=1.0)
    limiter.allow("other", now=1.0)

    for _ in range(4):  # 1 (above) + 4 == max_events
        assert limiter.allow("legit", now=1.0) is True
    assert limiter.allow("legit", now=1.0) is False  # its own rate limit, not the cap


def test_the_default_cap_is_on() -> None:
    limiter = guard.NewSessionRateLimiter(max_events=5, window_seconds=10.0)
    assert limiter._max_sources == guard._MAX_TRACKED_SOURCES
    assert guard._MAX_TRACKED_SOURCES > 0
