# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side defenses against a follower reconnect/session storm.

Every other storm defense in octowright is *follower*-side (the flap-guard in
``proxy_runtime``, the 180s recovery window) — so it only helps once every
connected client runs a fixed version. But the leader serves one shared ``/mcp``
transport to *all* clients, old and new, and an old/buggy follower that churns
StreamableHTTP sessions (each forwarded RPC opening a fresh session instead of
reusing one) can pile up per-session server tasks + transports until the leader
is at many GB RSS and real tool calls are starved — observed live at 18GB after
2 days. The leader therefore cannot trust followers to behave; it needs its own
protection, deployable with a single daemon restart and independent of follower
version. Two pieces, both on by default:

1. **New-session rate limit** (this module's middleware): a session-creating
   request (POST to ``/mcp`` with no ``Mcp-Session-Id`` header) beyond
   ``max`` per ``window`` seconds *per source* is rejected with ``429`` +
   ``Retry-After``. Legit clients create ~1 session and reuse it, so they never
   approach the limit; a storming follower is throttled instead of taking down
   the shared leader. Source = the ``X-Octowright-Follower`` header a current
   follower sends (its pid) so each gets its own bucket; old followers omit it
   and share the ``anonymous`` bucket — which is exactly the storm, collectively
   throttled.

2. **Session-table cap + LRU evict** (``select_eviction_victims`` here, driven
   by a housekeeping job): when the manager's live session table exceeds
   ``max_sessions``, the most-idle sessions are evicted back down to the cap.
   Activity-agnostic, so it never false-reaps a quietly-waiting live session the
   way a fixed idle timer would — it only sheds genuine over-capacity, and sheds
   sessions that have gone silent (absent from the activity tracker) before any
   recently-active one.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Final

from provide.telemetry import get_logger

from octowright._tracing import counter

log = get_logger(__name__)

# Tokens that turn a knob off, matching defaults.py's inlined disable checks.
_FALSEY: Final[frozenset[str]] = frozenset({"", "0", "off", "false", "no", "never", "none", "disabled"})


def _is_falsey(raw: str) -> bool:
    return raw.strip().lower() in _FALSEY


# On by default. A real host runs a handful of MCP clients, each holding one
# reused session, so 256 is orders of magnitude above legit use and only ever
# reached by pathological accumulation (a storm). ~54KB/session → 256 is ~14MB.
_MAX_SESSIONS_DEFAULT: Final[int] = 256
# A legit client creates a session only on connect/reconnect, so 10 per 10s per
# source is generous; a storm creates hundreds/sec and is cut to the window rate.
_NEW_SESSION_MAX_DEFAULT: Final[int] = 10
_NEW_SESSION_WINDOW_DEFAULT: Final[float] = 10.0

_ANONYMOUS_SOURCE: Final[str] = "anonymous"
_FOLLOWER_HEADER: Final[bytes] = b"x-octowright-follower"
_SESSION_ID_HEADER: Final[bytes] = b"mcp-session-id"

_THROTTLED = counter(
    "octowright_mcp_new_session_throttled_total",
    description="New-session /mcp requests rejected (429) by the leader-side per-source rate limit.",
)


def _parse_positive_int(raw: str | None, default: int) -> int | None:
    """Parse a positive-int knob. Falsey token / non-positive / unparsable → None
    (disabled). Unset → ``default`` (on by default)."""
    if raw is None:
        return default
    if _is_falsey(raw):
        return None
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else None


def _parse_positive_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def mcp_max_sessions(raw: str | None = None) -> int | None:
    """Cap on concurrent leader ``/mcp`` sessions before LRU eviction kicks in.
    ``OCTOWRIGHT_MCP_MAX_SESSIONS``; default 256; falsey/non-positive disables."""
    import os

    return _parse_positive_int(
        os.environ.get("OCTOWRIGHT_MCP_MAX_SESSIONS") if raw is None else raw, _MAX_SESSIONS_DEFAULT
    )


def mcp_new_session_rate() -> tuple[int, float] | None:
    """Return ``(max, window_seconds)`` for the per-source new-session limit, or
    ``None`` when disabled. ``OCTOWRIGHT_MCP_NEW_SESSION_MAX`` (default 10) and
    ``OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS`` (default 10)."""
    import os

    max_n = _parse_positive_int(os.environ.get("OCTOWRIGHT_MCP_NEW_SESSION_MAX"), _NEW_SESSION_MAX_DEFAULT)
    if max_n is None:
        return None
    window = _parse_positive_float(
        os.environ.get("OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS"), _NEW_SESSION_WINDOW_DEFAULT
    )
    return max_n, window


class NewSessionRateLimiter:
    """Per-source sliding-window limiter. Thread-safe under one lock."""

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max = max_events
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._last_prune = 0.0
        self._lock = Lock()

    def allow(self, key: str, now: float) -> bool:
        """Record + admit an event for ``key`` at ``now``; False if over the
        window rate (the event is NOT recorded when refused, so a throttled
        source that backs off recovers cleanly). Sweeps emptied keys at most
        once per window so per-source state stays bounded as followers churn."""
        cutoff = now - self._window
        with self._lock:
            if now - self._last_prune >= self._window:
                self._prune_locked(cutoff)
                self._last_prune = now
            dq = self._events.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                if not dq:  # empty deque left by pruning — drop the key
                    self._events.pop(key, None)
                return False
            dq.append(now)
            return True

    def prune(self, now: float) -> None:
        """Drop keys whose window has fully emptied, bounding memory."""
        with self._lock:
            self._prune_locked(now - self._window)

    def _prune_locked(self, cutoff: float) -> None:
        for key in list(self._events):
            dq = self._events[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                del self._events[key]


def is_new_session_request(scope: dict) -> bool:
    """A session-creating request: POST to ``/mcp`` with no ``Mcp-Session-Id``.
    Continuations carry the id; DELETE/GET without an id aren't creations."""
    if scope.get("method", "").upper() != "POST":
        return False
    return all(name.lower() != _SESSION_ID_HEADER for name, _value in scope.get("headers", []))


def source_key(scope: dict) -> str:
    """Rate-limit bucket: the follower's self-reported id, else ``anonymous``
    (shared by every follower that doesn't send the header — i.e. old ones)."""
    for name, value in scope.get("headers", []):
        if name.lower() == _FOLLOWER_HEADER:
            try:
                decoded = value.decode("ascii").strip()
            except UnicodeDecodeError:
                return _ANONYMOUS_SOURCE
            return decoded or _ANONYMOUS_SOURCE
    return _ANONYMOUS_SOURCE


def select_eviction_victims(
    instance_ids: list[str],
    recent_ids: set[str],
    last_seen: dict[str, float],
    over: int,
) -> list[str]:
    """Pick ``over`` sessions to evict, most-abandoned first.

    ``instance_ids`` is the manager's table in creation order. Sessions absent
    from ``recent_ids`` (no activity within the tracker TTL) are shed first,
    oldest-created first; only if that isn't enough are recently-active sessions
    evicted, least-recently-seen first. So a quietly-waiting live session is the
    last thing touched, and only when the table is genuinely over the cap.
    """
    if over <= 0:
        return []
    abandoned = [s for s in instance_ids if s not in recent_ids]
    if len(abandoned) >= over:
        return abandoned[:over]
    victims = list(abandoned)
    remaining = over - len(victims)
    recents = [s for s in instance_ids if s in recent_ids]
    recents.sort(key=lambda s: last_seen.get(s, 0.0))
    victims.extend(recents[:remaining])
    return victims


class McpNewSessionRateLimitMiddleware:
    """Reject over-rate new-session ``/mcp`` requests with ``429`` before they
    reach the transport (so a throttled creation never registers a session)."""

    def __init__(self, app, limiter: NewSessionRateLimiter, *, retry_after: float) -> None:
        self.app = app
        self._limiter = limiter
        self._retry_after = max(1, round(retry_after))

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http"
            and is_new_session_request(scope)
            and not self._limiter.allow(source_key(scope), time.monotonic())
        ):
            _THROTTLED.add(1)
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send) -> None:
        body = b'{"error":"new-session rate limit exceeded; retry after backoff"}'
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(self._retry_after).encode("ascii")),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
