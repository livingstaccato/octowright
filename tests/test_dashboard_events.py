# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.responses import StreamingResponse

from octowright.dashboard_events import DashboardEventBus
from octowright.http.routes.events import dashboard_events_endpoint


class _FakeRequest:
    def __init__(self, *, disconnected: bool = False) -> None:
        self.disconnect_checks = 0
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        self.disconnect_checks += 1
        return self.disconnected


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
