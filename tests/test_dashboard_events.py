# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse

from octowright.dashboard_events import DashboardEventBus
from octowright.http.pairing import DASHBOARD_STATE_ATTR, DashboardPairingState, dashboard_access_ok
from octowright.http.routes import events as event_routes
from octowright.http.routes.events import dashboard_events_endpoint


class _FakeRequest:
    def __init__(self, *, disconnected: bool = False) -> None:
        self.disconnect_checks = 0
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        self.disconnect_checks += 1
        return self.disconnected


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _redeem(state: DashboardPairingState) -> str:
    grant = state.redeem_code(state.mint_code())
    assert grant is not None
    return grant.bearer


def _paired_request(state: DashboardPairingState, bearer: str) -> Request:
    app = Starlette()
    setattr(app.state, DASHBOARD_STATE_ATTR, state)
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", f"Bearer {bearer}".encode())],
        "query_string": b"",
        "path": "/api/dashboard/events",
        "app": app,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


class _TailWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int | None, str | None] | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = (code, reason)

    async def receive(self) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_dashboard_event_bus_publishes_invalidation_to_subscribers() -> None:
    bus = DashboardEventBus()
    async with bus.subscribe() as subscription:
        await bus.publish("sessions")

        event = await subscription.get()

    assert event == {"scope": "sessions"}


@pytest.mark.asyncio
async def test_dashboard_events_stream_sends_hello_then_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = DashboardEventBus()
    monkeypatch.setattr("octowright.http.routes.events.dashboard_events", bus)
    request = _FakeRequest()

    response = await dashboard_events_endpoint(request)  # type: ignore[arg-type]

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    body = cast(AsyncGenerator[bytes, None], response.body_iterator)
    hello = (await anext(body)).decode("utf-8")
    assert hello.startswith("event: hello\n")
    await bus.publish("sessions")
    invalidation = (await anext(body)).decode("utf-8")
    assert invalidation.startswith("event: invalidate\n")
    data_line = next(line for line in invalidation.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == {"scope": "sessions"}
    await body.aclose()


@pytest.mark.asyncio
async def test_dashboard_events_stream_cleans_up_idle_disconnected_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = DashboardEventBus()
    monkeypatch.setattr("octowright.http.routes.events.dashboard_events", bus)
    request = _FakeRequest()
    response = await dashboard_events_endpoint(request)  # type: ignore[arg-type]
    body = cast(AsyncGenerator[bytes, None], response.body_iterator)

    await anext(body)
    assert bus.subscriber_count == 1

    request.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(body), timeout=0.2)

    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_dashboard_events_stream_stops_when_established_bearer_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "1")
    bus = DashboardEventBus()
    monkeypatch.setattr(event_routes, "dashboard_events", bus)
    clock = _Clock()
    state = DashboardPairingState(expected_token="token", monotonic_clock=clock, session_ttl=5.0)
    request = _paired_request(state, _redeem(state))
    assert dashboard_access_ok(request)
    response = await dashboard_events_endpoint(request)
    body = cast(AsyncGenerator[bytes, None], response.body_iterator)

    assert (await anext(body)).startswith(b"event: hello")
    clock.now += 6.0
    await bus.publish("sessions")

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(body), timeout=0.2)


@pytest.mark.asyncio
async def test_dashboard_events_stream_stops_when_established_bearer_is_lru_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "1")
    bus = DashboardEventBus()
    monkeypatch.setattr(event_routes, "dashboard_events", bus)
    state = DashboardPairingState(expected_token="token", max_sessions=1)
    request = _paired_request(state, _redeem(state))
    assert dashboard_access_ok(request)
    response = await dashboard_events_endpoint(request)
    body = cast(AsyncGenerator[bytes, None], response.body_iterator)

    assert (await anext(body)).startswith(b"event: hello")
    _redeem(state)
    await bus.publish("sessions")

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(body), timeout=0.2)


@pytest.mark.asyncio
async def test_tail_stream_closes_1008_when_established_bearer_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "1")
    clock = _Clock()
    state = DashboardPairingState(expected_token="token", monotonic_clock=clock, session_ttl=5.0)
    request = _paired_request(state, _redeem(state))
    assert dashboard_access_ok(request)
    websocket = _TailWebSocket()

    monkeypatch.setattr(event_routes, "_tail_jsonl", lambda _path, cursor: {"events": [], "cursor": cursor})
    monkeypatch.setattr(event_routes, "_live_session_or_none", lambda _sid: object())

    async def advance_clock(_websocket: object, _seconds: float) -> bool:
        clock.now += 6.0
        return True

    monkeypatch.setattr(event_routes, "_sleep_or_disconnect", advance_clock)

    await asyncio.wait_for(
        event_routes._stream_tail(
            websocket,  # type: ignore[arg-type]
            "session",
            Path("/tmp/session.jsonl"),
            0,
            lease=request.state.dashboard_stream_lease,
        ),
        timeout=0.2,
    )

    assert websocket.closed == (1008, "dashboard pairing expired")
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_dashboard_event_bus_coalesces_scheduled_delivery_per_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = DashboardEventBus()
    loop = asyncio.get_running_loop()
    scheduled: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_call_soon_threadsafe(callback: Any, *args: Any) -> None:
        scheduled.append((callback, args))

    monkeypatch.setattr(loop, "call_soon_threadsafe", fake_call_soon_threadsafe)

    async with bus.subscribe() as subscription:
        for scope in ("sessions", "scenarios", "macros"):
            bus.publish_nowait(scope)

        assert len(scheduled) == 1
        callback, args = scheduled[0]
        callback(*args)
        assert await subscription.get() == {"scope": "macros"}


def test_dashboard_events_route_is_guarded() -> None:
    from octowright import http as _http

    app = _http.build_app()
    matches: list[Any] = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/dashboard/events"
        and "GET" in (getattr(route, "methods", None) or set())
    ]

    assert len(matches) == 1
    endpoint = SimpleNamespace(wrapped=getattr(matches[0].endpoint, "__wrapped__", None))
    assert endpoint.wrapped is dashboard_events_endpoint
