# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``TerminalPool`` against the SessionPool contract, not against its old shape.

The pool used to build its own Recorder and return bare values. Core now owns
the launch transaction, so a launch that fails must leave no orphan recording
and a commit must go through ``SessionLaunch.commit`` -- which is also what
enforces cross-pool id uniqueness.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from octowright_terminal import pool as _pool_module
from octowright_terminal.pool import TerminalPool

from octowright.plugins.contract import SessionPool
from octowright.plugins.session_launch import PluginContext, SessionLaunch


@pytest.fixture
def ctx(tmp_path):
    # IdInUse is keyword-only on exclude_kind (see plugins/session_launch.py);
    # begin_session's commit path always calls it with that kwarg, so the
    # probe must accept it even when it ignores it.
    return PluginContext(kind="terminal", recordings_dir=tmp_path, id_in_use=lambda _id, **_: False)


def test_the_pool_satisfies_the_session_pool_protocol(ctx):
    pool = TerminalPool(ctx)
    # SessionPool is a plain (non-runtime_checkable) Protocol, so isinstance()
    # raises on it -- structural conformance is checked by name instead, the
    # same way tests/plugins/test_reference_plugin.py checks the reference
    # pool: every public callable SessionPool declares must exist on the pool.
    required = {name for name, value in vars(SessionPool).items() if not name.startswith("_") and callable(value)}
    missing = required - {name for name in dir(pool) if not name.startswith("_")}
    assert not missing, f"TerminalPool is missing SessionPool members: {sorted(missing)}"


@pytest.mark.asyncio
async def test_launch_returns_a_launch_result_with_the_contract_keys(ctx):
    pool = TerminalPool(ctx)
    result = await pool.launch(kind="pty", connector_config={"command": "/bin/sh"})
    try:
        assert result["kind"] == "terminal", "session kind is terminal; pty is the CONNECTOR type"
        assert result["instance_id"]
        assert result["log_path"]
    finally:
        await pool.close(result["instance_id"], force=True)


@pytest.mark.asyncio
async def test_close_returns_a_close_result(ctx):
    pool = TerminalPool(ctx)
    result = await pool.launch(kind="pty", connector_config={"command": "/bin/sh"})
    closed = await pool.close(result["instance_id"], force=True)
    assert closed["instance_id"] == result["instance_id"]
    assert closed["closed"] is True


@pytest.mark.asyncio
async def test_a_failed_launch_leaves_no_orphan_recording(ctx, tmp_path):
    pool = TerminalPool(ctx)
    # An SSH launch missing known_hosts is the reliable, already-exercised
    # failure mode (the connector raises ValueError synchronously in its
    # ctor) -- a nonexistent PTY command is not a dependable way to force a
    # pre-registration failure across platforms.
    with pytest.raises(ValueError, match="known_hosts"):
        await pool.launch(kind="ssh", connector_config={"host": "h", "username": "u"})
    # The transaction discards an opening-row-only recording. Any .jsonl left
    # behind here is the orphan the launch transaction exists to prevent.
    assert list(tmp_path.glob("*.jsonl")) == []


async def test_a_failing_commit_stops_the_engine_it_already_started(tmp_path) -> None:
    """`engine.start()` forks the PTY BEFORE `commit()` can refuse.

    Core's launch transaction discards the recording on failure, but it has no
    handle on the connector and the session is never registered anywhere that
    could close it -- so the pool has to stop what it started. Without this a
    refused launch leaves a live child process and a running poll task behind.
    """
    from octowright_terminal.plugin import plugin as terminal_plugin

    from octowright.plugins.registry import PluginRegistry
    from octowright.plugins.session_launch import PluginContext

    registry = PluginRegistry()
    ctx = PluginContext(kind="terminal", recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = terminal_plugin.create_pool(ctx)

    started: list[Any] = []
    real_engine_cls = _pool_module.TerminalEngine

    class _RecordingEngine(real_engine_cls):  # type: ignore[misc, valid-type]
        async def start(self) -> None:
            await super().start()
            started.append(self)

    boom = RuntimeError("commit refused")

    def _explode(_self: Any, _record: Any) -> Any:
        raise boom

    with (
        mock.patch.object(_pool_module, "TerminalEngine", _RecordingEngine),
        mock.patch.object(SessionLaunch, "commit", _explode),
        pytest.raises(RuntimeError, match="commit refused"),
    ):
        await pool.launch(kind="pty", connector_config={"command": "/bin/cat"})

    assert started, "the test did not reach engine.start(); it is not exercising the window"
    engine = started[0]
    assert not engine._connector.is_connected(), "the connector survived a failed launch"
    assert engine._poll_task is None or engine._poll_task.done(), "the poll task outlived the failed launch"
    assert pool.maybe_get(engine._instance_id) is None
