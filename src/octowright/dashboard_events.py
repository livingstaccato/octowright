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
        # Distinct scopes awaiting the one scheduled delivery, in first-publish
        # order. A single "last event wins" slot dropped every scope but the
        # newest, so scenario_start's back-to-back "scenarios" + "sessions"
        # never refreshed the scenarios panel.
        self.pending_scopes: dict[str, None] = {}
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
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
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
        for subscriber in tuple(self._subscribers):
            if not self._mark_pending(subscriber, scope):
                continue
            try:
                subscriber.loop.call_soon_threadsafe(self._deliver, subscriber)
            except RuntimeError:
                self._clear_pending(subscriber)

    @staticmethod
    def _mark_pending(subscriber: _Subscriber, scope: str) -> bool:
        with subscriber.lock:
            subscriber.pending_scopes[scope] = None
            if subscriber.delivery_scheduled:
                return False
            subscriber.delivery_scheduled = True
            return True

    @staticmethod
    def _deliver(subscriber: _Subscriber) -> None:
        with subscriber.lock:
            scopes = list(subscriber.pending_scopes)
            subscriber.pending_scopes.clear()
            subscriber.delivery_scheduled = False
        queue = subscriber.queue
        for scope in scopes:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait({"scope": scope})

    @staticmethod
    def _clear_pending(subscriber: _Subscriber) -> None:
        with subscriber.lock:
            subscriber.pending_scopes.clear()
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
