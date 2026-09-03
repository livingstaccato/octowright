# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from octowright_terminal import pool as pool_module
from octowright_terminal.errors import ProtectedTerminalCloseError
from octowright_terminal.pool import TerminalPool

from octowright.plugins.session_launch import PluginContext

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")

#: A binary that exits immediately, resolved through PATH because its location
#: is not portable (/bin/true on Linux, /usr/bin/true on macOS).
TRUE_BIN = shutil.which("true") or "/usr/bin/true"


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0) -> None:
    """Poll ``predicate`` against a wall-clock deadline.

    A deadline rather than a fixed iteration count: what each iteration costs
    depends on the connector's own internal read timeout, so `range(N)` encodes
    a budget nobody measured and turns a slow CI runner into a bare
    ``assert ... is None`` failure.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)


@pytest.fixture
def ctx(tmp_path):
    return PluginContext(kind="terminal", recordings_dir=tmp_path, id_in_use=lambda _id, **_: False)


async def test_launch_registers_and_lists_session(ctx) -> None:
    pool = TerminalPool(ctx)
    try:
        result = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="cat")
        iid = result["instance_id"]
        assert result["kind"] == "terminal"
        assert result["log_path"].endswith(".jsonl")

        summaries = pool.list_sessions()
        assert len(summaries) == 1
        s = summaries[0]
        # Same keys BrowserPool.list_sessions() returns, so /api/sessions is uniform.
        assert set(s) >= {"instance_id", "kind", "label", "profile", "url", "log_path", "har_path", "protected"}
        assert s["instance_id"] == iid
        assert s["kind"] == "terminal"
        assert s["url"] is None
        assert s["har_path"] is None
    finally:
        await pool.close_all(force=True)


async def test_get_and_maybe_get(ctx) -> None:
    pool = TerminalPool(ctx)
    try:
        iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}))["instance_id"]
        assert pool.get(iid).instance_id == iid
        assert pool.maybe_get(iid) is not None
        assert pool.maybe_get("nope") is None
        with pytest.raises(KeyError):
            pool.get("nope")
    finally:
        await pool.close_all(force=True)


async def test_failed_launch_leaves_no_orphan_recording(ctx, tmp_path) -> None:
    pool = TerminalPool(ctx)
    # SSH without known_hosts: the connector raises ValueError in its ctor (via
    # build_connector in TerminalEngine.__init__), after the recorder file is
    # created but before the session registers. The transaction (ctx.begin_session)
    # discards the opening-row-only recording, so the file must not be orphaned.
    with pytest.raises(ValueError, match="known_hosts"):
        await pool.launch(kind="ssh", connector_config={"host": "h", "username": "u"})
    assert list(tmp_path.glob("*.jsonl")) == []
    assert list(pool.iter_sessions()) == []


async def test_close_refuses_protected_without_force(ctx) -> None:
    pool = TerminalPool(ctx)
    iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, protected=True))["instance_id"]
    try:
        with pytest.raises(ProtectedTerminalCloseError):
            await pool.close(iid)
        # still present after refused close
        assert pool.maybe_get(iid) is not None
    finally:
        await pool.close(iid, force=True)
    assert pool.maybe_get(iid) is None


async def test_close_returns_a_close_result(ctx) -> None:
    pool = TerminalPool(ctx)
    iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}))["instance_id"]
    result = await pool.close(iid, force=True)
    assert result == {"instance_id": iid, "kind": "terminal", "closed": True}


async def test_close_all_attempts_every_session_and_aggregates_failures(ctx) -> None:
    pool = TerminalPool(ctx)
    sessions = {
        "first": SimpleNamespace(protected=False, close=AsyncMock(side_effect=RuntimeError("first failed"))),
        "second": SimpleNamespace(protected=False, close=AsyncMock()),
        "third": SimpleNamespace(protected=False, close=AsyncMock(side_effect=OSError("third failed"))),
    }
    pool._sessions.update(sessions)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError) as exc_info:
        await pool.close_all(force=True)

    for session in sessions.values():
        session.close.assert_awaited_once()
    assert list(pool._sessions) == ["first", "third"]
    message = str(exc_info.value)
    assert message.index("first") < message.index("third")
    assert "first failed" in message
    assert "third failed" in message


async def test_dead_connector_is_evicted_from_the_registry(ctx) -> None:
    """A terminal whose connector ends must stop being listed as live.

    The engine has always NOTICED the death (``_on_poll_done`` records a stop
    and logs ``terminal.poll_loop.died``), but the pool never learned:
    ``list_sessions`` reads ``_sessions`` with no liveness filter and
    ``_sessions.pop`` ran only inside ``close()``. So a dropped SSH connection
    or an exited shell kept appearing in ``terminal_list`` and the dashboard as
    though it were live, until somebody closed it by hand.
    """
    pool = TerminalPool(ctx)
    try:
        # `true` exits immediately, so the poll loop sees a disconnected
        # connector and records an "eof" stop without any help from the test.
        # Resolved through PATH rather than hardcoded: this was /bin/true, which
        # does not exist on macOS (it is /usr/bin/true), and uterm's
        # validate_command checks only shape -- never existence -- so the fork
        # succeeded, the execve failed inside the child, and the test passed by
        # exercising an exec failure instead of the clean-exit path it names.
        result = await pool.launch(kind="pty", connector_config={"command": TRUE_BIN}, label="dies")
        iid = result["instance_id"]
        session = pool.get(iid)
        await _wait_until(lambda: pool.maybe_get(iid) is None)

        assert pool.maybe_get(iid) is None, "dead terminal still resolvable"
        assert [s["instance_id"] for s in pool.list_sessions()] == []
        assert list(pool.iter_sessions()) == []

        # Against the real recorder, not a mock: eviction drops the only
        # reference to the session, so if it does not also close it nothing
        # ever will. `_fh` is private, but it is the resource -- asserting on a
        # public proxy would leave the actual handle untested.
        await pool.drain_evictions()
        assert session.recorder._fh.closed, "evicted session leaked its recording handle"
    finally:
        await pool.close_all(force=True)


async def test_eviction_closes_the_session_it_drops(ctx) -> None:
    """Eviction must TEAR DOWN, not just forget.

    Dropping the registry entry removes the only reference to the session --
    core keeps no parallel table -- so an eviction that does not also close it
    leaks whatever the connector holds (an SSH transport, a PTY master fd and
    an unreaped child) plus the recorder's file handle, and ``close_all`` at
    shutdown can no longer reach it to clean up. That is strictly worse than
    the stale listing eviction was added to fix.
    """
    pool = TerminalPool(ctx)
    engine = SimpleNamespace()
    session = SimpleNamespace(connector_type="pty", engine=engine, close=AsyncMock())
    pool._sessions["gone"] = session  # type: ignore[assignment]

    pool._evict_stopped("gone", engine, "eof")

    assert "gone" not in pool._sessions
    # Teardown is scheduled rather than inline: `_evict_stopped` runs from a
    # sync asyncio done-callback and cannot await. Draining the pool's own task
    # set keeps this deterministic instead of sleeping for a guessed interval.
    await pool.drain_evictions()
    session.close.assert_awaited_once()


async def test_eviction_identity_check_spares_a_live_session_that_reused_the_id(ctx) -> None:
    """A late callback for a dead terminal must not evict its id's new owner.

    This is the scenario the seam claims to defend, and it needs two DIFFERENT
    sessions under one id to exercise: re-reading the same dict key and
    comparing the result to itself can never fail, so the callback has to carry
    the identity of the engine it belongs to.
    """
    pool = TerminalPool(ctx)
    try:
        result = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="alive")
        iid = result["instance_id"]
        live = pool.get(iid)

        # A stale callback from the terminal that held this id BEFORE `live`.
        dead_engine = SimpleNamespace()
        pool._evict_stopped(iid, dead_engine, "eof")

        assert pool.maybe_get(iid) is live, "stale callback evicted the live session"
        await pool.drain_evictions()
        assert pool.maybe_get(iid) is live, "stale callback tore down the live session"

        # Idempotent: the real owner's callback may fire twice (the launch-race
        # re-check and the poll loop both call it), and an unknown id is a no-op.
        pool._evict_stopped(iid, live.engine, "eof")
        pool._evict_stopped(iid, live.engine, "eof")
        pool._evict_stopped("never-existed", dead_engine, "eof")
        await pool.drain_evictions()
    finally:
        await pool.close_all(force=True)


async def test_deliberate_close_is_not_reported_as_an_eviction(ctx, caplog) -> None:
    """`terminal.evicted_on_stop` must mean "this died", not "this was closed".

    `pool.close` awaits `session.close()` BEFORE popping the registry entry, so
    the callback fires while the session is still registered and every ordinary
    close reached the eviction path. An operator grepping for dropped SSH
    connections got one hit per deliberate close and no way to tell them apart,
    and the session would have been torn down twice.
    """
    pool = TerminalPool(ctx)
    engine = SimpleNamespace()
    session = SimpleNamespace(connector_type="pty", engine=engine, close=AsyncMock())
    pool._sessions["bye"] = session  # type: ignore[assignment]

    with caplog.at_level("INFO"):
        pool._evict_stopped("bye", engine, "closed")
    await pool.drain_evictions()

    session.close.assert_not_awaited()
    assert "evicted_on_stop" not in caplog.text
    # Left registered on purpose: `pool.close` pops it after its own teardown.
    assert "bye" in pool._sessions


async def test_eviction_invalidates_the_dashboard(ctx, monkeypatch) -> None:
    """The dashboard is the stated motivation, so tell it the session is gone.

    `terminal_launch` and `terminal_close` both publish a `sessions`
    invalidation; without one here an open dashboard keeps rendering a dead
    terminal until its next `/api/sessions` poll.
    """
    published: list[str] = []
    monkeypatch.setattr(pool_module, "publish_dashboard_invalidation_nowait", published.append)

    pool = TerminalPool(ctx)
    engine = SimpleNamespace()
    pool._sessions["gone"] = SimpleNamespace(connector_type="pty", engine=engine, close=AsyncMock())  # type: ignore[assignment]

    pool._evict_stopped("gone", engine, "eof")
    await pool.drain_evictions()

    assert published == ["sessions"]


async def test_lookup_after_eviction_says_the_connector_died(ctx) -> None:
    """An evicted id must not answer like an id that never existed.

    Once the session is gone every `terminal_*` tool fails at pool lookup, so
    the lookup error is the only place left to say WHY -- otherwise the agent
    is told "no terminal session 'abc'" for a terminal it just watched work,
    and `send_input`'s "input was NOT delivered" guard is unreachable.
    """
    pool = TerminalPool(ctx)
    try:
        iid = (await pool.launch(kind="pty", connector_config={"command": TRUE_BIN}))["instance_id"]
        await _wait_until(lambda: pool.maybe_get(iid) is None)
        await pool.drain_evictions()

        with pytest.raises(KeyError) as exc_info:
            pool.get(iid)
        assert "eof" in str(exc_info.value)

        with pytest.raises(KeyError) as unknown_info:
            pool.get("never-existed")
        assert "eof" not in str(unknown_info.value)
    finally:
        await pool.close_all(force=True)
