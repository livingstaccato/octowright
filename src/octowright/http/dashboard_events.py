# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""In-process dashboard invalidation events."""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from types import TracebackType

DashboardEvent = dict[str, str]


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=32)
        self.lock = threading.Lock()
        self.pending_event: DashboardEvent | None = None
        self.delivery_scheduled = False


class DashboardEventSubscription:
    def __init__(self, subscriber: _Subscriber) -> None:
        self._subscriber = subscriber

    async def get(self) -> DashboardEvent:
        return await self._subscriber.queue.get()


class DashboardEventSubscriptionContext:
    def __init__(self, bus: DashboardEventBus) -> None:
        self._bus = bus
        self._subscriber: _Subscriber | None = None

    async def __aenter__(self) -> DashboardEventSubscription:
        subscriber = _Subscriber(asyncio.get_running_loop())
        self._subscriber = subscriber
        self._bus._subscribers.add(subscriber)
        return DashboardEventSubscription(subscriber)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._subscriber is not None:
            self._bus._subscribers.discard(self._subscriber)


class DashboardEventBus:
    """Lightweight pub/sub bus for dashboard invalidations.

    This is intentionally process-local: events only tell connected dashboards
    to refetch canonical REST state.
    """

    def __init__(self) -> None:
        self._subscribers: set[_Subscriber] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> DashboardEventSubscriptionContext:
        return DashboardEventSubscriptionContext(self)

    async def publish(self, scope: str) -> None:
        self.publish_nowait(scope)

    def publish_nowait(self, scope: str) -> None:
        event = {"scope": scope}
        for subscriber in tuple(self._subscribers):
            if not self._mark_pending(subscriber, event):
                continue
            try:
                subscriber.loop.call_soon_threadsafe(self._deliver, subscriber)
            except RuntimeError:
                self._clear_pending(subscriber)

    @staticmethod
    def _mark_pending(subscriber: _Subscriber, event: DashboardEvent) -> bool:
        with subscriber.lock:
            subscriber.pending_event = event
            if subscriber.delivery_scheduled:
                return False
            subscriber.delivery_scheduled = True
            return True

    @staticmethod
    def _deliver(subscriber: _Subscriber) -> None:
        with subscriber.lock:
            event = subscriber.pending_event
            subscriber.pending_event = None
            subscriber.delivery_scheduled = False
        if event is None:
            return
        queue = subscriber.queue
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)

    @staticmethod
    def _clear_pending(subscriber: _Subscriber) -> None:
        with subscriber.lock:
            subscriber.pending_event = None
            subscriber.delivery_scheduled = False


dashboard_events = DashboardEventBus()


async def publish_dashboard_invalidation(scope: str) -> None:
    await dashboard_events.publish(scope)


def publish_dashboard_invalidation_nowait(scope: str) -> None:
    dashboard_events.publish_nowait(scope)


__all__ = [
    "DashboardEvent",
    "DashboardEventBus",
    "DashboardEventSubscription",
    "dashboard_events",
    "publish_dashboard_invalidation",
    "publish_dashboard_invalidation_nowait",
]
