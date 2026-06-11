# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side idempotency cache for re-sent tool calls.

The follower bridge injects a stable ``octowrightIdempotencyKey`` into each
``tools/call`` _meta and, after a reconnect, re-sends the SAME key verbatim. This
cache makes the re-sent call a no-op — it returns the cached result, or awaits an
in-progress run — instead of double-executing a side-effectful tool.

Safety properties (each independently sufficient; kept together as belt-and-suspenders):

* **Success-only caching.** Any exception (including the ``CancelledError`` raised
  when a reconnect kills the old session's tool coroutine) evicts the entry, so a
  resend re-runs rather than replaying a stale failure.
* **Session-identity takeover.** An in-progress entry owned by a *different*
  (now-dead) session is abandoned and re-executed — the primary resume path, since
  every reconnect is a fresh leader session.
* **Bounded await.** A waiter on an in-progress entry never blocks longer than
  ``IDEMPOTENCY_INPROGRESS_WAIT_SECONDS`` before taking over.

Scoped to async tools (octowright's side-effectful tools are all async); sync tools
pass through untouched. The cache is process-global, lock-guarded, TTL- and
size-bounded, and never caches results larger than ``IDEMPOTENCY_MAX_RESULT_BYTES``
(it stores a DONE-marker instead, so a resend re-runs the cheap idempotent read).
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from mcp.server.lowlevel.server import request_ctx
from provide.telemetry import get_logger

from octowright import defaults

log = get_logger(__name__)

_META_KEY = "octowrightIdempotencyKey"


def _now() -> float:
    """Monotonic clock seam (patched in tests to drive TTL deterministically)."""
    return time.monotonic()


class _Entry:
    """A cache slot: an in-progress producer's completion event plus, once done,
    its (optionally cached) result."""

    __slots__ = ("done", "done_at", "event", "has_result", "owner", "result")

    def __init__(self, owner: Any) -> None:
        self.event = asyncio.Event()
        self.owner = owner  # identity of the session that created this entry
        self.done = False  # True once the producer stored a successful result
        self.result: Any = None
        self.has_result = False  # False for an over-cap DONE-marker
        self.done_at = 0.0


_lock = threading.Lock()
_cache: OrderedDict[str, _Entry] = OrderedDict()


def _current_key() -> str | None:
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    meta = getattr(ctx, "meta", None)
    if meta is None:
        return None
    extra = getattr(meta, "model_extra", None) or {}
    value = extra.get(_META_KEY)
    return value if isinstance(value, str) else None


def _current_owner() -> Any:
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    return id(getattr(ctx, "session", None))


def _result_size(result: Any) -> int:
    try:
        return len(repr(result))
    except Exception:  # pragma: no cover — repr should never raise for tool results
        return defaults.IDEMPOTENCY_MAX_RESULT_BYTES + 1


def _resume_window_seconds() -> float:
    """Max wall-clock from a result being stored to the bridge re-sending it.
    Entries younger than this must never be evicted by the size bound."""
    return defaults.BRIDGE_RESUME_MAX_ATTEMPTS * (
        defaults.BRIDGE_CONNECT_TIMEOUT_SECONDS + defaults.BRIDGE_RECONNECT_MAX_SECONDS
    )


def _evict_expired_locked() -> None:
    ttl = defaults.IDEMPOTENCY_TTL_SECONDS
    now = _now()
    for key in [k for k, e in _cache.items() if e.done and (now - e.done_at) > ttl]:
        del _cache[key]


def _enforce_bound_locked() -> None:
    bound = defaults.IDEMPOTENCY_MAX_ENTRIES
    if len(_cache) <= bound:
        return
    window = _resume_window_seconds()
    now = _now()
    # Evict oldest-first, but only DONE entries safely past the resume window —
    # never drop one a reconnect might still re-send, nor an in-progress entry.
    for key in list(_cache.keys()):
        if len(_cache) <= bound:
            break
        entry = _cache[key]
        if entry.done and (now - entry.done_at) > window:
            del _cache[key]
    if len(_cache) > bound:
        log.warning("octowright.idempotency.over_bound", size=len(_cache), bound=bound)


def _idempotent_dispatch(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async tool so a re-sent call (same idempotency key) dedups.

    Sync tools and calls with no key pass straight through. ``functools.wraps``
    preserves the signature/annotations so FastMCP's Context injection and input
    schema still resolve through this wrapper.
    """
    if not asyncio.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not defaults.IDEMPOTENCY_ENABLED:
            return await fn(*args, **kwargs)
        key = _current_key()
        if key is None:
            return await fn(*args, **kwargs)
        owner = _current_owner()

        while True:
            wait_entry: _Entry | None = None
            with _lock:
                _evict_expired_locked()
                entry = _cache.get(key)
                if entry is not None and entry.done:
                    _cache.move_to_end(key)
                    if entry.has_result:
                        return entry.result
                    entry = None  # over-cap DONE-marker → re-run as a fresh producer
                if entry is None or entry.owner != owner:
                    # Fresh, or abandoned by a now-dead session → we produce.
                    mine = _Entry(owner)
                    _cache[key] = mine
                    _cache.move_to_end(key)
                    break
                wait_entry = entry  # same-session in-progress → await it (below)

            try:
                await asyncio.wait_for(wait_entry.event.wait(), timeout=defaults.IDEMPOTENCY_INPROGRESS_WAIT_SECONDS)
            except TimeoutError:
                with _lock:
                    if _cache.get(key) is wait_entry and not wait_entry.done:
                        del _cache[key]  # abandoned: re-create as producer next loop
            continue

        try:
            result = await fn(*args, **kwargs)
        except BaseException:
            # Evict on ANY failure (incl. CancelledError from session teardown) so
            # a resend re-runs instead of replaying a stale/partial failure.
            with _lock:
                if _cache.get(key) is mine:
                    del _cache[key]
            mine.event.set()
            raise

        with _lock:
            if _cache.get(key) is mine:
                mine.done = True
                mine.done_at = _now()
                if _result_size(result) <= defaults.IDEMPOTENCY_MAX_RESULT_BYTES:
                    mine.result = result
                    mine.has_result = True
                # else: DONE-marker — dedup an in-flight resend, but a later resend re-runs.
                _enforce_bound_locked()
        mine.event.set()
        return result

    return wrapper


# ─── test hooks ──────────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    with _lock:
        _cache.clear()


def _cache_size() -> int:
    with _lock:
        return len(_cache)


def _seed_orphan_in_progress(key: str, owner: Any) -> None:
    """Insert an in-progress entry that never resolves — exercises the
    bounded-await backstop / takeover path."""
    with _lock:
        _cache[key] = _Entry(owner)
