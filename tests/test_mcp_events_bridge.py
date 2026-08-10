# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Option A — proactive notifications over the follower bridge (deterministic).

Two halves, wired together in production by ``session_event_bus`` → the leader's
``/api/mcp-events`` SSE (``http/routes/mcp_events``) → the follower's
``consume_leader_notifications`` (``proxy_runtime``) → the local client's stdio
write. Here we test the leader endpoint and the follower parser in-process,
without a live daemon (that end-to-end path is covered by
``tests/test_mcp_events_daemon_live.py``).
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest

from octowright.browser_pool.events import DriverDiedEvent, SessionClosedEvent, SessionCrashedEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.http.routes.mcp_events import mcp_events_endpoint
from octowright.proxy_runtime import _forward_sse_notifications


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── follower parser: _forward_sse_notifications ─────────────────────────────


class _Collector:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


async def _lines(*items: str) -> Any:
    for item in items:
        yield item


@pytest.mark.anyio
async def test_forward_sse_delivers_only_valid_notifications() -> None:
    collector = _Collector()
    await _forward_sse_notifications(
        _lines(
            ": ready",  # SSE comment → skipped
            "",  # blank separator → skipped
            'data: {"method": "notifications/octowright/session_closed", "params": {"instance_id": "a"}}',
            ": heartbeat",  # comment → skipped
            "data: {not json",  # malformed → skipped
            'data: {"params": {"x": 1}}',  # missing method → skipped
            'data: {"method": "notifications/octowright/browser_crashed", "params": {"instance_id": "b"}}',
        ),
        collector,
    )

    methods = [m.message.method for m in collector.sent]
    assert methods == [
        "notifications/octowright/session_closed",
        "notifications/octowright/browser_crashed",
    ]
    # params round-trip intact
    assert collector.sent[0].message.params == {"instance_id": "a"}


@pytest.mark.anyio
async def test_forward_sse_swallows_send_failure_and_continues() -> None:
    """A dead local client (send raises) must not kill the stream — later frames
    still process (the RPC bridge owns teardown)."""

    class _FlakyWrite:
        def __init__(self) -> None:
            self.ok: list[Any] = []
            self._first = True

        async def send(self, message: Any) -> None:
            if self._first:
                self._first = False
                raise RuntimeError("client gone")
            self.ok.append(message)

    write = _FlakyWrite()
    await _forward_sse_notifications(
        _lines(
            'data: {"method": "notifications/octowright/session_closed", "params": {"instance_id": "boom"}}',
            'data: {"method": "notifications/octowright/session_closed", "params": {"instance_id": "ok"}}',
        ),
        write,
    )
    assert [m.message.params["instance_id"] for m in write.ok] == ["ok"]


# ─── leader endpoint: /api/mcp-events ────────────────────────────────────────


class _FakeRequest:
    """Minimal Request stand-in for the SSE endpoint (only is_disconnected used)."""

    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


@pytest.mark.anyio
async def test_leader_endpoint_streams_event_as_notification_frame() -> None:
    request = _FakeRequest()
    response = await mcp_events_endpoint(request)  # type: ignore[arg-type]
    body = response.body_iterator

    # First chunk is the ": ready" comment — after it the subscription is active,
    # so an event published now is guaranteed to be seen.
    first = await body.__anext__()
    assert first == b": ready\n\n"

    session_event_bus.publish_nowait(
        SessionClosedEvent(
            instance_id="sid-1", kind="chromium", label="L", profile=None, reason="agent_close", log_path="/x.jsonl"
        )
    )

    payload: dict[str, Any] | None = None
    with anyio.fail_after(5):
        while payload is None:
            chunk = await body.__anext__()
            if chunk.startswith(b"data:"):
                payload = json.loads(chunk[len(b"data:") :].strip())

    assert payload["method"] == "notifications/octowright/session_closed"
    assert payload["params"]["instance_id"] == "sid-1"
    assert payload["params"]["reason"] == "agent_close"

    request.disconnected = True
    await body.aclose()


@pytest.mark.anyio
async def test_leader_endpoint_streams_crash_and_driver_events() -> None:
    request = _FakeRequest()
    response = await mcp_events_endpoint(request)  # type: ignore[arg-type]
    body = response.body_iterator
    assert (await body.__anext__()) == b": ready\n\n"

    session_event_bus.publish_nowait(
        SessionCrashedEvent(
            instance_id="c1",
            kind="firefox",
            label=None,
            profile=None,
            scope="renderer",
            log_path="/c.jsonl",
            recovering=True,
        )
    )
    session_event_bus.publish_nowait(
        DriverDiedEvent(restart_count=1, relaunch_mode="off", lost_count=2, lost_instance_ids=("c1", "c2"))
    )

    methods: list[str] = []
    with anyio.fail_after(5):
        while len(methods) < 2:
            chunk = await body.__anext__()
            if chunk.startswith(b"data:"):
                methods.append(json.loads(chunk[len(b"data:") :].strip())["method"])

    assert methods == [
        "notifications/octowright/browser_crashed",
        "notifications/octowright/driver_died",
    ]
    request.disconnected = True
    await body.aclose()
