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

* **Cancellation-isolated production.** The handler runs in a shielded producer
  task, so teardown of the request/session stops only that caller. The producer
  continues and records its result. If the producer itself terminates with any
  exception, its slot becomes an unknown-outcome tombstone instead of allowing a
  blind resend of a side effect that may already have committed.
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
* **Fail-closed capacity.** A fresh distinct key is refused before its handler
  runs when every bounded slot is still authoritative. Existing same-key callers
  continue to await or reuse their slot; live producers are never displaced.
* **Oversize results fail closed.** A successful result too large to retain
  leaves an authoritative terminal marker. A resend reports that the result is
  unavailable instead of executing the tool again.

Async handlers run directly and synchronous handlers preserve the MCP SDK's
worker-thread scheduling, while both kinds share the same at-most-once
boundary. The cache is process-global,
lock-guarded, TTL- and size-bounded, and never caches results larger than
``IDEMPOTENCY_MAX_RESULT_BYTES``.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import inspect
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, get_args

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


class IdempotencyCapacityError(RuntimeError):
    """Raised before execution when no bounded cache slot can safely be reused.

    A capacity refusal has a known outcome: this call's handler did not run.
    This is deliberately distinct from :class:`IdempotencyOutcomeUnknownError`,
    which means an earlier same-key producer may already have committed.
    """


