# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCResponse

from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import (
    _request,
    _response,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_concurrent_response_and_timeout_emits_single_frame() -> None:
    """If a response arrives in the same tick as deadline expiry, the watchdog
    must not emit a second bridge_error after the real response is delivered."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.05,
    )

    request_id = "race-id"
    supervisor_obj.track_local_message(_request("tools/call", request_id))

    # Drive the in-flight entry past its deadline so the watchdog would mark
    # it expired on the next wake. Then deliver the real response first; the
    # watchdog must not emit a second frame for the same request id.
    in_flight = supervisor_obj._in_flight[request_id]
    in_flight.deadline = -1.0

    await supervisor_obj.forward_remote_message(_response(request_id))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.watch_deadlines, 0.01)
        with anyio.move_on_after(0.2):
            await anyio.sleep_forever()
        tg.cancel_scope.cancel()

    frames: list[SessionMessage] = []
    while True:
        with anyio.move_on_after(0.01):
            frames.append(await outgoing_recv.receive())
            continue
        break

    assert len(frames) == 1
    assert supervisor.message_request_id(frames[0]) == request_id
    assert isinstance(frames[0].message.root, JSONRPCResponse)


@pytest.mark.anyio
async def test_responded_flag_blocks_double_response_when_id_reused() -> None:
    """Direct check: once an in-flight entry is marked responded, neither path
    sends another frame for it. Exercises the dataclass invariant in isolation."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "dup-id"))
    in_flight = supervisor_obj._in_flight["dup-id"]

    # Pretend the response already shipped.
    in_flight.responded = True

    await supervisor_obj.fail_all_in_flight("would-be second frame")

    with anyio.move_on_after(0.05):
        frame = await outgoing_recv.receive()
        raise AssertionError(f"unexpected frame after responded=True: {frame!r}")


@pytest.mark.anyio
async def test_run_supervised_proxy_session_survives_past_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected session must not be cancelled at BRIDGE_CONNECT_TIMEOUT_SECONDS.

    The bug: ``with anyio.fail_after(...)`` previously wrapped the whole
    ``async with streamablehttp_client(...)`` block, so the body (the read
    loop, the inner task group) was also subject to the connect deadline.
    Active sessions died every BRIDGE_CONNECT_TIMEOUT_SECONDS and reconnected,
    failing every in-flight request. The fix scopes the deadline to only the
    ``__aenter__`` handshake.
    """
    # Force a tiny connect timeout so the test runs fast. The session, once
    # entered, must outlive this deadline; the read loop is unconstrained.
    monkeypatch.setattr(runtime, "BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.05)

    fake_remote_read_send, fake_remote_read_recv = anyio.create_memory_object_stream[SessionMessage](10)
    fake_remote_write_send, fake_remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)

    enters: list[float] = []
    exits: list[float] = []

    @asynccontextmanager
    async def fake_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        enters.append(__import__("time").monotonic())
        try:
            yield (fake_remote_read_recv, fake_remote_write_send, lambda: "sess-1")
        finally:
            exits.append(__import__("time").monotonic())

    monkeypatch.setattr(runtime, "streamablehttp_client", fake_client)
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)

    # No-op snapshot writer to avoid hitting disk.
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot", lambda **_kwargs: None)

    # Stub stdio_server so the supervisor uses our in-memory streams.
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    async with anyio.create_task_group() as tg:

        async def _runner() -> None:
            await runtime.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp")

        tg.start_soon(_runner)

        # Wait long enough that the OLD code would have cancelled the session
        # at the connect deadline (0.05s). With the fix, the session stays up.
        await anyio.sleep(0.3)

        # If the fix is in place: exactly one enter, no exit yet.
        assert len(enters) == 1, f"expected one connect, saw {len(enters)} (reconnect storm)"
        assert exits == [], f"session should still be open, but it exited: {exits}"

        tg.cancel_scope.cancel()

    # Quiet the unused stream warnings.
    await fake_remote_read_send.aclose()
    await fake_remote_write_recv.aclose()
    await local_in_send.aclose()
