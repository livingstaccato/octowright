# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Protocol

from provide.telemetry import get_logger

log = get_logger(__name__)


class _PageScreencast(Protocol):
    async def start(
        self,
        *,
        on_frame: Callable[[Mapping[str, object]], None],
        quality: int,
    ) -> None: ...

    async def stop(self) -> None: ...


class _ScreencastPage(Protocol):
    screencast: _PageScreencast


class _ScreencastSession(Protocol):
    instance_id: str
    page: _ScreencastPage


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


class ScreencastManager:
    """Per-session screencast lifecycle and viewer fan-out."""

    def __init__(self, session: _ScreencastSession, *, fps: int, quality: int) -> None:
        self._session = session
        self._fps = fps
        self._quality = quality
        self._lock = asyncio.Lock()
        self._viewers: set[ScreencastViewer] = set()
        self._started = False
        self._recovery_task: asyncio.Task[None] | None = None
        self._instance_id = str(session.instance_id)
        self.latest: bytes | None = None

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    async def add_viewer(self, *, fps: int | None = None) -> ScreencastViewer:
        async with self._lock:
            if not self._started:
                await self._start_locked(self._session.page)

            viewer = ScreencastViewer(fps=self._fps if fps is None else fps)
            if self.latest is not None:
                viewer.offer(self.latest)
            self._viewers.add(viewer)
            return viewer

    async def rebind(self, new_page: _ScreencastPage) -> None:
        async with self._lock:
            self._session.page = new_page
            if not self._started or not self._viewers:
                return

            self._started = False
            await self._start_locked(new_page)

    async def remove_viewer(self, viewer: ScreencastViewer) -> None:
        async with self._lock:
            if viewer not in self._viewers:
                return

            if len(self._viewers) > 1:
                self._viewers.remove(viewer)
                return

            if not self._started:
                self._viewers.remove(viewer)
                await self._stop_recovery_watcher_locked()
                return

            stop_error: BaseException | None = None
            try:
                await self._session.page.screencast.stop()
            except BaseException as exc:
                stop_error = exc
            finally:
                self._started = False
                self._viewers.remove(viewer)
                await self._stop_recovery_watcher_locked()
            if stop_error is not None:
                raise stop_error

    def _handle_frame(self, frame: Mapping[str, object]) -> None:
        data = frame.get("data")
        if not isinstance(data, bytes):
            return

        self.latest = data
        for viewer in tuple(self._viewers):
            viewer.offer(data)

    async def _start_locked(self, page: _ScreencastPage) -> None:
        await page.screencast.start(
            on_frame=self._handle_frame,
            quality=self._quality,
        )
        self._started = True
        self._ensure_recovery_watcher_locked()

    def _ensure_recovery_watcher_locked(self) -> None:
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        self._recovery_task = asyncio.create_task(
            self._watch_recovery(),
            name=f"octowright.screencast.recovery.{self._instance_id}",
        )

    async def _stop_recovery_watcher_locked(self) -> None:
        task = self._recovery_task
        self._recovery_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _watch_recovery(self) -> None:
        from octowright.browser_pool.session_event_bus import session_event_bus

        async with session_event_bus.subscribe() as sub:
            while True:
                event = await sub.get()
                if getattr(event, "instance_id", None) != self._instance_id:
                    continue
                if getattr(event, "outcome", None) != "recovered":
                    continue
                try:
                    await self.rebind(self._session.page)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "octowright.screencast.rebind_failed",
                        instance_id=self._instance_id,
                        error=repr(exc),
                    )


_registry_lock = asyncio.Lock()
_managers: dict[str, ScreencastManager] = {}
_pending_acquires: dict[str, int] = {}


async def acquire_viewer(
    session: _ScreencastSession,
    *,
    fps: int,
    quality: int,
) -> tuple[ScreencastManager, ScreencastViewer]:
    instance_id = str(session.instance_id)
    async with _registry_lock:
        manager = _managers.get(instance_id)
        if manager is None:
            manager = ScreencastManager(session, fps=fps, quality=quality)
            _managers[instance_id] = manager
        _pending_acquires[instance_id] = _pending_acquires.get(instance_id, 0) + 1

    viewer: ScreencastViewer | None = None
    try:
        viewer = await manager.add_viewer(fps=fps)
        await _finish_acquire(instance_id, manager, cleanup_empty=False)
    except BaseException:
        try:
            if viewer is not None:
                await manager.remove_viewer(viewer)
        finally:
            await _finish_acquire(instance_id, manager, cleanup_empty=True)
        raise
    return manager, viewer


async def release_viewer(manager: ScreencastManager, viewer: ScreencastViewer) -> None:
    try:
        await manager.remove_viewer(viewer)
    finally:
        if manager.viewer_count == 0:
            await _drop_empty_manager(manager)


async def _finish_acquire(
    instance_id: str,
    manager: ScreencastManager,
    *,
    cleanup_empty: bool,
) -> None:
    async with _registry_lock:
        pending = _pending_acquires.get(instance_id, 0)
        if pending <= 1:
            _pending_acquires.pop(instance_id, None)
            pending = 0
        else:
            _pending_acquires[instance_id] = pending - 1
            pending -= 1

        if cleanup_empty and pending == 0 and manager.viewer_count == 0 and _managers.get(instance_id) is manager:
            _managers.pop(instance_id, None)


async def _drop_empty_manager(manager: ScreencastManager) -> None:
    instance_id = manager._instance_id
    async with _registry_lock:
        if _managers.get(instance_id) is not manager:
            return
        if manager.viewer_count != 0 or _pending_acquires.get(instance_id, 0):
            return
        _managers.pop(instance_id, None)
