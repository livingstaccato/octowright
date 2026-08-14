# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the browser-pool session-closed event pipeline.

Covers:
- ``browser_close`` (agent_close) publishes to ``session_event_bus``.
- External eviction via ``_wire_close_evictor`` publishes ``user_close``.
- ``shutdown_pool`` publishes ``shutdown`` events.
- The ``SessionEventBus`` drops oldest events when the queue is full rather
  than blocking, and discards dead subscribers.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright.browser_pool.events import SessionClosedEvent
from octowright.browser_pool.session_event_bus import SessionEventBus, session_event_bus

# ─── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fake_session(
    *,
    instance_id: str = "abc123",
    kind: str = "chromium",
    label: str | None = "test-label",
    profile: str | None = "test-persona",
    log_path: str = "/tmp/test.jsonl",
) -> Any:
    """A duck-typed session double carrying a REAL ``SessionOperationGate`` --
    the close coordinator drives ``_operation_gate`` directly."""
    from octowright.session.operation_gate import SessionOperationGate

    return SimpleNamespace(
        instance_id=instance_id,
        kind=kind,
        label=label,
        profile=profile,
        log_path=log_path,
        url="about:blank",
        video_path=None,
        trace_path=None,
        har_path=None,
        protected=False,
        protected_reason="explicit",
        recorder=SimpleNamespace(
            record=MagicMock(),
            close=MagicMock(),
        ),
        pages=[],
        close=AsyncMock(),
        _teardown_after_close_cutoff=AsyncMock(),
        _operation_gate=SessionOperationGate(instance_id, kind),
        _crashed=False,
    )


# ─── SessionEventBus unit tests ───────────────────────────────────────────────


class TestSessionEventBus:
    @pytest.mark.anyio
    async def test_subscribe_and_receive_event(self) -> None:
        """A subscriber receives every published event."""
        bus = SessionEventBus()
        event = SessionClosedEvent(
            instance_id="x1",
            kind="chromium",
            label=None,
            profile=None,
            reason="agent_close",
            log_path="/tmp/x.jsonl",
        )
        received: list[SessionClosedEvent] = []

        async with bus.subscribe() as sub:
            bus.publish_nowait(event)
            received.append(await sub.get())

        assert len(received) == 1
        assert received[0] == event

    @pytest.mark.anyio
    async def test_no_events_after_unsubscribe(self) -> None:
        """After exiting the ``async with`` block, the subscriber is removed."""
        bus = SessionEventBus()
        assert bus.subscriber_count == 0
        async with bus.subscribe():
            assert bus.subscriber_count == 1
        assert bus.subscriber_count == 0

    @pytest.mark.anyio
    async def test_multiple_subscribers_all_receive(self) -> None:
        """Each subscriber gets its own copy of every event."""
        bus = SessionEventBus()
        event = SessionClosedEvent(
            instance_id="y1", kind="firefox", label=None, profile=None, reason="user_close", log_path="/tmp/y.jsonl"
        )
        results_a: list[SessionClosedEvent] = []
        results_b: list[SessionClosedEvent] = []

        async with bus.subscribe() as sub_a, bus.subscribe() as sub_b:
            bus.publish_nowait(event)
            results_a.append(await sub_a.get())
            results_b.append(await sub_b.get())

        assert results_a == [event]
        assert results_b == [event]

    @pytest.mark.anyio
    async def test_overflow_drops_oldest(self) -> None:
        """When the queue is full, the oldest event is dropped to make room."""
        from octowright.browser_pool.session_event_bus import _QUEUE_SIZE

        bus = SessionEventBus()

        def _ev(n: int) -> SessionClosedEvent:
            return SessionClosedEvent(
                instance_id=f"id{n}",
                kind="chromium",
                label=None,
                profile=None,
                reason="agent_close",
                log_path=f"/tmp/{n}.jsonl",
            )

        async with bus.subscribe() as sub:
            # Publish one more than the queue can hold.
            for i in range(_QUEUE_SIZE + 1):
                bus.publish_nowait(_ev(i))

            # Yield twice so all call_soon_threadsafe callbacks run and
            # enqueue their events before we try to drain.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            collected: list[SessionClosedEvent] = []
            while not sub._subscriber.queue.empty():
                collected.append(await sub.get())

        # We published _QUEUE_SIZE + 1 events; the queue holds _QUEUE_SIZE,
        # so exactly one was dropped (the oldest).
        assert len(collected) == _QUEUE_SIZE
        # The first event (id=0) was dropped; the last event is still there.
        assert collected[-1].instance_id == f"id{_QUEUE_SIZE}"


