# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""In-process dashboard invalidation events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

DashboardEvent = dict[str, str]


class DashboardEventSubscription:
    def __init__(self, queue: asyncio.Queue[DashboardEvent]) -> None:
        self._queue = queue

    async def get(self) -> DashboardEvent:
        return await self._queue.get()


class DashboardEventBus:
    """Lightweight pub/sub bus for dashboard invalidations.

    This is intentionally process-local: events only tell connected dashboards
    to refetch canonical REST state.
    """

    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[DashboardEvent]]] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[DashboardEventSubscription]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=32)
        subscriber = (loop, queue)
        self._subscribers.add(subscriber)
        try:
            yield DashboardEventSubscription(queue)
        finally:
            self._subscribers.discard(subscriber)

    async def publish(self, scope: str) -> None:
        self.publish_nowait(scope)

    def publish_nowait(self, scope: str) -> None:
        event = {"scope": scope}
        for loop, queue in tuple(self._subscribers):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._deliver, queue, event)

    @staticmethod
    def _deliver(queue: asyncio.Queue[DashboardEvent], event: DashboardEvent) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)


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
