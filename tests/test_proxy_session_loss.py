# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower bridge reads the leader's HTTP status itself.

mcp's streamable-HTTP client does not raise for a refused POST: a 404 on a
session the leader forgot becomes a ``-32600 Session not found`` answer, a
403 or a 429 with a non-JSON-RPC body a ``-32603`` one, and the transport
stays open. Before the response hook (``proxy_session_loss``) the bridge
forwarded those answers forever and never reconnected, and a throttled
reconnect was classified as an ordinary failed connect.

The end-to-end tests here go through the REAL ``streamable_http_client``
against an in-process leader (an ``httpx2.MockTransport``), because the
harness the throttle tests use -- a fake transport that raises
``HTTPStatusError`` -- is exactly the behaviour the real client lacks.
"""

from __future__ import annotations

import functools
import json
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import _trace_propagation, proxy_retry, proxy_session_loss
from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _request, _tools_call

LEADER_URL = "http://127.0.0.1:6299/mcp/"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _json(status: int, payload: Any, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, json=payload, headers=headers or {})


class _Leader:
    """A minimal streamable-HTTP leader: JSON answers, numbered sessions, and
    sessions it can be told to forget (``lost``) or to refuse (``refusal``)."""

    def __init__(self, *, refusal: httpx2.Response | None = None, new_session: httpx2.Response | None = None) -> None:
        self.sessions = 0
        self.lost: set[str] = set()
        self.refusal = refusal or _json(
            404, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Session not found"}}
        )
        # When set, every session-creating POST is answered with it (the flap guard).
        self.new_session = new_session
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "DELETE":
            return httpx2.Response(200)
        session_id = request.headers.get("mcp-session-id")
        body = json.loads(request.content)
        if session_id is None:
            if self.new_session is not None:
                return self.new_session
            self.sessions += 1
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "octowright", "version": "test"},
            }
            return _json(
                200, {"jsonrpc": "2.0", "id": body["id"], "result": result}, {"mcp-session-id": f"s{self.sessions}"}
            )
        if session_id in self.lost:
            return self.refusal
        if "id" not in body:
            return httpx2.Response(202)
        self.calls.append((session_id, body["method"]))
        answer = {"content": [{"type": "text", "text": f"ran on {session_id}"}], "isError": False}
        return _json(200, {"jsonrpc": "2.0", "id": body["id"], "result": answer})


class _SeededSupervisor(supervisor.BridgeSupervisor):
    """A supervisor whose client already initialized, so every connect replays
    the handshake as a reconnect does."""

    instances: list[_SeededSupervisor] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._initialize_message = SessionMessage(
            JSONRPCRequest(
                jsonrpc="2.0",
                id="client-init",
                method="initialize",
                params={
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        )
        self._initialized_message = SessionMessage(
            JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized")
        )
        _SeededSupervisor.instances.append(self)


def _wire(monkeypatch: pytest.MonkeyPatch, leader: _Leader) -> tuple[Any, Any, list[dict[str, Any]]]:
    transport = httpx2.MockTransport(leader.handler)
    monkeypatch.setattr(
        proxy_session_loss,
        "build_tracing_http_client",
        functools.partial(_trace_propagation.build_tracing_http_client, transport=transport),
    )
    _SeededSupervisor.instances = []
    monkeypatch.setattr(runtime, "BridgeSupervisor", _SeededSupervisor)
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    # A different leader process per session, as after a restart: a refused
    # call must still be re-sent, not failed as "the leader was replaced".
    monkeypatch.setattr(runtime, "resolve_leader_generation", lambda: f"leader-{leader.sessions}")
    monkeypatch.setattr(runtime, "reconnect_delay", lambda attempt, *, max_delay: 0.0)

    async def _leader_version(_url: Any) -> str:
        return "0.26.0-test"

    monkeypatch.setattr(runtime, "_leader_health_version", _leader_version)

    async def _no_notifications(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(runtime, "consume_leader_notifications", _no_notifications)
    snapshots: list[dict[str, Any]] = []

    async def _snapshot(**kwargs: Any) -> bool:
        snapshots.append(kwargs)
        return True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](20)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](20)

    @asynccontextmanager
    async def fake_stdio() -> Any:
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)
    return local_in_send, local_out_recv, snapshots


async def _until(predicate: Any, *, timeout: float = 10.0) -> None:
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.01)


def _root(message: SessionMessage) -> Any:
    return getattr(message.message, "root", message.message)


async def _call_after_session_loss(monkeypatch: pytest.MonkeyPatch, leader: _Leader, call: SessionMessage) -> Any:
    local_in, local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=LEADER_URL))
        await _until(lambda: leader.sessions == 1 and len(snapshots) >= 1)
        leader.lost.add("s1")
        await local_in.send(call)
        with anyio.fail_after(10.0):
            response = await local_out.receive()
        tg.cancel_scope.cancel()
    return _root(response)


@pytest.mark.anyio
async def test_a_call_on_a_forgotten_session_reconnects_and_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    root = await _call_after_session_loss(monkeypatch, leader, _tools_call("octowright_status", "call-1"))
    assert isinstance(root, JSONRPCResponse), f"the client got the refusal instead of an answer: {root!r}"
    assert root.id == "call-1"
    assert root.result["content"][0]["text"] == "ran on s2"
    assert leader.sessions == 2
    assert leader.calls == [("s2", "tools/call")]
    assert _SeededSupervisor.instances[0].last_error is None  # cleared by the answered reconnect


@pytest.mark.anyio
async def test_a_rotated_token_403_is_a_lost_session_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restarted leader rotates the bridge token: the old session's POST is
    refused 403 with a plain-text body (-32603 from the client). The call
    never ran, so it is re-sent on the new leader even though that is a
    different process -- and even when it is not a keyed tools/call."""
    leader = _Leader(refusal=httpx2.Response(403, text="missing or invalid X-Octowright-Token"))
    root = await _call_after_session_loss(monkeypatch, leader, _request("tools/list", "list-1"))
    assert isinstance(root, JSONRPCResponse), f"the client got the refusal instead of an answer: {root!r}"
    assert root.id == "list-1"
    assert leader.calls == [("s2", "tools/list")]


