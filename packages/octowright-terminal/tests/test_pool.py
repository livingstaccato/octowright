# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from octowright_terminal.errors import ProtectedTerminalCloseError
from octowright_terminal.pool import TerminalPool

from octowright.plugins.session_launch import PluginContext

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


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
        # /bin/true exits immediately, so the poll loop sees a disconnected
        # connector and records an "eof" stop without any help from the test.
        result = await pool.launch(kind="pty", connector_config={"command": "/bin/true"}, label="dies")
        iid = result["instance_id"]

        for _ in range(100):
            if pool.maybe_get(iid) is None:
                break
            await asyncio.sleep(0.05)

        assert pool.maybe_get(iid) is None, "dead terminal still resolvable"
        assert [s["instance_id"] for s in pool.list_sessions()] == []
        assert list(pool.iter_sessions()) == []
    finally:
        await pool.close_all(force=True)


async def test_eviction_is_identity_checked_and_idempotent(ctx) -> None:
    """A late callback must not evict a live session that reused the id.

    ``pop(instance_id)`` alone would; the seam compares identity first. Also
    covers the launch-race path calling ``_evict_stopped`` a second time.
    """
    pool = TerminalPool(ctx)
    try:
        result = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="alive")
        iid = result["instance_id"]
        live = pool.get(iid)

        # Impersonate a stale callback for a DIFFERENT session that once held
        # this id: identity differs, so the live entry must survive.
        pool._sessions[iid] = live
        stale = SimpleNamespace(connector_type="pty")
        pool._sessions["ghost"] = stale
        pool._evict_stopped("ghost")
        assert "ghost" not in pool._sessions
        pool._evict_stopped("ghost")  # idempotent: second call is a no-op
        assert pool.maybe_get(iid) is live

        pool._evict_stopped("never-existed")  # unknown id is a no-op
    finally:
        await pool.close_all(force=True)