# ─── close_browser publishes agent_close ─────────────────────────────────────


@pytest.mark.anyio
async def test_close_browser_publishes_agent_close() -> None:
    """Calling ``close_browser`` publishes a ``SessionClosedEvent`` with
    ``reason='agent_close'`` to the process-level ``session_event_bus``."""
    from octowright.browser_pool.lifecycle import close_browser
    from octowright.browser_pool.pool import BrowserPool

    pool = BrowserPool()
    session = _fake_session(instance_id="close-me")
    pool._sessions["close-me"] = session  # type: ignore[assignment]

    received: list[SessionClosedEvent] = []

    with patch("octowright.browser_pool.lifecycle.remove_manifest_session"):
        async with session_event_bus.subscribe() as sub:
            await close_browser(pool, "close-me")
            received.append(await asyncio.wait_for(sub.get(), timeout=1.0))

    assert len(received) == 1
    evt = received[0]
    assert evt.instance_id == "close-me"
    assert evt.reason == "agent_close"
    assert evt.kind == "chromium"
    assert evt.label == "test-label"
    assert evt.profile == "test-persona"


@pytest.mark.anyio
async def test_close_browser_with_shutdown_reason() -> None:
    """``_reason='shutdown'`` is forwarded to the event."""
    from octowright.browser_pool.lifecycle import close_browser
    from octowright.browser_pool.pool import BrowserPool

    pool = BrowserPool()
    session = _fake_session(instance_id="shut-me")
    pool._sessions["shut-me"] = session  # type: ignore[assignment]

    received: list[SessionClosedEvent] = []

    with patch("octowright.browser_pool.lifecycle.remove_manifest_session"):
        async with session_event_bus.subscribe() as sub:
            await close_browser(pool, "shut-me", _reason="shutdown")
            received.append(await asyncio.wait_for(sub.get(), timeout=1.0))

    assert received[0].reason == "shutdown"


# ─── external eviction publishes user_close ───────────────────────────────────


@pytest.mark.anyio
async def test_wire_close_evictor_publishes_user_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``_wire_close_evictor``'s ``_evict`` callback fires (simulating a
    page.close or context.close signal), the bus receives ``reason='user_close'``.

    ``_accept_external_close_nowait`` needs a running loop to schedule its
    durable coordinator (which is what actually publishes the event), so this
    runs inside the test's own event loop rather than firing the callback
    from a bare sync function -- the production callback always runs on the
    daemon's real loop too."""
    from octowright.browser_pool.listeners import _wire_close_evictor
    from octowright.browser_pool.pool import BrowserPool

    pool = BrowserPool()
    session = _fake_session(instance_id="ext-close")
    pool._sessions["ext-close"] = session  # type: ignore[assignment]

    session.context = MagicMock()
    session.context.on = MagicMock()
    session.browser = None
    session._browser_for_close = None
    monkeypatch.setattr("octowright.session_manifest.remove_session", lambda _id: None)
    _wire_close_evictor(pool, session)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        # Trigger the evict path the same way Playwright would — call the
        # evict callback registered on context.on("close", ...).
        close_callback = session.context.on.call_args_list[0][0][1]
        close_callback()
        received = await asyncio.wait_for(sub.get(), timeout=2.0)

    assert received.reason == "user_close"
    assert received.instance_id == "ext-close"
