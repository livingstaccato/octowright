# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side idempotency cache (``server/_idempotency.py``).

Drives ``_idempotent_dispatch`` directly by setting octowright's request
contextvar — no HTTP / MCP server app needed. The follower injects a stable
``octowrightIdempotencyKey`` into each tools/call _meta and re-sends it verbatim
on reconnect; this cache makes the re-sent call a no-op (cached result / awaited
in-progress run) instead of a double-execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver import Context

from octowright.server import _idempotency
from octowright.server import _request_context as _rc


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    _idempotency._reset_for_tests()
    yield
    _idempotency._reset_for_tests()


@contextlib.contextmanager
def _request_context(key: str | None, session: Any) -> Iterator[None]:
    """Set the octowright request contextvar to a minimal RequestContext carrying ``key`` in _meta
    and owned by ``session`` (a sentinel object standing in for an MCP session)."""
    # MCP 2.0 hands handlers a plain dict for _meta; a non-spec key like ours
    # survives verbatim there instead of landing in a pydantic `model_extra`.
    meta = {"octowrightIdempotencyKey": key} if key is not None else None
    ctx = SimpleNamespace(meta=meta, session=session, request_id="r")
    token = _rc._request_ctx.set(ctx)
    try:
        yield
    finally:
        _rc._request_ctx.reset(token)


# ─── no key / disabled ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_no_key_runs_normally_and_caches_nothing() -> None:
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    sess = object()
    with _request_context(None, sess):
        assert await tool() == {"n": 1}
    with _request_context(None, sess):
        assert await tool() == {"n": 2}  # ran again — no dedup without a key
    assert len(calls) == 2


# ─── dedup ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_duplicate_key_returns_cached_without_reexec() -> None:
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    sess = object()
    with _request_context("k1", sess):
        first = await tool()
    with _request_context("k1", sess):
        second = await tool()
    assert first == second == {"n": 1}
    assert len(calls) == 1  # second call served from cache


@pytest.mark.anyio
async def test_concurrent_in_progress_await_runs_once() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        started.set()
        await release.wait()
        return {"n": len(calls)}

    async def call() -> dict[str, int]:
        with _request_context("k1", sess):
            return await tool()

    t1 = asyncio.create_task(call())
    await started.wait()  # producer registered in-progress
    t2 = asyncio.create_task(call())
    await asyncio.sleep(0)  # let the waiter attach
    release.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == r2 == {"n": 1}
    assert len(calls) == 1  # waiter never re-ran


