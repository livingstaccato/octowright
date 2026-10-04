# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's health probe follows the leader it is actually bridged to.

The reconnect loop re-resolves the leader URL from the lockfile on every
connect, so a follower whose leader came back on a different port (the
lifespan port-walk, 6286 -> 6287) reconnects there. The health URL, though,
was derived once from the FIRST leader's URL: the monitor kept probing the
dead 6286, declared the healthy 6287 leader down, and cancelled its session
every few seconds, indefinitely -- interrupting and re-sending every call
longer than the probe cycle.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage

from octowright import proxy_runtime as runtime

_OLD = "http://127.0.0.1:6286"
_NEW = "http://127.0.0.1:6287"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_probe_targets_the_reconnected_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda _fallback: f"{_NEW}/mcp/")
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(runtime, "resolve_leader_generation", lambda: "leader-B")
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot", lambda **_kwargs: True)
    monkeypatch.setattr(runtime, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    async def _no_notifications(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(runtime, "consume_leader_notifications", _no_notifications)

    probed: list[str] = []

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class _HClient:
        def __init__(self, **_k: Any) -> None: ...

        async def __aenter__(self) -> _HClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            probed.append(url)
            if url.startswith(_NEW):
                return _Resp(200)
            raise runtime.httpx2.ConnectError("connection refused")

    monkeypatch.setattr(runtime.httpx2, "AsyncClient", _HClient)

    connects = {"n": 0}

    @asynccontextmanager
    async def live_client(_url: str, **_k: Any):  # type: ignore[no-untyped-def]
        connects["n"] += 1
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, _ = anyio.create_memory_object_stream[SessionMessage](10)
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(runtime, "streamable_http_client", live_client)
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    async def run() -> None:
        await runtime.run_supervised_proxy(
            leader_mcp_url=f"{_OLD}/mcp/",
            health_url=f"{_OLD}/api/health",
            heartbeat_interval=0.02,
            heartbeat_max_failures=2,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        with anyio.fail_after(3.0):
            while len(probed) < 10:
                await anyio.sleep(0.02)
        tg.cancel_scope.cancel()
    await local_in_send.aclose()

    assert all(url == f"{_NEW}/api/health" for url in probed), probed
    assert connects["n"] == 1, "a healthy leader's session must not be torn down"
