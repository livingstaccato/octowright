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
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

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
async def test_exception_evicts_entry_so_resend_reruns() -> None:
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
    with _request_context("k1", sess):
        result = await tool()  # resend re-runs (failure was not cached)
    assert result == {"n": 2}
    assert len(calls) == 2


@pytest.mark.anyio
async def test_cancellation_evicts_entry() -> None:
    started = asyncio.Event()
    block = asyncio.Event()
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, int]:
        started.set()
        await block.wait()
        return {"n": 1}

    async def call() -> dict[str, int]:
        with _request_context("k1", sess):
            return await tool()

    t = asyncio.create_task(call())
    await started.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    # The cancelled run must have evicted its entry — the key is no longer cached.
    assert _idempotency._cache_size() == 0


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
async def test_oversize_result_not_cached_but_resend_reruns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_MAX_RESULT_BYTES", 16)
    calls: list[int] = []
    sess = object()

    @_idempotency._idempotent_dispatch
    async def tool(**_kw: Any) -> dict[str, str]:
        calls.append(1)
        return {"payload": "x" * 1000}  # far over the 16-byte cap

    with _request_context("k1", sess):
        await tool()
    with _request_context("k1", sess):
        await tool()
    assert len(calls) == 2  # over-cap result stored as a marker → resend re-ran