@pytest.mark.anyio
async def test_in_progress_stuck_producer_reports_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resend that races a still-running producer must NOT double-execute. When
    the producer's fate can't be established within the window, the resend gets
    an explicit unknown-outcome error instead of a silent second run."""
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", 0.05)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        started.set()
        await release.wait()  # the first run blocks past the wait window
        return {"n": len(calls)}

    async def first() -> dict[str, int]:
        with _request_context("k1", object()):  # session A
            return await tool()

    t1 = asyncio.create_task(first())
    await started.wait()
    # Session B resend: the stuck producer's outcome is unknown → error, no re-run.
    with _request_context("k1", object()), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
        await tool()
    assert len(calls) == 1  # never re-ran
    release.set()
    with contextlib.suppress(Exception):
        await t1


@pytest.mark.anyio
async def test_aged_live_producer_is_retained_across_repeated_resends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Age-based cleanup must retain a live producer and its authoritative slot.

    Repeated same-key resends report ambiguity without cancelling or executing
    the producer twice.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["t"])
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", 0.02)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        if len(calls) > 1:
            return {"n": len(calls)}
        started.set()
        await release.wait()
        return {"n": 1}

    async def first() -> dict[str, int]:
        with _request_context("k1", object()):
            return await tool()

    producer = asyncio.create_task(first())
    try:
        await started.wait()
        clock["t"] += _idempotency._abandon_threshold_seconds() + 1

        for _ in range(2):
            with _request_context("k1", object()), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
                await tool()

        assert not producer.cancelled()
        assert not producer.done()
        assert len(calls) == 1

        release.set()
        assert await producer == {"n": 1}
        with _request_context("k1", object()):
            assert await tool() == {"n": 1}
        assert len(calls) == 1
    finally:
        release.set()
        if not producer.done():
            producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer


@pytest.mark.anyio
async def test_aged_live_producer_is_never_cancelled_into_a_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Age cannot prove whether a live producer already committed its side effect."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["t"])
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", 0.02)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)  # model a side effect that lands before the final await
        started.set()
        await release.wait()
        return {"n": len(calls)}

    async def first() -> dict[str, int]:
        with _request_context("k1", object()):
            return await tool()

    producer = asyncio.create_task(first())
    try:
        await started.wait()
        clock["t"] += _idempotency._abandon_threshold_seconds() + 1

        with _request_context("k1", object()), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
            await tool()

        assert not producer.cancelled()
        assert not producer.done()
        assert calls == [1]
        release.set()
        assert await producer == {"n": 1}
    finally:
        release.set()
        if not producer.done():
            producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer


@pytest.mark.anyio
async def test_capacity_refuses_a_fresh_key_without_displacing_live_producers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full cache cannot grow by admitting another distinct live producer.

    Existing keys remain authoritative at capacity: a same-key caller awaits
    the original producer and receives its result without running the handler
    again.
    """
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_MAX_ENTRIES", 2)
    release = asyncio.Event()
    started = {"one": asyncio.Event(), "two": asyncio.Event()}
    calls: list[str] = []

    @_idempotency._idempotent_dispatch
    async def tool(label: str) -> str:
        if label == "fresh":
            raise AssertionError("a fresh handler ran after the idempotency cache reached capacity")
        calls.append(label)
        started[label].set()
        await release.wait()
        return label

    async def call(key: str, label: str) -> str:
        with _request_context(key, object()):
            return await tool(label)

    producer_one = asyncio.create_task(call("k-one", "one"))
    await started["one"].wait()
    producer_two = asyncio.create_task(call("k-two", "two"))
    await started["two"].wait()

    try:
        with pytest.raises(_idempotency.IdempotencyCapacityError, match="idempotency cache is at capacity"):
            await call("k-fresh", "fresh")

        assert calls == ["one", "two"]
        assert _idempotency._cache_size() == 2

        same_key_waiter = asyncio.create_task(call("k-one", "one"))
        await asyncio.sleep(0)
        assert not same_key_waiter.done()

        release.set()
        assert await asyncio.gather(producer_one, producer_two, same_key_waiter) == ["one", "two", "one"]
        assert calls == ["one", "two"]
    finally:
        release.set()
        for task in (producer_one, producer_two):
            if not task.done():
                task.cancel()
        await asyncio.gather(producer_one, producer_two, return_exceptions=True)


@pytest.mark.anyio
async def test_orphan_in_progress_reports_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A same-key waiter on an entry that never resolves reports unknown rather
    than silently re-executing a possibly-committed side effect."""
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", 0.05)
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    # Seed an orphan under the SAME namespaced key the wrapper computes.
    key = _idempotency._storage_key("k1", tool, (), {})
    _idempotency._seed_orphan_in_progress(key, owner=id(sess))

    with _request_context("k1", sess), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
        await tool()
    assert len(calls) == 0  # the orphan blocked us; we did not re-run


@pytest.mark.anyio
async def test_same_key_different_args_do_not_cross_dedup() -> None:
    """The idempotency key is namespaced by method + args: a key (buggily) reused
    across different args must NOT return the other call's cached result."""
    calls: list[dict[str, Any]] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"n": len(calls), "args": kw}

    with _request_context("k1", sess):
        r1 = await tool(x=1)
    with _request_context("k1", sess):  # SAME key, different args
        r2 = await tool(x=2)
    assert r1["args"] == {"x": 1}
    assert r2["args"] == {"x": 2}  # not r1's cached result
    assert len(calls) == 2  # both ran


# ─── failure handling ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_exception_retains_unknown_outcome_so_resend_does_not_rerun() -> None:
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("boom")
        return {"n": len(calls)}

    with _request_context("k1", sess), pytest.raises(ValueError):
        await tool()
    with _request_context("k1", sess), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
        await tool()
    assert len(calls) == 1


