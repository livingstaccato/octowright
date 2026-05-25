# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for demo/playground/server.py — the demo playground.

These tests cover the unit-of-behaviour:
- canvas tile claim updates state and broadcasts
- form-step append updates state and broadcasts
- reset clears state
- SSE delivers snapshot then live events
- bind-port-busy is a clean RuntimeError, not sys.exit(1)

Real recording smoke tests live in scripts/demos/ and require browsers; this
test file exercises only the HTTP plumbing so it runs in CI without browsers.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest

# The server module is out-of-wheel (demo/playground/server.py). The tests
# directory's conftest already inserts the repo root into sys.path for the
# demo-bundle tests, so this import resolves the same way.
from demo.playground.server import PlaygroundServer, _State

pytestmark = pytest.mark.integration_local


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    """Bind to port 0 to discover a free port, then release it. Race window
    is fine for a serialised test suite."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _start_server() -> PlaygroundServer:
    s = PlaygroundServer(port=_free_port())
    await s.start()
    return s


@pytest.mark.anyio
async def test_index_serves_static_html() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + "/")
        assert r.status_code == 200
        assert "Octowright Test Range" in r.text
        assert "/assets/octowright-logo-512.png" in r.text
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_logo_asset_served_from_docs_image() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + "/assets/octowright-logo-512.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_favicon_alias_serves_logo_without_404() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + "/favicon.ico")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
    finally:
        await s.stop()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/dialog-lab.html", "Dialog & Popup Lab"),
        ("/frame-lab.html", "Frame Lab"),
        ("/network-lab.html", "Network Lab"),
        ("/download-bay.html", "Download Bay"),
        ("/storage-console.html", "Storage Console"),
        ("/external.html", "External Launchpad"),
    ],
)
async def test_showcase_pages_are_served(path: str, expected: str) -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + path)
        assert r.status_code == 200
        assert expected in r.text
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_state_starts_empty() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + "/api/state")
        state = r.json()
        assert state["canvas"][0][0] is None
        assert state["form_steps"] == []
        assert state["events"] == []
        assert len(state["canvas"]) == _State.GRID_SIZE
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_claim_tile_updates_state() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.post(
                s.url + "/api/claim",
                json={"row": 2, "col": 3, "colour": "#abc", "claimed_by": "p1"},
            )
            assert r.status_code == 200
            assert r.json()["event"] == "tile_claimed"
            state = (await client.get(s.url + "/api/state")).json()
            assert state["canvas"][2][3] == "#abc"
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_claim_tile_out_of_bounds_returns_400() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.post(
                s.url + "/api/claim",
                json={"row": 99, "col": 0, "colour": "#fff", "claimed_by": "p1"},
            )
        assert r.status_code == 400
        assert "out of bounds" in r.json()["error"]
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_form_step_appended() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            await client.post(
                s.url + "/api/form-step",
                json={"step": 1, "label": "name", "value": "Tim"},
            )
            await client.post(
                s.url + "/api/form-step",
                json={"step": 2, "label": "email", "value": "tim@octowright.test"},
            )
            state = (await client.get(s.url + "/api/state")).json()
        assert len(state["form_steps"]) == 2
        assert state["form_steps"][0] == {"step": 1, "label": "name", "value": "Tim"}
        assert state["form_steps"][1]["label"] == "email"
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_event_appended_to_shared_log() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.post(
                s.url + "/api/event",
                json={"source": "network-lab", "kind": "ping", "message": "GET /api/ping 200"},
            )
            assert r.status_code == 200
            assert r.json()["event"] == "log_event"
            state = (await client.get(s.url + "/api/state")).json()
        assert state["events"] == [{"source": "network-lab", "kind": "ping", "message": "GET /api/ping 200"}]
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_ping_echo_and_error_endpoints_are_deterministic() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            ping = await client.get(s.url + "/api/ping")
            echo = await client.post(s.url + "/api/echo", json={"value": "octowright"})
            err = await client.get(s.url + "/api/error")
        assert ping.json() == {"ok": True, "service": "octowright-playground"}
        assert echo.json() == {"ok": True, "echo": {"value": "octowright"}}
        assert err.status_code == 418
        assert err.json()["error"] == "intentional playground error"
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_download_report_endpoint_returns_csv_attachment() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            r = await client.get(s.url + "/api/download/report.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert "browser,action,status" in r.text
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_reset_clears_state() -> None:
    s = await _start_server()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            await client.post(
                s.url + "/api/claim",
                json={"row": 0, "col": 0, "colour": "#000", "claimed_by": "p1"},
            )
            await client.post(
                s.url + "/api/form-step",
                json={"step": 1, "label": "x", "value": "y"},
            )
            await client.post(
                s.url + "/api/event",
                json={"source": "dialog-lab", "kind": "dialog", "message": "alert opened"},
            )
            await client.post(s.url + "/api/reset")
            state = (await client.get(s.url + "/api/state")).json()
        assert state["canvas"][0][0] is None
        assert state["form_steps"] == []
        assert state["events"] == []
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_sse_delivers_snapshot_then_events() -> None:
    """A fresh SSE subscriber gets a snapshot frame first, then live events.
    Pin both so the playground frontend's "render on snapshot, append on
    event" code path stays valid."""
    s = await _start_server()
    received: list[dict[str, Any]] = []
    try:

        async def consume() -> None:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=5.0)) as client,
                client.stream("GET", s.url + "/api/events") as resp,
            ):
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        if frame.startswith("data: "):
                            import json

                            received.append(json.loads(frame[len("data: ") :]))
                            if len(received) >= 2:
                                return

        consumer_task = asyncio.create_task(consume())
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            await client.post(
                s.url + "/api/claim",
                json={"row": 4, "col": 5, "colour": "#0f0", "claimed_by": "p1"},
            )
        await asyncio.wait_for(consumer_task, timeout=3.0)
        assert received[0]["event"] == "snapshot"
        assert received[1]["event"] == "tile_claimed"
        assert received[1]["row"] == 4 and received[1]["col"] == 5
    finally:
        await s.stop()


@pytest.mark.anyio
async def test_bind_failure_is_clean_runtime_error() -> None:
    """Uvicorn calls sys.exit(1) on EADDRINUSE; PlaygroundServer.start must
    pre-check and raise so the embedding script can recover."""
    port = _free_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", port))
    try:
        s = PlaygroundServer(port=port)
        with pytest.raises(RuntimeError, match=r"port .* is busy"):
            await s.start()
    finally:
        holder.close()
