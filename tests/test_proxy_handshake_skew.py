# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A follower records when the handshake its client holds is not the leader's.

A follower answers ``initialize`` itself while it elects a leader, from its own
``mcp_identity``: a follower older than the leader hands its client its own
instructions and capabilities, not the leader's. A client that kept one
handshake across a leader restart holds the old leader's. Each connect replays
the handshake and gets the current leader's answer, so that is where the two
are compared; the difference goes into the bridge snapshot and from there into
``octowright_status()["bridge"]["summary"]``.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCResponse

from octowright import mcp_identity
from octowright import proxy_runtime as runtime
from tests.test_proxy_early_handshake import (
    _INIT_PARAMS,
    _URL,
    _Election,
    _initialize,
    _initialized,
    _Leader,
    _start,
    _wire,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _capture_snapshots(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []

    async def _snapshot(**kwargs: Any) -> bool:
        snapshots.append(kwargs)
        return True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)
    return snapshots


async def _connect(leader: _Leader, index: int, answer: dict[str, Any]) -> tuple[Any, Any]:
    """Play the leader for connect ``index``: answer the replay with ``answer``."""
    await leader.wait_for(index + 1)
    to_leader, from_leader = leader.sessions[index][1], leader.sessions[index][0]
    replay = (await to_leader.receive()).message
    await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result=answer)))
    assert (await to_leader.receive()).message.method == "notifications/initialized"
    return to_leader, from_leader


async def _wait_for_snapshot(snapshots: list[dict[str, Any]], count: int) -> dict[str, Any]:
    while len(snapshots) < count:
        await anyio.sleep(0.01)
    return snapshots[count - 1]


@pytest.mark.anyio
async def test_a_leader_answering_as_the_follower_did_is_no_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    snapshots = _capture_snapshots(monkeypatch)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            election.decide(_URL)
            await _connect(leader, 0, mcp_identity.local_initialize_result(_INIT_PARAMS) or {})
            snapshot = await _wait_for_snapshot(snapshots, 1)
        assert snapshot["handshake_mismatch"] == []
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_follower_that_answered_with_other_instructions_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    snapshots = _capture_snapshots(monkeypatch)
    election = _Election()
    newer = {**(mcp_identity.local_initialize_result(_INIT_PARAMS) or {}), "instructions": "a newer leader's text"}
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            election.decide(_URL)
            await _connect(leader, 0, newer)
            snapshot = await _wait_for_snapshot(snapshots, 1)
        assert snapshot["handshake_mismatch"] == ["instructions"]
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_handshake_kept_across_a_leader_restart_is_compared_with_the_new_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client's initialize went to the first leader, which answered it; the
    leader it reconnects to answers differently."""
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    snapshots = _capture_snapshots(monkeypatch)
    election = _Election()
    first = mcp_identity.local_initialize_result(_INIT_PARAMS) or {}
    second = {
        **first,
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": "octowright", "version": "9"},
    }
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(10.0):
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            await local_in.send(_initialize("init-9"))
            forwarded = (await to_leader.receive()).message
            assert forwarded.id == "init-9"  # no leader-less phase left: it goes to the leader
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id="init-9", result=first)))
            assert (await local_out.receive()).message.result == first
            await local_in.send(_initialized())
            await to_leader.receive()
            await from_leader.aclose()  # the leader restarts
            await _connect(leader, 1, second)
            snapshot = await _wait_for_snapshot(snapshots, 2)  # the first connect, then the reconnect
        assert snapshot["handshake_mismatch"] == ["capabilities", "serverInfo"]
        tg.cancel_scope.cancel()
