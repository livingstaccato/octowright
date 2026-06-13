# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

import pytest

from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.pool import TerminalPool

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_launch_registers_and_lists_session() -> None:
    pool = TerminalPool()
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


async def test_get_and_maybe_get() -> None:
    pool = TerminalPool()
    try:
        iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}))["instance_id"]
        assert pool.get(iid).instance_id == iid
        assert pool.maybe_get(iid) is not None
        assert pool.maybe_get("nope") is None
        with pytest.raises(KeyError):
            pool.get("nope")
    finally:
        await pool.close_all(force=True)


async def test_close_refuses_protected_without_force() -> None:
    pool = TerminalPool()
    iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, protected=True))["instance_id"]
    try:
        with pytest.raises(ProtectedTerminalCloseError):
            await pool.close(iid)
        # still present after refused close
        assert pool.maybe_get(iid) is not None
    finally:
        await pool.close(iid, force=True)
    assert pool.maybe_get(iid) is None