@pytest.mark.anyio
async def test_a_real_429_on_the_replayed_handshake_is_classified_as_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = _json(
        429,
        {"error": "new-session rate limit exceeded; retry after backoff", "source": "1", "retry_after_seconds": 0.05},
        {"retry-after": "0.05"},
    )
    leader = _Leader(new_session=refusal)
    _local_in, _local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=LEADER_URL))
        await _until(lambda: len(snapshots) >= 1)
        started = anyio.current_time()
        await _until(lambda: len(snapshots) >= 3)
        elapsed = anyio.current_time() - started
        tg.cancel_scope.cancel()
    first = snapshots[0]["last_error"]
    assert "leader throttled this follower" in first, first
    assert "HTTP 429" in first
    assert "the leader's Retry-After" in first
    assert elapsed >= 2 * 0.05 * 0.8, f"Retry-After was not honored (2 cycles in {elapsed:.3f}s)"


@pytest.mark.anyio
async def test_a_real_410_on_the_replayed_handshake_says_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = _json(410, {"error": "follower refused"}, {"retry-after": "0.05"})
    leader = _Leader(new_session=refusal)
    _local_in, _local_out, snapshots = _wire(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=LEADER_URL))
        await _until(lambda: len(snapshots) >= 1)
        tg.cancel_scope.cancel()
    assert "leader refused to serve this follower" in snapshots[0]["last_error"]


# -- unit level ---------------------------------------------------------------


def _supervisor() -> tuple[supervisor.BridgeSupervisor, Any, list[int]]:
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=out_send, request_timeout_seconds=20.0)
    drops: list[int] = []
    sup.on_session_lost = lambda: drops.append(1)
    sup.remote_session_id = "s1"
    return sup, out_recv, drops


def _response(status: int, *, session: str | None, method: str = "POST", body: Any = None, **kw: Any) -> Any:
    headers = {"mcp-session-id": session} if session is not None else {}
    content = json.dumps(body).encode() if body is not None else b""
    request = httpx2.Request(method, LEADER_URL, headers=headers, content=content)
    return httpx2.Response(status, request=request, **kw)


_CALL = {"jsonrpc": "2.0", "id": "r1", "method": "tools/list"}