@pytest.mark.anyio
async def test_session_cancellation_does_not_cancel_or_reexecute_producer() -> None:
    started = asyncio.Event()
    block = asyncio.Event()
    sess = object()
    commits: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        commits.append(1)  # side effect may land before request teardown
        started.set()
        await block.wait()
        return {"n": len(commits)}

    async def call() -> dict[str, int]:
        with _request_context("k1", sess):
            return await tool()

    t = asyncio.create_task(call())
    await started.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    # Request/session teardown cancels the caller, not the authoritative
    # producer: it may already have committed and must finish into the cache.
    assert _idempotency._cache_size() == 1
    assert commits == [1]

    block.set()
    for _ in range(20):
        await asyncio.sleep(0)
        with _idempotency._lock:
            if next(iter(_idempotency._cache.values())).done:
                break

    with _request_context("k1", sess):
        assert await tool() == {"n": 1}
    assert commits == [1]


@pytest.mark.anyio
async def test_producer_cancellation_leaves_unknown_tombstone() -> None:
    """Even direct producer loss cannot authorize an automatic mutation retry."""
    started = asyncio.Event()
    block = asyncio.Event()
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        started.set()
        await block.wait()
        return {"n": len(calls)}

    async def call() -> dict[str, int]:
        with _request_context("k1", sess):
            return await tool()

    caller = asyncio.create_task(call())
    await started.wait()
    with _idempotency._lock:
        producer = next(iter(_idempotency._cache.values())).producer_task
    assert producer is not None
    producer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    with _request_context("k1", sess), pytest.raises(_idempotency.IdempotencyOutcomeUnknownError):
        await tool()
    assert calls == [1]


# ─── eviction & memory ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ttl_eviction_reruns(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["t"])
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_TTL_SECONDS", 100.0)
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    with _request_context("k1", sess):
        await tool()
    clock["t"] += 101.0  # past TTL
    with _request_context("k1", sess):
        await tool()
    assert len(calls) == 2  # entry expired → re-ran


@pytest.mark.anyio
async def test_oversize_result_resend_fails_closed_without_rerunning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_MAX_RESULT_BYTES", 16)
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, str]:
        calls.append(1)
        return {"payload": "x" * 1000}  # far over the 16-byte cap

    with _request_context("k1", sess):
        await tool()
    with _request_context("k1", sess), pytest.raises(_idempotency.IdempotencyResultUnavailableError, match="too large"):
        await tool()
    assert len(calls) == 1


def test_result_size_counts_utf8_bytes_not_unicode_codepoints() -> None:
    assert _idempotency._result_size("🚀") == len(repr("🚀").encode("utf-8"))
    assert _idempotency._result_size("🚀") > len(repr("🚀"))


@pytest.mark.anyio
async def test_sync_mutation_resend_uses_cached_result_without_rerunning() -> None:
    calls: list[str] = []
    sess = object()
    execution_threads: list[int] = []

    @_idempotency._idempotent_dispatch
    def tool(value: str) -> dict[str, int]:
        execution_threads.append(threading.get_ident())
        calls.append(value)
        return {"calls": len(calls)}

    with _request_context("sync-k1", sess):
        first = await tool("write")
    with _request_context("sync-k1", object()):
        second = await tool("write")

    assert first == second == {"calls": 1}
    assert calls == ["write"]
    assert execution_threads[0] != threading.get_ident()


@pytest.mark.anyio
async def test_reconnect_context_identity_is_excluded_from_storage_key() -> None:
    calls: list[int] = []

    @_idempotency._idempotent_dispatch
    async def tool(value: str, ctx: Context | None = None) -> dict[str, int]:
        del value, ctx
        calls.append(1)
        return {"calls": len(calls)}

    with _request_context("context-key", object()):
        first = await tool("same-wire-value", ctx=object())  # type: ignore[arg-type]
    with _request_context("context-key", object()):
        second = await tool("same-wire-value", ctx=object())  # type: ignore[arg-type]

    assert first == second == {"calls": 1}
    assert calls == [1]


@pytest.mark.anyio
async def test_blocking_sync_tool_does_not_stall_event_loop() -> None:
    entered = threading.Event()
    release = threading.Event()

    @_idempotency._idempotent_dispatch
    def tool() -> str:
        entered.set()
        release.wait(timeout=1.0)
        return "done"

    with _request_context("sync-blocking", object()):
        call = asyncio.create_task(tool())
    assert await asyncio.to_thread(entered.wait, 0.5)
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
    assert not call.done()
    release.set()
    assert await call == "done"
