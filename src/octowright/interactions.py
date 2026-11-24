# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-owned passive interaction capture; no browser IPC from bindings."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octowright.interaction_schema import VIDEO, number, valid_geometry, valid_payload
from octowright.interaction_storage import InteractionSink, interaction_path, read_interactions

if TYPE_CHECKING:
    from octowright.session import BrowserSession

__all__ = ["InteractionController", "interaction_path", "read_interactions"]
_ASSET = Path(__file__).parent / "browser_pool" / "_assets" / "interactions.js"
_CONFIGURE = """options => {
    const observer = window.__octowrightInteractions;
    if (!observer) return false;
    observer.configure(options);
    return true;
}"""


@dataclass
class _PageState:
    page_id: int
    origin: float = field(default_factory=lambda: time.time() * 1000)
    video: str | None = None
    closed: bool = False
    resolved: bool = False
    published: bool = False
    geometry: dict[str, Any] | None = None


class InteractionController:
    """Install before navigation, even while dormant, to preserve each video clock."""

    def __init__(self, log_path: Path, *, show: bool = False, record: bool = False) -> None:
        if type(show) is not bool or type(record) is not bool:
            raise ValueError("interaction settings must be boolean")
        self.show, self.record = show, record
        self._log_path = log_path
        self._sink: InteractionSink | None = None
        self._context: Any = None
        self._session: BrowserSession | None = None
        self._pages: dict[Any, _PageState] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._contacts: dict[tuple[int, str, int], float] = {}
        self.applied_frames = 0
        self.pending_frames = 0
        self._next_id = 0
        self._accepted: deque[float] = deque()
        self._ever_lost = False
        self._loss = False
        self._failed = False
        self._closed = False

    def _write(self, row: dict[str, Any]) -> None:
        if self._failed:
            return
        try:
            if self._sink is None:
                self._sink = InteractionSink(self._log_path)
            if not self._sink.append(row):
                self._failed = True
        except (OSError, ValueError):
            # Never return host paths/errors through a page binding.
            self._failed = True

    def _control(self, kind: str, **fields: Any) -> None:
        self._write({"type": kind, "v": 1, "at": time.time() * 1000, **fields})

    def _metadata(self, state: _PageState) -> None:
        state.published = True
        self._write(
            {
                "type": "page",
                "v": 1,
                "page_id": state.page_id,
                "video": state.video,
                "time_origin": state.origin,
                "time_origin_kind": "estimate",
                "video_geometry": state.geometry,
            }
        )

    def _settings(self) -> dict[str, bool]:
        return {"show": self.show, "record": self.record}

    def _source(self, source: Any) -> _PageState | None:
        if self._closed or not isinstance(source, dict):
            return None
        page, frame = source.get("page"), source.get("frame")
        if source.get("context") is not self._context or getattr(frame, "page", None) is not page:
            return None
        state = self._pages.get(page)
        return state if state is not None and not state.closed else None

    def _seed_geometry(self, state: _PageState, payload: Any) -> None:
        if state.geometry is not None or not isinstance(payload, dict):
            return
        geometry = {
            "engine": getattr(self._session, "kind", None),
            "viewport_width": payload.get("width"),
            "viewport_height": payload.get("height"),
            "device_scale_factor": payload.get("dpr"),
        }
        if valid_geometry(geometry):
            state.geometry = geometry
            if self.record:
                self._metadata(state)

    def _config_binding(self, source: Any, payload: Any) -> dict[str, bool]:
        # Only the top viewport seeds initial geometry. Clock ownership stays in setup.
        state = self._source(source)
        if state is None:
            return {"show": False, "record": False}
        if source["frame"] is source["page"].main_frame:
            self._seed_geometry(state, payload)
        return self._settings()

    def _event_binding(self, source: Any, payload: Any) -> None:
        state = self._source(source)
        if state is None or not self.record or not valid_payload(payload):
            return
        if abs(payload["at"] - time.time() * 1000) > 60_000:
            return
        if not self._admit(state.page_id, payload):
            return
        if not state.published:
            self._metadata(state)
        self._write(
            {
                "type": "interaction",
                "page_id": state.page_id,
                "video": state.video,
                "t": max(0, payload["at"] - state.origin),
                **payload,
            }
        )

    def _admit(self, page_id: int, payload: dict[str, Any]) -> bool:
        now = time.monotonic()
        while self._accepted and now - self._accepted[0] >= 1:
            self._accepted.popleft()
        if len(self._accepted) >= 120:
            self._drop()
            return False
        if not self._contact(page_id, payload, now):
            return False
        self._accepted.append(now)
        self._loss = False
        return True

    def _contact(self, page_id: int, payload: dict[str, Any], now: float) -> bool:
        key = (page_id, payload["pointer_type"], payload["pointer_id"])
        phase = payload["phase"]
        if phase == "down" and key not in self._contacts and len(self._contacts) >= 16:
            self._drop()
            return False
        if phase == "move":
            previous = self._contacts.get(key)
            if previous is None or now - previous < 0.05:
                return False
        if phase in ("up", "cancel"):
            self._contacts.pop(key, None)
        elif phase in ("down", "move"):
            self._contacts[key] = now
        return True

    def _drop(self) -> None:
        self._contacts.clear()
        if not self._loss and self.record:
            self._control("reset", reset=True, reason="loss")
        self._loss = True
        self._ever_lost = True

    def _spawn(self, page: Any, state: _PageState, *, closing: bool = False) -> None:
        if len(self._tasks) >= 64 or self._closed:
            self._drop()
            return
        task = asyncio.create_task(self._prepare_page(page, state, closing=closing))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _new_page(self, page: Any) -> _PageState | None:
        for old_page, old in list(self._pages.items()):
            if old.closed and old.resolved:
                del self._pages[old_page]
        if page in self._pages:
            return self._pages[page]
        if len(self._pages) >= 64:
            self._drop()
            return None
        self._next_id += 1
        state = _PageState(self._next_id)
        self._pages[page] = state
        page.on("close", lambda: self._page_closed(page, state))
        return state

    def _page_created(self, page: Any) -> None:
        state = self._new_page(page)
        if state is not None:
            self._spawn(page, state)

    def _page_closed(self, page: Any, state: _PageState) -> None:
        if self._closed:
            return
        state.closed = True
        self._contacts = {k: t for k, t in self._contacts.items() if k[0] != state.page_id}
        if self.record:
            self._control("reset", reset=True, reason="page_closed", page_id=state.page_id)
        if not state.resolved:
            self._spawn(page, state, closing=True)

    async def _probe_origin(self, page: Any, state: _PageState) -> None:
        from octowright.session.timeouts import bounded

        blank = page.url == "about:blank"
        try:
            probe = await bounded(
                page.evaluate(
                    "({time_origin:performance.timeOrigin,width:innerWidth,height:innerHeight,dpr:devicePixelRatio})"
                ),
                operation="interaction initial geometry",
                timeout=2,
            )
            if isinstance(probe, dict):
                if (
                    blank
                    and not state.published
                    and number(probe.get("time_origin"), state.origin - 60_000, state.origin + 1000)
                ):
                    state.origin = probe["time_origin"]
                self._seed_geometry(state, probe)
        except Exception:
            pass  # Navigation may replace the blank document while probing.

    async def _prepare_page(self, page: Any, state: _PageState, *, closing: bool = False) -> None:
        from octowright.session.timeouts import bounded

        if not closing:
            await self._probe_origin(page, state)
        try:
            if self.record:
                self._metadata(state)
            video = page.video
            if video is not None:
                path = await bounded(video.path(), operation="interaction video identity", timeout=3)
                name = Path(path).name
                if VIDEO.fullmatch(name):
                    state.video = name
                    if self.record:
                        self._metadata(state)
            state.resolved = video is None or state.video is not None or closing
        except Exception:
            # Lifecycle work is best effort and bounded; cancellation still propagates.
            state.resolved = closing

    async def install(self, session: BrowserSession) -> None:
        from octowright.session.timeouts import bounded

        if self._context is not None:
            raise ValueError("interaction controller already installed")
        self._session = session
        self._context = session.context
        self._context.on("page", self._page_created)
        for page in self._context.pages:
            state = self._new_page(page)
            if state is not None:
                await self._prepare_page(page, state)
        await bounded(
            self._context.expose_binding("__octowrightInteractionConfig", self._config_binding),
            operation="interaction config binding",
        )
        await bounded(
            self._context.expose_binding("__octowrightInteractionEvent", self._event_binding),
            operation="interaction event binding",
        )
        script = _ASSET.read_text(encoding="utf-8")
        await bounded(self._context.add_init_script(script=script), operation="interaction observer init")
        await self._propagate(
            "async () => {\n"
            + script
            + "\nawait window.__octowrightInteractions?.ready; return Boolean(window.__octowrightInteractions); }"
        )
        if self.record:
            self._control("settings", **self._settings(), reset=True)

    async def _propagate(self, script: str = _CONFIGURE) -> None:
        if self._session is None:
            raise ValueError("interaction controller not installed")
        async with self._session.operation("browser_set_interaction_visuals"):
            frames = []
            total = 0
            for page in list(self._pages):
                current = page.frames
                total += len(current)
                frames.extend(current[:256])
            self.applied_frames = await self._apply_frames(frames, script)
            self.pending_frames = total - self.applied_frames

    async def _apply_frames(self, frames: list[Any], script: str) -> int:
        from octowright.session.timeouts import bounded

        if self._session is None:
            raise ValueError("interaction controller not installed")
        applied = 0
        async with self._session.operation("browser_set_interaction_visuals"):
            try:
                async with asyncio.timeout(10):
                    for frame in frames:
                        try:
                            result = await bounded(
                                frame.evaluate(script, self._settings()), operation="interaction configure", timeout=2
                            )
                            applied += result is True
                        except Exception:
                            continue
            except TimeoutError:
                pass
        return applied

    @staticmethod
    def _validate_change(show: bool | None, record: bool | None) -> None:
        if show is None and record is None:
            raise ValueError("provide an interaction setting")
        if any(v is not None and type(v) is not bool for v in (show, record)):
            raise ValueError("interaction settings must be boolean")

    def _change_settings(self, show: bool | None, record: bool | None) -> bool:
        self._validate_change(show, record)
        if self._closed:
            raise ValueError("interaction controller closed")
        requested = {"show": self.show if show is None else show, "record": self.record if record is None else record}
        if requested == self._settings():
            return False
        was_recording = self.record
        self.show, self.record = requested["show"], requested["record"]
        self._contacts.clear()
        if self.record or was_recording:
            for state in self._pages.values():
                self._metadata(state)
            self._control("settings", **self._settings(), reset=True)
        return True

    async def configure(self, *, show: bool | None = None, record: bool | None = None) -> dict[str, Any]:
        if self._session is None:
            raise ValueError("interaction controller not installed")
        async with self._session.operation("browser_set_interaction_visuals"):
            self._change_settings(show, record)
            await self._propagate()
            return {
                **self._settings(),
                "truncated": self._ever_lost or self._failed,
                "applied_frames": self.applied_frames,
                "pending_frames": self.pending_frames,
            }

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._context is not None:
            self._context.remove_listener("page", self._page_created)
        try:
            if self._tasks:
                await asyncio.wait(self._tasks, timeout=3.2)
        finally:
            await self._finish_close()

    async def _finish_close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self.record:
                self._control("settings", show=self.show, record=False, reset=True)
            if self._sink is not None:
                self._sink.close()
            self._contacts.clear()
            self._pages.clear()
