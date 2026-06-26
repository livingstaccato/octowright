# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""In-process pub/sub for browser-pool session lifecycle events.

Mirrors the structural pattern of ``dashboard_events.DashboardEventBus`` but
delivers every event faithfully rather than coalescing duplicates.  Each
subscriber gets its own bounded asyncio queue so a slow consumer cannot block
pool operations — events are dropped (oldest first) if the queue fills, which
is preferable to blocking a Playwright callback thread.

Usage::

    async with session_event_bus.subscribe() as sub:
        while True:
            event = await sub.get()
            ...
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import TracebackType

from octowright.browser_pool.events import (
    DriverDiedEvent,
    SessionClosedEvent,
    SessionCrashedEvent,
    SessionEvent,
    SessionRecoveredEvent,
)

_QUEUE_SIZE = 64


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=_QUEUE_SIZE)


class SessionEventSubscription:
    """Read side handed to the subscriber inside the ``async with`` block."""

    def __init__(self, subscriber: _Subscriber) -> None:
        self._subscriber = subscriber

    async def get(self) -> SessionEvent:
        return await self._subscriber.queue.get()


class _SessionEventSubscriptionContext:
    def __init__(self, bus: SessionEventBus) -> None:
        self._bus = bus
        self._subscriber: _Subscriber | None = None

    async def __aenter__(self) -> SessionEventSubscription:
        subscriber = _Subscriber(asyncio.get_running_loop())
        self._subscriber = subscriber
        self._bus._subscribers.add(subscriber)
        return SessionEventSubscription(subscriber)

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        if self._subscriber is not None:
            self._bus._subscribers.discard(self._subscriber)


class SessionEventBus:
    """Process-local fan-out bus for ``SessionClosedEvent`` notifications.

    ``publish_nowait`` is safe to call from synchronous Playwright callbacks
    (Playwright fires ``page.close`` on a sync thread).  Each subscriber's loop
    receives the event via ``call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._subscribers: set[_Subscriber] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> _SessionEventSubscriptionContext:
        return _SessionEventSubscriptionContext(self)

    def publish_nowait(self, event: SessionEvent) -> None:
        """Deliver ``event`` to every subscriber.

        When called from within the subscriber's asyncio event loop (the
        normal case — pool operations always run in the MCP server loop),
        the event is enqueued directly via ``call_soon`` so that a
        subsequent ``await sub.get()`` in the same coroutine resolves on
        the next iteration without an extra round-trip through the self-pipe.

        When called from a different thread (edge case: a Playwright sync
        callback that somehow runs outside the loop), ``call_soon_threadsafe``
        is used to safely hand off to the subscriber's loop.
        """
        for subscriber in tuple(self._subscribers):
            current: asyncio.AbstractEventLoop | None
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None

            if current is subscriber.loop:
                # Same loop — schedule directly without the self-pipe overhead.
                subscriber.loop.call_soon(self._enqueue, subscriber, event)
            else:
                try:
                    subscriber.loop.call_soon_threadsafe(self._enqueue, subscriber, event)
                except RuntimeError:
                    # Loop is closed — discard the stale subscriber.
                    self._subscribers.discard(subscriber)

    @staticmethod
    def _enqueue(subscriber: _Subscriber, event: SessionEvent) -> None:
        queue = subscriber.queue
        if queue.full():
            # Drop oldest to make room; a slow consumer misses stale events
            # rather than blocking pool teardown.
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)


session_event_bus = SessionEventBus()

__all__ = [
    "DriverDiedEvent",
    "SessionClosedEvent",
    "SessionCrashedEvent",
    "SessionEvent",
    "SessionEventBus",
    "SessionEventSubscription",
    "SessionRecoveredEvent",
    "session_event_bus",
]
