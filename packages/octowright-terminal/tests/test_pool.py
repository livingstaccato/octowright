# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

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
