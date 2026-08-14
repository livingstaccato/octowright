# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from typing import Any, LiteralString, Protocol

from provide.telemetry import get_logger

from octowright.session.operation_gate import USE_DEFAULT, UseDefault

log = get_logger(__name__)

# Stopping the producer on a page that just crashed (or is being torn down) can
# hang on the Playwright channel; a live preview must not wedge the endpoint's
# release path waiting for it.
_STOP_TIMEOUT_SECONDS = 5.0


class ScreencastEnded(Exception):
    """Raised by :meth:`ScreencastViewer.get` when the stream ended server-side.

    The producer is gone for good (the session closed, or a rebind could not
    reattach it), so the endpoint closes the WebSocket and the dashboard falls
    back to screenshot polling instead of blocking on frames that never come.
    """


class _PageScreencast(Protocol):
    # Loosely typed on purpose: Playwright's ``Page.screencast.start`` takes a
    # ``ScreencastFrame`` TypedDict callback and returns an async context
    # manager. The manager only ever uses the frame mapping and ignores the
    # return, so the protocol stays wide enough for a real page to satisfy it.
    async def start(
        self,
        *,
        on_frame: Callable[[Any], Any] | None = None,
        quality: int | None = None,
    ) -> Any: ...

    async def stop(self) -> None: ...


class _ScreencastPage(Protocol):
    # Read-only: Playwright exposes ``Page.screencast`` as a property, so a
    # settable protocol member would not match a real page.
    @property
    def screencast(self) -> _PageScreencast: ...


class _ScreencastSession(Protocol):
    instance_id: str
    page: _ScreencastPage

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]: ...


