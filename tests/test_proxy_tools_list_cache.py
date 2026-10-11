# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A ``tools/list`` that waits too long for an election is answered from the
last answer a leader gave (``octowright.proxy_tools_cache``).

The copy answers only after ``GRACE_SECONDS`` with no leader, only while the
leader is still being elected, and only when it was kept under the same
version, profile, plugins and protocol version. Otherwise the call waits for
the leader as it always has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCRequest, JSONRPCResponse

from octowright import defaults, proxy_tools_cache
from tests.test_proxy_early_handshake import _URL, _Election, _initialize, _initialized, _Leader, _start, _wire

_HANDSHAKE = {"protocolVersion": "2025-06-18"}
_TOOLS = {"tools": [{"name": "octowright_status", "inputSchema": {"type": "object"}}]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "tools-list-cache.json"
    monkeypatch.setattr(defaults, "TOOLS_LIST_CACHE_PATH", path)
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    monkeypatch.delenv("OCTOWRIGHT_PLUGINS", raising=False)
    return path


def _list(request_id: str = "list-1", params: dict[str, Any] | None = None) -> SessionMessage:
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method="tools/list", params=params or {}))


def _answer(result: dict[str, Any], request_id: str = "list-1") -> SessionMessage:
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result))


def test_a_kept_answer_is_given_back_under_the_new_request_id(cache_path: Path) -> None:
    proxy_tools_cache.remember(_list("old"), _answer(_TOOLS, "old"), _HANDSHAKE)
    reply = proxy_tools_cache.cached_answer(_list("new"), _HANDSHAKE)
    assert reply is not None
    assert isinstance(reply.message, JSONRPCResponse)
    assert reply.message.id == "new"
    assert reply.message.result == _TOOLS


@pytest.mark.parametrize(
    ("change", "value"),
    [("OCTOWRIGHT_PROFILE", "core"), ("OCTOWRIGHT_PLUGINS", "terminal"), ("version", "0.0.0-other")],
)
def test_a_copy_kept_under_other_conditions_is_not_used(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, value: str
) -> None:
    proxy_tools_cache.remember(_list(), _answer(_TOOLS), _HANDSHAKE)
    if change == "version":
        monkeypatch.setattr(proxy_tools_cache, "VERSION", value)
    else:
        monkeypatch.setenv(change, value)
    assert proxy_tools_cache.cached_answer(_list(), _HANDSHAKE) is None


def test_a_copy_kept_for_another_protocol_version_is_not_used(cache_path: Path) -> None:
    proxy_tools_cache.remember(_list(), _answer(_TOOLS), _HANDSHAKE)
    assert proxy_tools_cache.cached_answer(_list(), {"protocolVersion": "2025-03-26"}) is None


def test_only_a_whole_first_page_is_kept_or_answered(cache_path: Path) -> None:
    proxy_tools_cache.remember(_list(params={"cursor": "c2"}), _answer(_TOOLS), _HANDSHAKE)
    proxy_tools_cache.remember(_list(), _answer({**_TOOLS, "nextCursor": "c2"}), _HANDSHAKE)
    assert not cache_path.exists()
    proxy_tools_cache.remember(_list(), _answer(_TOOLS), _HANDSHAKE)
    assert proxy_tools_cache.cached_answer(_list(params={"cursor": "c2"}), _HANDSHAKE) is None
    assert proxy_tools_cache.cached_answer(_list(), _HANDSHAKE) is not None


def test_an_error_answer_or_an_unknown_handshake_keeps_nothing(cache_path: Path) -> None:
    error = SessionMessage(JSONRPCError(jsonrpc="2.0", id="list-1", error={"code": -1, "message": "no"}))
    proxy_tools_cache.remember(_list(), error, _HANDSHAKE)
    proxy_tools_cache.remember(_list(), _answer(_TOOLS), None)
    assert not cache_path.exists()


def test_an_unreadable_copy_answers_nothing(cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("{not json", encoding="utf-8")
    assert proxy_tools_cache.cached_answer(_list(), _HANDSHAKE) is None
    cache_path.write_text(json.dumps({"key": {}, "result": "nope"}), encoding="utf-8")
    assert proxy_tools_cache.cached_answer(_list(), _HANDSHAKE) is None


async def _handshake(local_in: Any, local_out: Any) -> None:
    await local_in.send(_initialize())
    await local_out.receive()  # answered locally
    await local_in.send(_initialized())


@pytest.mark.anyio
async def test_a_list_that_waits_out_the_grace_is_answered_from_the_copy(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proxy_tools_cache, "GRACE_SECONDS", 0.2)
    proxy_tools_cache.remember(_list(), _answer(_TOOLS), {"protocolVersion": "2025-06-18"})
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            await _handshake(local_in, local_out)
            await local_in.send(_list("list-9"))
            reply = (await local_out.receive()).message
        assert isinstance(reply, JSONRPCResponse)
        assert reply.id == "list-9"
        assert reply.result == _TOOLS
        assert leader.sessions == []  # the election never finished
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_with_no_copy_the_list_keeps_waiting_and_its_answer_is_kept(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proxy_tools_cache, "GRACE_SECONDS", 0.1)
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            await _handshake(local_in, local_out)
            await local_in.send(_list("list-1"))
            await anyio.sleep(0.4)  # well past the grace
            with pytest.raises(anyio.WouldBlock):
                local_out.receive_nowait()
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            assert (await to_leader.receive()).message.id == "list-1"
            await from_leader.send(_answer(_TOOLS))
            reply = (await local_out.receive()).message
        assert reply.id == "list-1"
        assert reply.result == _TOOLS
        kept = proxy_tools_cache.cached_answer(_list("again"), {"protocolVersion": "2025-06-18"})
        assert kept is not None and kept.message.result == _TOOLS
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_leader_that_arrives_within_the_grace_answers_the_list(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proxy_tools_cache, "GRACE_SECONDS", 2.0)
    proxy_tools_cache.remember(_list(), _answer({"tools": []}), {"protocolVersion": "2025-06-18"})
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            await _handshake(local_in, local_out)
            await local_in.send(_list("list-1"))
            await anyio.sleep(0.1)
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            await to_leader.receive()  # notifications/initialized
            assert (await to_leader.receive()).message.id == "list-1"
            await from_leader.send(_answer(_TOOLS))
            reply = (await local_out.receive()).message
        assert reply.result == _TOOLS  # the leader's, not the copy
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_reconnect_never_answers_from_the_copy(cache_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the election is over the copy is out of play: a list waiting on a
    reconnect waits for the leader, as before."""
    monkeypatch.setattr(proxy_tools_cache, "GRACE_SECONDS", 0.05)
    proxy_tools_cache.remember(_list(), _answer({"tools": []}), {"protocolVersion": "2025-06-18"})
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(10.0):
            await _handshake(local_in, local_out)
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            await to_leader.receive()  # notifications/initialized
            await from_leader.aclose()  # the leader stream drops: a reconnect follows
            await anyio.sleep(0.1)
            await local_in.send(_list("list-1"))
            await anyio.sleep(0.4)  # well past the grace
            with pytest.raises(anyio.WouldBlock):
                local_out.receive_nowait()
            await leader.wait_for(2)
            to_leader2, from_leader2 = leader.sessions[1][1], leader.sessions[1][0]
            replay2 = (await to_leader2.receive()).message
            await from_leader2.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay2.id, result={})))
            await to_leader2.receive()  # notifications/initialized
            assert (await to_leader2.receive()).message.id == "list-1"
        tg.cancel_scope.cancel()
