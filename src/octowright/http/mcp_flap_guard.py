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
   follower sends (its pid) so each gets its own bucket. A request that omits
   the header (an old follower, or a direct HTTP-MCP client that skips the
   follower handshake) shares the one ``anonymous`` bucket — which is exactly
   the storm, collectively throttled. It is deliberately NOT keyed by TCP peer:
   every peer is loopback, so the only thing distinguishing two headerless
   requests is the ephemeral port, and a storm that opens a fresh connection
   per session would land every request in a fresh, empty bucket and never be
   throttled at all.

2. **Session-table cap + LRU evict** (``select_eviction_victims`` here, driven
   by a housekeeping job): when the manager's live session table exceeds
   ``max_sessions``, the most-idle sessions are evicted back down to the cap.
   Activity-agnostic, so it never false-reaps a quietly-waiting live session the
   way a fixed idle timer would — it only sheds genuine over-capacity, and sheds
   sessions that have gone silent (absent from the activity tracker) before any
   recently-active one.
"""

from __future__ import annotations

import json
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

# Ceiling on distinct rate-limit buckets held at once. `source_key` derives the
# bucket from client-chosen input (the follower header), and `allow` allocates
# a tracker for every distinct key it sees; the sweep that reclaims emptied
# ones runs at most once per window, so without a ceiling the map could grow
# unbounded WITHIN a window — a leak inside the guard built to stop a leak.
# At the ceiling the least-recently-used bucket is dropped to make room; an
# unseen key is never refused for it. Refusing shed exactly the wrong traffic:
# every follower not yet seen (a client reconnecting gets a new pid) was
# 429'd for as long as a key flood lasted, while the flood kept its buckets.
# This does not (and cannot) stop a local process from side-stepping the rate
# limit by rotating the header: `/mcp` needs the capability token out of the
# 0600 lockfile, so anything reaching it is same-user and already trusted at
# the RCE-equivalent level. What the guard defends against is a buggy/old
# follower, which reports a stable pid (or none) and buckets correctly. 4096
# buckets is far past any real client population and costs well under a MB.
_MAX_TRACKED_SOURCES: Final[int] = 4096

# Sustained-throttle refusal: a source that keeps tripping the new-session
# limit across whole windows is never going to settle (the observed case was a
# follower too old for the leader's handshake — every reconnect trips the
# guard, for days). Pacing that with 429s forever burns the leader's time for
# nothing, so once a source's rejections span ~5 windows with at least this
# many rejections, the guard shifts to a flat 410 telling it what the 429
# never could: this follower cannot work with this leader. Off via the env
# knob below; a source whose rejections stop for the span is forgiven.
_REFUSAL_MIN_REJECTIONS: Final[int] = 6
_REFUSAL_SPAN_FACTOR: Final[float] = 5.0

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


def mcp_sustained_refusal_span() -> float | None:
    """Seconds of sustained throttling (span of a source's rejection window)
    after which the guard answers 410 Gone instead of 429. Default 5 windows
    (~50s at the default window); a falsey/non-positive value disables the
    refusal tier, falling back to pure 429 pacing."""
    import os

    raw = os.environ.get("OCTOWRIGHT_MCP_SUSTAINED_REFUSAL_SECONDS")
    if raw is not None and _is_falsey(raw):
        return None
    try:
        value = float(raw) if raw is not None else _REFUSAL_SPAN_FACTOR * _NEW_SESSION_WINDOW_DEFAULT
    except ValueError:
        value = _REFUSAL_SPAN_FACTOR * _NEW_SESSION_WINDOW_DEFAULT
    return value if value > 0 else None


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

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        max_sources: int = _MAX_TRACKED_SOURCES,
    ) -> None:
        self._max = max_events
        self._window = window_seconds
        self._max_sources = max_sources
        self._events: dict[str, deque[float]] = {}
        self._last_prune = 0.0
        self._lock = Lock()

    def allow(self, key: str, now: float) -> bool:
        """Record + admit an event for ``key`` at ``now``; False if over the
        window rate (the event is NOT recorded when refused, so a throttled
        source that backs off recovers cleanly). Sweeps emptied keys at most
        once per window so per-source state stays bounded as followers churn.
        Once `max_sources` buckets are live, an unseen key evicts the
        least-recently-used bucket instead of being refused, so a key flood
        cannot lock a legitimate newcomer out. The map is kept in use order
        (a key is moved to the end on every call), so the first key is the
        least recently used."""
        cutoff = now - self._window
        with self._lock:
            if now - self._last_prune >= self._window:
                self._prune_locked(cutoff)
                self._last_prune = now
            dq = self._events.pop(key, None)
            if dq is None:
                dq = deque()
                if len(self._events) >= self._max_sources:
                    self._prune_locked(cutoff)
                while len(self._events) >= self._max_sources:
                    del self._events[next(iter(self._events))]
            self._events[key] = dq
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                if not dq:  # max_events == 0: nothing to remember for the key
                    self._events.pop(key, None)
                return False
            dq.append(now)
            return True

    def prune(self, now: float) -> None:
        """Drop keys whose window has fully emptied, bounding memory."""
        with self._lock:
            self._prune_locked(now - self._window)

    def in_window(self, key: str, now: float) -> int:
        """Admitted events for ``key`` inside the current window. Read-only
        (no re-ordering): advisory input for the throttle log line's request
        count, so it must not perturb the limiter it's describing."""
        cutoff = now - self._window
        with self._lock:
            dq = self._events.get(key)
            if dq is None:
                return 0
            return sum(1 for ts in dq if ts >= cutoff)

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
    """Rate-limit bucket: the follower's self-reported id when present and
    valid, else the one shared ``anonymous`` bucket (see the module docstring
    for why a headerless request is not keyed by its TCP peer)."""
    for name, value in scope.get("headers", []):
        if name.lower() == _FOLLOWER_HEADER:
            try:
                decoded = value.decode("ascii").strip()
            except UnicodeDecodeError:
                decoded = ""
            if decoded:
                return decoded
            break
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
    """Reject over-rate new-session ``/mcp`` requests with ``429`` (or ``410``
    once throttling proves sustained) before they reach the transport, so a
    throttled creation never registers a session."""

    def __init__(
        self,
        app,
        limiter: NewSessionRateLimiter,
        *,
        retry_after: float,
        refusal_span: float | None = None,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._retry_after = max(1, round(retry_after))
        self._refusal_span = refusal_span
        # Bookkeeping for ONE log line per throttled source per window:
        # rejections since that source's last line, and when its line went
        # out. Logging every rejection is itself the storm — a looping
        # follower hits this middleware many times a second, and the
        # ``_THROTTLED`` counter is a no-op unless telemetry is enabled, so
        # without this line a throttled leader says nothing anywhere.
        # Bounded the same way the limiter bounds its buckets.
        self._throttled_since_log: dict[str, int] = {}
        self._last_log_at: dict[str, float] = {}
        # Sustained tier (410): per-source rejection timestamps, pruned to the
        # refusal span so a source that goes quiet for the span is forgiven.
        self._rejections: dict[str, deque[float]] = {}
        self._refusal_announced: set[str] = set()
        self._refusal_lock = Lock()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and is_new_session_request(scope):
            key = source_key(scope)
            now = time.monotonic()
            if not self._limiter.allow(key, now):
                _THROTTLED.add(1)
                self._note_throttled(key, now)
                await self._reject(send, key, gone=self._record_refusal(key, now))
                return
        await self.app(scope, receive, send)

    def _record_refusal(self, key: str, now: float) -> bool:
        """Whether this rejection crosses into the sustained 410 tier.

        A source whose rejections span the whole refusal window with at least
        ``_REFUSAL_MIN_REJECTIONS`` events is never going to settle (a
        follower too old for the leader's handshake trips the limit on every
        reconnect), so pacing stops and the answer says so instead. A source
        quiet for the span prunes empty and is forgiven.
        """
        if self._refusal_span is None:
            return False
        with self._refusal_lock:
            dq = self._rejections.setdefault(key, deque())
            if not dq:
                self._refusal_announced.discard(key)
            dq.append(now)
            cutoff = now - self._refusal_span
            while dq and dq[0] < cutoff:
                dq.popleft()
            sustained = len(dq) >= _REFUSAL_MIN_REJECTIONS and dq[-1] - dq[0] >= self._refusal_span
            rejections = len(dq)
            if len(self._rejections) > _MAX_TRACKED_SOURCES:
                # Keys are client-chosen, so the map gets the limiter's own bound.
                for stale_key in list(self._rejections)[: len(self._rejections) - _MAX_TRACKED_SOURCES]:
                    self._rejections.pop(stale_key, None)
        if sustained and key not in self._refusal_announced:
            self._refusal_announced.add(key)
            log.warning(
                "octowright.mcp.sustained_throttle_refused",
                source=key,
                rejections=rejections,
                span_seconds=self._refusal_span,
            )
        return sustained

    def _note_throttled(self, key: str, now: float) -> None:
        """Emit at most one throttle warning per source per window, with counts.

        The line names the follower key and both halves an operator needs:
        how many new-session requests this source were admitted into the
        window, and how many were turned away since the previous line — the
        difference between "burst at the limit" and "hard loop".
        """
        self._throttled_since_log[key] = self._throttled_since_log.get(key, 0) + 1
        last = self._last_log_at.get(key)
        if last is not None and now - last < self._retry_after:
            return
        refused = self._throttled_since_log.pop(key, 0)
        log.warning(
            "octowright.mcp.new_session_throttled",
            source=key,
            throttled_since_last_log=refused,
            requests_in_window=self._limiter.in_window(key, now),
            window_seconds=self._retry_after,
        )
        self._last_log_at[key] = now
        self._bound_log_state()

    def _bound_log_state(self) -> None:
        """Cap the log bookkeeping at the limiter's own bucket ceiling: keys
        come from a client-chosen header, so they need the same bound, and a
        key flood large enough to trip it loses only diagnostic accuracy."""
        for mapping in (self._last_log_at, self._throttled_since_log):
            if len(mapping) <= _MAX_TRACKED_SOURCES:
                continue
            drop = len(mapping) - _MAX_TRACKED_SOURCES
            for stale_key in list(mapping)[:drop]:
                mapping.pop(stale_key, None)

    async def _reject(self, send, key: str, *, gone: bool) -> None:
        payload = {
            "source": key,
            "retry_after_seconds": self._retry_after,
        }
        if gone:
            payload["error"] = (
                "follower refused: sustained new-session throttling — this follower is too old or "
                "looping for this leader; reconnect your MCP client so a current follower spawns"
            )
        else:
            payload["error"] = "new-session rate limit exceeded; retry after backoff"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 410 if gone else 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(self._retry_after).encode("ascii")),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
