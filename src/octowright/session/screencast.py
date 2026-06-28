# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress


class ScreencastViewer:
    """Bounded, fps-throttled frame sink for one dashboard client."""

    def __init__(self, *, fps: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._queue: deque[bytes] = deque(maxlen=4)
        self._min_gap = 1.0 / max(1, fps)
        self._clock = clock
        self._last_accepted: float | None = None
        self._waiters: deque[asyncio.Future[None]] = deque()

    def offer(self, frame: bytes) -> None:
        try:
            now = self._clock()
            if self._last_accepted is not None and now - self._last_accepted < self._min_gap:
                return
            self._last_accepted = now
            self._queue.append(frame)
            self._wake_one_waiter()
        except Exception:
            return

    async def get(self) -> bytes:
        while not self._queue:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            finally:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                task = asyncio.current_task()
                if task is not None and task.cancelling() and self._queue:
                    self._wake_one_waiter()
        return self._queue.popleft()

    def pending(self) -> int:
        return len(self._queue)

    def _wake_one_waiter(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
