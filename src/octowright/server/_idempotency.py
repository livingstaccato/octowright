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
* **Await-any-owner, then honest-unknown.** An in-progress entry is awaited
  regardless of which session created it, so a resend that races a still-running
  producer dedups on that producer's result instead of launching a second side
  effect. If the producer neither completes nor evicts within
  ``IDEMPOTENCY_INPROGRESS_WAIT_SECONDS``, its fate is genuinely unknown, so the
  resend raises ``IdempotencyOutcomeUnknownError`` rather than silently
  re-executing a possibly-committed mutation.
* **Namespaced key.** The follower's ``octowrightIdempotencyKey`` is hashed with
  the method name and canonical args (``_storage_key``), so a key that is reused
  across different calls can't return another call's cached result. A legitimate
  resend — same key, method and args — still resolves to the same slot.

Scoped to async tools (octowright's side-effectful tools are all async); sync tools
pass through untouched. The cache is process-global, lock-guarded, TTL- and
size-bounded, and never caches results larger than ``IDEMPOTENCY_MAX_RESULT_BYTES``
(it stores a DONE-marker instead, so a resend re-runs the cheap idempotent read).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from provide.telemetry import get_logger

from octowright import defaults
from octowright.server._request_context import current_meta_value, current_session

log = get_logger(__name__)

_META_KEY = "octowrightIdempotencyKey"


class IdempotencyOutcomeUnknownError(RuntimeError):
    """Raised when a re-sent call meets an in-progress producer whose fate can't
    be established within the wait window — the prior producer neither completed
    nor evicted, so whether its side effect committed is genuinely unknown.

    Reporting this (instead of silently re-executing) is the honest answer: a
    blind re-run could double-execute a committed side effect. The caller can
    retry a pure read with a fresh key, or surface the ambiguity for a mutation.
    """


def _storage_key(raw_key: str, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Namespace the follower-supplied idempotency key by method + canonical
    args. A key that is (buggily) reused across different calls then lands in
    different cache slots instead of returning another call's result. A legit
    resend — same key, method and args — still hashes to the same slot, so
    dedup is unchanged. Arg VALUES are hashed, never stored, so a
    credential-substituted arg is not exposed here."""
    method = getattr(fn, "__name__", "?")
    try:
        argsig = repr((args, sorted(kwargs.items())))
    except Exception:  # pragma: no cover — tool args repr/sort reliably
        argsig = repr(id(kwargs))
    digest = hashlib.sha256(f"{raw_key}\x00{method}\x00{argsig}".encode()).hexdigest()
    return digest


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
    value = current_meta_value(_META_KEY)
    return value if isinstance(value, str) else None


def _current_owner() -> Any:
    return id(current_session())


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
    preserves the signature/annotations so the server's Context injection and input
    schema still resolve through this wrapper.
    """
    if not asyncio.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not defaults.IDEMPOTENCY_ENABLED:
            return await fn(*args, **kwargs)
        raw_key = _current_key()
        if raw_key is None:
            return await fn(*args, **kwargs)
        key = _storage_key(raw_key, fn, args, kwargs)
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
                if entry is None:
                    # Fresh (or the prior producer already evicted after failing)
                    # → we produce.
                    mine = _Entry(owner)
                    _cache[key] = mine
                    _cache.move_to_end(key)
                    break
                # In-progress — await it REGARDLESS of owner. A resend that races
                # a still-running producer must dedup on that producer's result,
                # not launch a second side effect (the double-execute bug).
                wait_entry = entry

            try:
                await asyncio.wait_for(wait_entry.event.wait(), timeout=defaults.IDEMPOTENCY_INPROGRESS_WAIT_SECONDS)
            except TimeoutError:
                # The producer neither completed nor evicted within the window,
                # so whether its side effect committed is unknown. Do NOT
                # silently re-execute — report the ambiguity honestly.
                raise IdempotencyOutcomeUnknownError(
                    "a prior in-progress call with the same idempotency key did not complete within "
                    f"{defaults.IDEMPOTENCY_INPROGRESS_WAIT_SECONDS}s; its outcome is unknown. Retry a "
                    "read with a fresh key, or verify state before re-issuing a mutation."
                ) from None
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