class IdempotencyResultUnavailableError(RuntimeError):
    """Raised when a successful prior result was too large to cache.

    The prior handler definitely ran, so re-executing it would violate the
    at-most-once contract. The caller must verify state or retry a pure read
    with a fresh key.
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
        signature = inspect.signature(fn)
        annotations = inspect.get_annotations(fn, eval_str=True)
        bound = signature.bind_partial(*args, **kwargs)
        wire_arguments = tuple(
            (name, value)
            for name, value in bound.arguments.items()
            if not _is_mcp_context_annotation(annotations.get(name))
        )
        argsig = repr(wire_arguments)
    except Exception:  # pragma: no cover — tool args repr/sort reliably
        argsig = repr((args, sorted(kwargs.items())))
    digest = hashlib.sha256(f"{raw_key}\x00{method}\x00{argsig}".encode()).hexdigest()
    return digest


def _is_mcp_context_annotation(annotation: Any) -> bool:
    """True only for the SDK-injected Context type (including Optional/Union).

    Context is request-local transport state, not a wire tool argument. A
    reconnect necessarily creates a fresh object, so hashing it would bypass
    same-key deduplication. Parameter names alone are insufficient because a
    user tool may legitimately expose a wire argument named ``ctx``.
    """
    if getattr(annotation, "__name__", None) == "Context" and getattr(annotation, "__module__", "").startswith(
        "mcp.server.mcpserver"
    ):
        return True
    return any(_is_mcp_context_annotation(member) for member in get_args(annotation))


def _now() -> float:
    """Monotonic clock seam (patched in tests to drive TTL deterministically)."""
    return time.monotonic()


class _Entry:
    """A cache slot: an in-progress producer's completion event plus, once done,
    its (optionally cached) result."""

    __slots__ = (
        "abandon_reported",
        "done",
        "done_at",
        "event",
        "has_result",
        "outcome_unknown",
        "owner",
        "producer_task",
        "result",
        "started_at",
    )

    def __init__(self, owner: Any, producer_task: asyncio.Task[Any] | None = None) -> None:
        self.event = asyncio.Event()
        self.owner = owner  # identity of the session that created this entry
        self.producer_task = producer_task
        self.abandon_reported = False
        self.outcome_unknown = False
        self.done = False  # True once the producer stored a successful result
        self.result: Any = None
        self.has_result = False  # False for an oversize terminal marker
        self.done_at = 0.0
        # When the producer claimed this slot. Once the abandon threshold is
        # reached, cleanup cannot cancel or reclaim a live producer because its
        # side effect may already have committed (see _evict_expired_locked).
        self.started_at = _now()


_lock = threading.Lock()
_cache: OrderedDict[str, _Entry] = OrderedDict()

# True only while a producer task is running its own handler. Set by
# ``_run_producer`` before it calls the handler and read by ``wrapper`` as its
# very first action.
#
# Every async tool's module-level name is the FULLY WRAPPED function, so a
# composite tool that calls another tool by name (``browser_observe`` ->
# ``browser_page_outline``, or any ``response_mode="outline"`` follow-up) re-enters
# this dispatcher from inside its own session-gate lease. Without this flag the
# nested call — a different method name, so a different storage key — would spawn
# a SECOND producer task. That task is not the task holding the lease, so the
# gate's exact-task reentrancy rule correctly refuses it: it queues behind a lease
# its own awaiter holds, and the composite deadlocks until the queue timeout.
# Running the nested call inline in the SAME task keeps it reentrant.
#
# It is also the semantically correct cache behaviour: a client's idempotency key
# describes the top-level call only, so a sub-call no resend will ever target
# independently must not claim a slot of its own.
#
# No reset is needed. ``_run_producer`` is the entire body of its own task and
# asyncio gives each task a copied ``Context``, so the value can never leak back
# into the caller's context or into a sibling task.
_in_producer: contextvars.ContextVar[bool] = contextvars.ContextVar("octowright_idempotency_in_producer", default=False)


def _current_key() -> str | None:
    value = current_meta_value(_META_KEY)
    return value if isinstance(value, str) else None


def _current_owner() -> Any:
    return id(current_session())


def _result_size(result: Any) -> int:
    try:
        return len(repr(result).encode("utf-8"))
    except Exception:  # pragma: no cover — repr should never raise for tool results
        return defaults.IDEMPOTENCY_MAX_RESULT_BYTES + 1


def _resume_window_seconds() -> float:
    """Max wall-clock from a result being stored to the bridge re-sending it.
    Entries younger than this must never be evicted by the size bound."""
    return defaults.BRIDGE_RESUME_MAX_ATTEMPTS * (
        defaults.BRIDGE_CONNECT_TIMEOUT_SECONDS + defaults.BRIDGE_RECONNECT_MAX_SECONDS
    )


def _abandon_threshold_seconds() -> float:
    """Age past which a taskless in-progress orphan can be reclaimed.

    A producer is expected to finish, fail, or be cancelled — all of which
    resolve the slot. A taskless synthetic orphan can be reclaimed at this
    point, but a real producer may already have committed a side effect. Its
    slot remains authoritative until the task is confirmed terminated. The
    margin over the wait window keeps a merely-slow producer out of the orphan
    recovery path — a waiter gives up before the threshold is reached.
    """
    return defaults.IDEMPOTENCY_INPROGRESS_WAIT_SECONDS * 2


def _evict_expired_locked() -> None:
    ttl = defaults.IDEMPOTENCY_TTL_SECONDS
    abandon = _abandon_threshold_seconds()
    now = _now()
    for key, entry in list(_cache.items()):
        if entry.done or entry.outcome_unknown:
            if (now - entry.done_at) > ttl:
                del _cache[key]
            continue
        if (now - entry.started_at) <= abandon:
            continue

        producer_task = entry.producer_task
        if producer_task is not None and not producer_task.done():
            if not entry.abandon_reported:
                entry.abandon_reported = True
                log.warning(
                    "octowright.idempotency.abandoned_producer_retained",
                    age_seconds=round(now - entry.started_at, 1),
                )
            continue

        log.warning(
            "octowright.idempotency.abandoned_entry_reclaimed",
            age_seconds=round(now - entry.started_at, 1),
        )
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
        if (entry.done or entry.outcome_unknown) and (now - entry.done_at) > window:
            del _cache[key]
    if len(_cache) > bound:
        log.warning("octowright.idempotency.over_bound", size=len(_cache), bound=bound)


def _make_room_for_key_locked(key: str) -> bool:
    """Return whether ``key`` can claim a slot without exceeding the bound.

    Replacing an over-size DONE marker for the same key does not grow the cache.
    For a distinct key, safely reusable completed entries are evicted
    oldest-first. Live producers and reconnect-visible results remain
    authoritative, so exhaustion refuses admission instead of displacing them.
    """
    if key in _cache:
        return True

    bound = defaults.IDEMPOTENCY_MAX_ENTRIES
    if bound <= 0:
        return False
    if len(_cache) < bound:
        return True

    window = _resume_window_seconds()
    now = _now()
    for candidate_key in list(_cache.keys()):
        entry = _cache[candidate_key]
        if (entry.done or entry.outcome_unknown) and (now - entry.done_at) > window:
            del _cache[candidate_key]
            if len(_cache) < bound:
                return True
    return False


def _unknown_outcome_error() -> IdempotencyOutcomeUnknownError:
    return IdempotencyOutcomeUnknownError(
        "a prior call with the same idempotency key ended without a confirmed result; its outcome is unknown. "
        "Retry a read with a fresh key, or verify state before re-issuing a mutation."
    )


def _observe_producer_completion(task: asyncio.Task[Any]) -> None:
    """Retrieve detached producer errors after a request waiter is cancelled."""
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _run_producer(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], key: str, entry: _Entry
) -> Any:
    """Execute and resolve one authoritative producer independently of callers."""
    # Mark this task as "inside a producer" so a nested tool call made by this
    # handler runs inline here instead of hopping into a second task (see
    # _in_producer). Never reset: this task's context is its own copy.
    _in_producer.set(True)
    try:
        if asyncio.iscoroutinefunction(fn):
            result = await fn(*args, **kwargs)
        else:
            result = await asyncio.to_thread(fn, *args, **kwargs)
    except BaseException:
        # A handler can commit a mutation before failing or being cancelled.
        # Preserve an explicit unknown tombstone so a reconnect cannot blindly
        # execute it again. It remains subject to the cache's normal TTL/resume
        # horizon and capacity policy.
        with _lock:
            if _cache.get(key) is entry:
                entry.outcome_unknown = True
                entry.done_at = _now()
                entry.producer_task = None
                _enforce_bound_locked()
        entry.event.set()
        raise

    with _lock:
        if _cache.get(key) is entry:
            entry.done = True
            entry.done_at = _now()
            entry.producer_task = None
            if _result_size(result) <= defaults.IDEMPOTENCY_MAX_RESULT_BYTES:
                entry.result = result
                entry.has_result = True
            # Else retain an authoritative terminal marker. The handler ran
            # successfully, so a later resend must never execute it again.
            _enforce_bound_locked()
    entry.event.set()
    return result


def _idempotent_dispatch(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async tool so a re-sent call (same idempotency key) dedups.

    Calls with no key still execute through the async wrapper; synchronous tools
    preserve the SDK's worker scheduling while receiving the same authoritative
    slot when a key is present.
    ``functools.wraps`` preserves the signature/annotations so the server's
    Context injection and input schema still resolve through this wrapper.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _in_producer.get():
            # Nested call from inside a composite tool's own handler: run it
            # inline, in this same task, with no slot of its own (see
            # _in_producer for why a second producer task would deadlock).
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return await asyncio.to_thread(fn, *args, **kwargs)
        if not defaults.IDEMPOTENCY_ENABLED:
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return await asyncio.to_thread(fn, *args, **kwargs)
        raw_key = _current_key()
        if raw_key is None:
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return await asyncio.to_thread(fn, *args, **kwargs)
        key = _storage_key(raw_key, fn, args, kwargs)
        owner = _current_owner()

        while True:
            wait_entry: _Entry | None = None
            with _lock:
                _evict_expired_locked()
                entry = _cache.get(key)
                if entry is not None and entry.outcome_unknown:
                    _cache.move_to_end(key)
                    raise _unknown_outcome_error()
                if entry is not None and entry.done:
                    _cache.move_to_end(key)
                    if entry.has_result:
                        return entry.result
                    raise IdempotencyResultUnavailableError(
                        "a prior call with the same idempotency key succeeded, but its result was too large "
                        "to cache; the call was not executed again. Verify state, or retry a pure read with "
                        "a fresh key."
                    )
                if entry is None:
                    # Fresh (or the prior producer already evicted after failing)
                    # → we produce.
                    if not _make_room_for_key_locked(key):
                        bound = defaults.IDEMPOTENCY_MAX_ENTRIES
                        log.warning(
                            "octowright.idempotency.capacity_refused",
                            size=len(_cache),
                            bound=bound,
                        )
                        raise IdempotencyCapacityError(
                            f"the idempotency cache is at capacity ({bound} entries); this call was not executed. "
                            "Retry after an in-progress call completes or a cached result expires."
                        )
                    mine = _Entry(owner, producer_task=asyncio.current_task())
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

        producer = asyncio.create_task(_run_producer(fn, args, kwargs, key, mine))
        producer.add_done_callback(_observe_producer_completion)
        with _lock:
            if _cache.get(key) is mine:
                mine.producer_task = producer
        # A request/session teardown cancels this waiter, not the mutation. The
        # detached producer finishes into the shared slot for a reconnect.
        return await asyncio.shield(producer)

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