class ScreencastViewer:
    """Bounded, fps-throttled frame sink for one dashboard client."""

    def __init__(self, *, fps: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._queue: deque[bytes] = deque(maxlen=4)
        self._min_gap = 1.0 / max(1, fps)
        self._clock = clock
        self._last_accepted: float | None = None
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._ended = False

    def offer(self, frame: bytes) -> None:
        if self._ended:
            return
        try:
            now = self._clock()
            if self._last_accepted is not None and now - self._last_accepted < self._min_gap:
                return
            self._last_accepted = now
            self._queue.append(frame)
            self._wake_one_waiter()
        except Exception:
            return

    def end(self) -> None:
        """Mark the stream finished and wake every waiter with ``ScreencastEnded``.

        Frames already queued are still delivered — ``get`` only raises once the
        buffer is drained — so a viewer never loses the last frame it was sent.
        """
        self._ended = True
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)

    async def get(self) -> bytes:
        while not self._queue:
            if self._ended:
                raise ScreencastEnded("screencast stream ended")
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
        # Bus subscription backing the watcher. Held by the manager (not the
        # task) so it exists from the moment the first viewer starts the
        # producer — see _ensure_recovery_watcher_locked.
        self._recovery_stack: AsyncExitStack | None = None
        self._recovery_sub: Any = None
        self._instance_id = str(session.instance_id)
        # The page the producer is actually attached to. NOT the same thing as
        # ``session.page``: the active page moves on page_switch/page_close and
        # on crash recovery, and the stop must target whatever we started.
        self._bound_page: _ScreencastPage | None = None
        self.latest: bytes | None = None

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    async def add_viewer(self, *, fps: int | None = None) -> ScreencastViewer:
        # Lock ordering: the session operation lease is acquired BEFORE
        # ``self._lock``, never the reverse -- reversing it would let this
        # method block inside ``self._lock`` while holding no session lease,
        # which is exactly the shape that deadlocks against any other code
        # path that acquires the two in the opposite order.
        async with self._session.operation("screencast_start"), self._lock:
            return await self._add_viewer_locked(fps=fps)

    async def _add_viewer_locked(self, *, fps: int | None = None) -> ScreencastViewer:
        if not self._started:
            await self._start_owned_locked(self._session.page)

        viewer = ScreencastViewer(fps=self._fps if fps is None else fps)
        if self.latest is not None:
            viewer.offer(self.latest)
        self._viewers.add(viewer)
        return viewer

    async def rebind(self, new_page: _ScreencastPage) -> None:
        """Move the producer onto ``new_page`` (crash recovery, or a tab switch).

        A rebind to the page we are already casting is a no-op: Playwright
        refuses a second ``screencast.start`` on the same page, and a
        session-level recovery event names the unchanged active page whenever a
        *background* tab was the one that crashed.
        """
        async with self._session.operation("screencast_rebind"), self._lock:
            await self._rebind_locked(new_page)

    async def _rebind_locked(self, new_page: _ScreencastPage) -> None:
        self._session.page = new_page
        if not self._started or not self._viewers:
            return
        if new_page is self._bound_page:
            return

        await self._stop_bound_owned_locked()
        self._started = False
        try:
            await self._start_owned_locked(new_page)
        except BaseException:
            # No producer left and no path back: wake the viewers so their
            # sockets close and the dashboard falls back to polling.
            self._end_viewers_locked()
            raise

    async def terminate(self) -> None:
        """Stop the producer and end every viewer — the session is gone."""
        async with self._lock:
            await self._terminate_producer_after_close()

    async def _terminate_producer_after_close(self) -> None:
        """Best-effort producer stop + viewer wakeup once the session is
        already closing or closed.

        Runs under only ``self._lock`` -- NOT the session operation gate. By
        the time an external close reaches here the gate may already be
        ``closing``/``closed``, and entering it would raise instead of
        releasing the producer and waking viewers, which is the one thing
        this path must still do.
        """
        if self._started:
            self._started = False
            page = self._bound_page
            self._bound_page = None
            if page is not None:
                try:
                    await asyncio.wait_for(page.screencast.stop(), timeout=_STOP_TIMEOUT_SECONDS)
                except Exception as exc:
                    log.debug(
                        "octowright.screencast.stop_bound_failed",
                        instance_id=self._instance_id,
                        error=repr(exc),
                    )
        self._end_viewers_locked()

    async def remove_viewer(self, viewer: ScreencastViewer) -> None:
        async with self._session.operation("screencast_stop"), self._lock:
            await self._remove_viewer_locked(viewer)

    async def _remove_viewer_locked(self, viewer: ScreencastViewer) -> None:
        if viewer not in self._viewers:
            return

        if len(self._viewers) > 1:
            self._viewers.remove(viewer)
            return

        if not self._started:
            self._viewers.remove(viewer)
            await self._stop_recovery_watcher_locked()
            return

        bound = self._bound_page if self._bound_page is not None else self._session.page
        stop_error: BaseException | None = None
        try:
            await bound.screencast.stop()
        except BaseException as exc:
            stop_error = exc
        finally:
            self._started = False
            self._bound_page = None
            self._viewers.remove(viewer)
            await self._stop_recovery_watcher_locked()
        if stop_error is not None:
            raise stop_error

    def _handle_frame(self, frame: Mapping[str, object]) -> None:
        # Deliberately does not acquire the session operation gate: this runs
        # on every delivered frame from an already-started producer and must
        # never serialize against unrelated session work.
        data = frame.get("data")
        if not isinstance(data, bytes):
            return

        self.latest = data
        for viewer in tuple(self._viewers):
            viewer.offer(data)

    async def _start_owned_locked(self, page: _ScreencastPage) -> None:
        # Subscribe first: ``screencast.start`` awaits the Playwright channel, so
        # a session close landing during that await would be dropped by the bus
        # if we only subscribed afterwards.
        await self._ensure_recovery_watcher_locked()
        try:
            async with self._session.operation("screencast_start"):
                await page.screencast.start(
                    on_frame=self._handle_frame,
                    quality=self._quality,
                )
        except BaseException:
            if not self._viewers:
                await self._stop_recovery_watcher_locked()
            raise
        self._started = True
        self._bound_page = page

    async def _stop_bound_owned_locked(self) -> None:
        """Best-effort stop of the currently bound producer.

        Called when the producer moves to another page (a live rebind), so
        the old page's encoder does not keep running (and keep pushing frames)
        for the rest of its life. The page may be crashed or closing, hence the
        timeout and the swallow. Wraps only the Playwright call in the session
        operation lease so a standalone caller (a test exercising this helper
        directly) is still refused once the gate is closing/closed, rather
        than the failure being folded into the same best-effort swallow.
        """
        page = self._bound_page
        self._bound_page = None
        if page is None:
            return
        async with self._session.operation("screencast_stop"):
            try:
                await asyncio.wait_for(page.screencast.stop(), timeout=_STOP_TIMEOUT_SECONDS)
            except Exception as exc:
                log.debug(
                    "octowright.screencast.stop_bound_failed",
                    instance_id=self._instance_id,
                    error=repr(exc),
                )

    def _end_viewers_locked(self) -> None:
        for viewer in tuple(self._viewers):
            viewer.end()

    async def _ensure_recovery_watcher_locked(self) -> None:
        """Subscribe to the session event bus, then (re)start the watcher task.

        The subscription is taken HERE rather than inside the task: the bus
        drops events that have no subscriber, and ``create_task`` only
        *schedules* the task, so a ``SessionClosedEvent`` published before its
        first step would be lost and the viewers would never be woken. Entering
        the subscription context reaches no ``await`` that suspends, so it
        completes in the caller's step — before ``add_viewer`` returns and
        therefore before any other task can publish.
        """
        from octowright.browser_pool.session_event_bus import session_event_bus

        if self._recovery_stack is None:
            stack = AsyncExitStack()
            self._recovery_sub = await stack.enter_async_context(session_event_bus.subscribe())
            self._recovery_stack = stack
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        self._recovery_task = asyncio.create_task(
            self._watch_recovery(self._recovery_sub),
            name=f"octowright.screencast.recovery.{self._instance_id}",
        )

    async def _stop_recovery_watcher_locked(self) -> None:
        task = self._recovery_task
        stack = self._recovery_stack
        self._recovery_task = None
        self._recovery_stack = None
        self._recovery_sub = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()

    async def _watch_recovery(self, sub: Any) -> None:
        from octowright.browser_pool.session_event_bus import SessionClosedEvent

        while True:
            event = await sub.get()
            if getattr(event, "instance_id", None) != self._instance_id:
                continue
            if isinstance(event, SessionClosedEvent):
                # No page left to cast. Stop and wake the viewers rather
                # than leaving them blocked on a stream that ended.
                await self.terminate()
                return
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
    # ``add_viewer`` is now gated: even after its body finishes successfully,
    # releasing the session operation lease is itself a real await (shielded
    # internally so the release completes cleanly), so a caller cancellation
    # landing in that narrow window still surfaces here as CancelledError --
    # AFTER the viewer already exists. Run it as its own task so a shielded
    # re-await can recover the true, already-committed outcome instead of
    # leaking a live producer nobody has a handle to release just because
    # this call merely *looked* like it failed.
    add_task: asyncio.Task[ScreencastViewer] = asyncio.create_task(manager.add_viewer(fps=fps))
    try:
        try:
            viewer = await asyncio.shield(add_task)
        except asyncio.CancelledError:
            with suppress(BaseException):
                viewer = await asyncio.shield(add_task)
            raise
        await _finish_acquire(instance_id, manager, cleanup_empty=False)
    except BaseException:
        try:
            if viewer is not None:
                await manager.remove_viewer(viewer)
        finally:
            await _finish_acquire(instance_id, manager, cleanup_empty=True)
        raise
    return manager, viewer


async def notify_active_page(instance_id: str, page: _ScreencastPage) -> None:
    """Follow a session's active-page change with any live screencast.

    Called from ``switch_page`` / ``close_page``: without it the producer stays
    on the page that was active when the first viewer connected, so the preview
    shows the wrong tab and the old page's encoder is never stopped. Best-effort
    — a page switch must not fail because the preview could not follow it.
    """
    async with _registry_lock:
        manager = _managers.get(str(instance_id))
    if manager is None:
        return
    try:
        await manager.rebind(page)
    except Exception as exc:
        log.warning(
            "octowright.screencast.active_page_rebind_failed",
            instance_id=str(instance_id),
            error=repr(exc),
        )


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