@pytest.mark.anyio
async def test_only_the_current_session_can_be_lost() -> None:
    sup, _out, drops = _supervisor()
    sup.track_local_message(_request("tools/list", "r1"))
    await proxy_session_loss.watch_response(sup, _response(404, session="s0", body=_CALL))
    await proxy_session_loss.watch_response(sup, _response(404, session=None, body=_CALL))
    await proxy_session_loss.watch_response(sup, _response(404, session="s1", method="DELETE"))
    await proxy_session_loss.watch_response(sup, _response(500, session="s1", body=_CALL))
    # A 404 that answers a request id is an answer (an unknown method), not a lost session.
    answer = {"jsonrpc": "2.0", "id": "r1", "error": {"code": -32601, "message": "Method not found"}}
    await proxy_session_loss.watch_response(sup, _response(404, session="s1", body=_CALL, json=answer))
    assert sup.session_lost is None and drops == []
    assert not sup._in_flight["r1"].rejected_unrun


@pytest.mark.anyio
async def test_a_lost_session_flags_its_requests_and_drops_once() -> None:
    sup, out, drops = _supervisor()
    sup.track_local_message(_request("tools/list", "r1"))
    sup.track_local_message(_request("resources/list", "r2"))
    sup._in_flight["r1"].delivered = True
    await proxy_session_loss.watch_response(sup, _response(404, session="s1", body=_CALL, text="gone"))
    other = {"jsonrpc": "2.0", "id": "r2", "method": "resources/list"}
    await proxy_session_loss.watch_response(sup, _response(403, session="s1", body=[other]))
    assert drops == [1]
    assert sup.session_lost is not None and "HTTP 404" in sup.session_lost
    assert sup._in_flight["r1"].rejected_unrun and not sup._in_flight["r1"].delivered
    assert sup._in_flight["r2"].rejected_unrun
    # The transport's error answer for a refused request is swallowed ...
    refusal = JSONRPCError(jsonrpc="2.0", id="r1", error=ErrorData(code=-32600, message="Session not found"))
    await sup.forward_remote_message(SessionMessage(refusal))
    assert "r1" in sup._in_flight
    # ... the reset keeps it although it is not a keyed tools/call ...
    await sup.fail_or_mark_for_resume("remote leader session ended")
    assert set(sup._in_flight) == {"r1", "r2"}
    # ... and the next session re-sends it.
    sent: list[SessionMessage] = []

    class _Writer:
        async def send(self, message: SessionMessage) -> None:
            sent.append(message)

    await sup.resume_in_flight(_Writer())
    assert [_root(m).id for m in sent] == ["r1", "r2"]
    assert not sup._in_flight["r1"].rejected_unrun and sup._in_flight["r1"].delivered
    with anyio.move_on_after(0.05):
        leaked = await out.receive()
        pytest.fail(f"a refusal reached the client: {leaked!r}")


def test_an_observed_refusal_classifies_a_statusless_connect_failure() -> None:
    handshake = supervisor.ReplayHandshakeError("the leader refused the replayed initialize")
    throttled = proxy_retry.classify_connect_error(handshake, (429, "2"))
    assert throttled == proxy_retry.ConnectFailure(proxy_retry.KIND_THROTTLED, status=429, retry_after=2.0)
    refused = proxy_retry.classify_connect_error(handshake, (410, None))
    assert refused.kind == proxy_retry.KIND_REFUSED
    assert proxy_retry.classify_connect_error(handshake, None).kind == proxy_retry.KIND_ERROR
    assert proxy_retry.classify_connect_error(handshake, (403, None)).kind == proxy_retry.KIND_HTTP_ERROR


@pytest.mark.anyio
async def test_the_hook_records_a_new_session_refusal_and_resets_per_connect() -> None:
    sup, _out, _drops = _supervisor()
    client = proxy_session_loss.bridge_http_client({}, sup)
    await client.aclose()
    assert sup.connect_refusal is None and sup.remote_session_id is None
    await proxy_session_loss.watch_response(sup, _response(429, session=None, headers={"retry-after": "3"}))
    assert sup.connect_refusal == (429, "3")
    # A 429 on an existing session is not the new-session limit.
    sup.connect_refusal = None
    await proxy_session_loss.watch_response(sup, _response(429, session="s1"))
    assert sup.connect_refusal is None
