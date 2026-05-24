# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import ast
import importlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from playwright.async_api import ConsoleMessage, Page
from provide.telemetry import get_logger

from octowright.session._protocols import SessionLike

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession

log = get_logger(__name__)


def _looks_like_binary_text(payload: Any) -> bool:
    return isinstance(payload, str) and (
        (payload.startswith('b"') and payload.endswith('"')) or (payload.startswith("b'") and payload.endswith("'"))
    )


class SessionIOMixin(SessionLike):
    markdown_path: Path | None
    websocket_path: Path | None
    _last_markdown_capture_url: str | None
    _last_markdown_capture_key: str | None
    _pending_markdown_capture: Any | None

    def _append_websocket_cache(
        self,
        *,
        direction: str,
        id_: Any,
        url: Any,
        payload_preview: str | None = None,
        payload: Any = None,
        payload_size: int | None = None,
    ) -> None:
        """Persist websocket frames in a dedicated cache file."""
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "action": f"websocket_{direction}",
            "id": id_,
            "url": url,
        }
        if payload is not None:
            entry["payload_preview"] = payload_preview or ""
            normalized_size = payload_size
            payload_b64 = None

            if isinstance(payload, bytes | bytearray | memoryview):
                payload_bytes = bytes(payload)
                payload_b64 = __import__("base64").b64encode(payload_bytes).decode("ascii")
                if normalized_size is None:
                    normalized_size = len(payload_bytes)
            elif isinstance(payload, str) and _looks_like_binary_text(payload):
                try:
                    decoded = ast.literal_eval(payload)
                except Exception:
                    decoded = None
                if isinstance(decoded, bytes | bytearray | memoryview):
                    decoded_bytes = bytes(decoded)
                    payload_b64 = __import__("base64").b64encode(decoded_bytes).decode("ascii")
                    if normalized_size is None:
                        normalized_size = len(decoded_bytes)
                else:
                    entry["payload_text"] = payload
            else:
                entry["payload_text"] = payload
            entry["payload_size"] = (
                normalized_size
                if normalized_size is not None
                else (len(payload) if hasattr(payload, "__len__") else None)
            )
            if payload_b64 is not None:
                entry["payload_b64"] = payload_b64
        # Keep a single append-mode file handle for the session: high-frequency
        # WS feeds (game servers, market data) can fire thousands of frames
        # per second, and re-opening for every frame burns syscalls and inode
        # locks. The handle is closed (with a final flush) in
        # ``BrowserSession.close()``.
        #
        # Batched flush: a per-frame ``fh.flush()`` would still cost one
        # syscall per frame. Trade liveness against throughput by flushing
        # when EITHER the frame count or the elapsed-time threshold is hit.
        # See defaults.WEBSOCKET_CACHE_FLUSH_FRAMES / SECONDS.
        from octowright.defaults import WEBSOCKET_CACHE_FLUSH_FRAMES, WEBSOCKET_CACHE_FLUSH_SECONDS

        if self.websocket_path is None:
            self.websocket_path = self._websocket_cache_path()
            self.websocket_path.parent.mkdir(parents=True, exist_ok=True)
        fh = getattr(self, "_websocket_fh", None)
        if fh is None:
            fh = self.websocket_path.open("a", encoding="utf-8")
            self._websocket_fh = fh
            self._websocket_last_flush_ts = time.monotonic()
            self._websocket_frames_since_flush = 0
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        frames = getattr(self, "_websocket_frames_since_flush", 0) + 1
        last_flush = getattr(self, "_websocket_last_flush_ts", 0.0) or time.monotonic()
        now = time.monotonic()
        if frames >= WEBSOCKET_CACHE_FLUSH_FRAMES or (now - last_flush) >= WEBSOCKET_CACHE_FLUSH_SECONDS:
            fh.flush()
            self._websocket_frames_since_flush = 0
            self._websocket_last_flush_ts = now
        else:
            self._websocket_frames_since_flush = frames

    async def _extract_markdown(self, html: str) -> str:
        """Convert HTML to markdown using MarkItDown if available."""
        try:
            import inspect

            markitdown_mod = importlib.import_module("markitdown")
            converter = markitdown_mod.MarkItDown()
            rendered = converter.convert(html)
            if inspect.isawaitable(rendered):
                rendered = await rendered
            if isinstance(rendered, str):
                return rendered
            if rendered is None:
                raise ValueError("markitdown conversion returned empty result")
            for field in ("text", "markdown", "text_content"):
                candidate = getattr(rendered, field, None)
                if candidate:
                    if callable(candidate):
                        candidate = candidate()
                    text = str(candidate)
                    if text.strip():
                        return text
            return str(rendered)
        except Exception as exc:
            # markitdown is optional. Log the failure so a real bug (e.g. a
            # bad markitdown release) is visible during development, but
            # still fall through to the regex stripper below.
            log.debug("octowright.markdown.markitdown_failed", error=repr(exc))

        # Fallback: strip tags for non-structured content rather than returning
        # nothing when optional dependency is missing.
        clean = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()

    async def capture_markdown(self, *, page: Page | None = None, force: bool = False) -> Path | None:
        """Render and persist markdown for the current page.

        Args:
            page: Optional page to capture. Defaults to ``self.page``.
            force: Set to ``True`` to force a refresh even when the URL/key
                   matches the last cached capture.
        """
        import contextlib

        path = self._markdown_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".markdown.md.tmp")
        target = page or self._target()

        try:
            current_url = target.url
        except Exception:
            current_url = None
        key = None if current_url is None else f"{id(target)}:{current_url}"

        if (
            not force
            and current_url is not None
            and current_url == self._last_markdown_capture_url
            and key == self._last_markdown_capture_key
            and path.exists()
        ):
            self.markdown_path = path
            return path

        try:
            html = await target.content()
            markdown = await self._extract_markdown(html)
            temp_path.write_text(markdown, encoding="utf-8")
            temp_path.replace(path)
            self.markdown_path = path
            self._last_markdown_capture_url = current_url
            self._last_markdown_capture_key = key
            self.recorder.record("markdown_cached", path=str(path), url=current_url)
            return path
        except Exception as exc:
            self.recorder.record("markdown_cache_error", error=repr(exc), url=current_url)
            with contextlib.suppress(Exception):
                temp_path.unlink(missing_ok=True)
            return None

    def _schedule_markdown_capture(self, page: Page | None = None, force: bool = False) -> None:
        """Queue a non-blocking markdown-cache refresh."""
        existing = self._pending_markdown_capture
        try:
            if existing is not None and not existing.done():
                return
        except Exception as exc:
            log.debug(
                "octowright.markdown.pending_task_check_failed",
                error=repr(exc),
            )

        import asyncio
        import contextlib

        async def _run() -> None:
            await self.capture_markdown(page=page, force=force)

        task = asyncio.create_task(_run())
        self._pending_markdown_capture = task
        self._bg_tasks.add(task)

        def _cleanup(done: Any) -> None:
            if self._pending_markdown_capture is done:
                self._pending_markdown_capture = None
            self._bg_tasks.discard(done)
            with contextlib.suppress(Exception):
                done.result()

        task.add_done_callback(_cleanup)

    def attach_console(self) -> None:
        def _on_console(msg: ConsoleMessage) -> None:
            entry = {"level": msg.type, "text": msg.text}
            self.console.append(entry)
            self.console_count += 1
            self.recorder.record("console", **entry)

        self.page.on("console", _on_console)

    def _register_popup(self, page: Page) -> None:
        """Called by context's 'page' event. Appends new page and records the event."""
        from octowright.browser_pool.listeners import _wire_listeners

        self.pages.append(page)
        page_index = len(self.pages) - 1
        self.page_count = len(self.pages)
        self.recorder.record("popup_opened", page_index=page_index, url=page.url)

        # Attach console listener so logs from the new tab are collected.
        def _on_console(msg: ConsoleMessage) -> None:
            entry = {"level": msg.type, "text": msg.text, "page_index": page_index}
            self.console.append(entry)
            self.console_count += 1
            self.recorder.record("console", **entry)

        page.on("console", _on_console)
        _wire_listeners(cast("BrowserSession", self), page)

    def _handle_websocket(self, websocket: Any) -> None:
        """Attach frame handlers to a Playwright websocket and record lifecycle events."""
        url = getattr(websocket, "url", None)
        socket_id = getattr(websocket, "id", None)
        if socket_id is None:
            socket_id = id(websocket)

        self.recorder.record("websocket_opened", id=socket_id, url=url)

        def _binary_preview(payload: Any) -> str:
            if isinstance(payload, str) and _looks_like_binary_text(payload):
                # "b'...'" and `b\"...\"` text markers from playwright / logs
                # are rendered as the true byte length when possible.
                try:
                    parsed = ast.literal_eval(payload)
                except Exception:
                    parsed = None
                if isinstance(parsed, bytes | bytearray | memoryview):
                    return f"[binary payload hidden: {len(parsed)} bytes]"

            size = len(payload) if hasattr(payload, "__len__") else None
            if size is None:
                return "[binary payload hidden]"
            return f"[binary payload hidden: {size} bytes]"

        def _preview_payload(payload: Any, *, is_binary: bool = False, max_chars: int = 1024) -> str:
            if payload is None:
                return ""
            if is_binary or isinstance(payload, bytes | bytearray | memoryview):
                return _binary_preview(payload)

            text = payload if isinstance(payload, str) else str(payload)
            if len(text) > max_chars:
                return text[:max_chars] + "…"
            return text

        def _serialise_binary_payload(payload: Any) -> str | None:
            if isinstance(payload, bytes | bytearray | memoryview):
                return __import__("base64").b64encode(bytes(payload)).decode("ascii")
            if isinstance(payload, str) and _looks_like_binary_text(payload):
                try:
                    decoded = ast.literal_eval(payload)
                except Exception:
                    return None
                if isinstance(decoded, bytes | bytearray | memoryview):
                    return __import__("base64").b64encode(bytes(decoded)).decode("ascii")
            return None

        if not hasattr(websocket, "on"):
            return

        def _on_frame(direction: str) -> Any:
            def _handler(frame: Any) -> None:
                payload = getattr(frame, "payload", None)
                is_binary = (
                    bool(getattr(frame, "is_binary", False))
                    or isinstance(payload, bytes | bytearray | memoryview)
                    or _looks_like_binary_text(payload)
                )
                self.recorder.record(
                    f"websocket_{direction}",
                    id=socket_id,
                    url=url,
                    is_binary=is_binary,
                    payload_preview=_preview_payload(payload, is_binary=is_binary),
                    payload_size=(len(payload) if payload is not None and hasattr(payload, "__len__") else None),
                )
                payload_b64 = _serialise_binary_payload(payload)
                cache_entry_payload = payload_b64 if payload_b64 is not None else payload
                cache_payload_size = None
                if isinstance(payload, bytes | bytearray | memoryview):
                    cache_payload_size = len(payload)
                elif isinstance(payload, str) and _looks_like_binary_text(payload):
                    try:
                        decoded = ast.literal_eval(payload)
                    except Exception:
                        cache_payload_size = len(payload)
                    else:
                        if isinstance(decoded, bytes | bytearray | memoryview):
                            cache_payload_size = len(decoded)
                        else:
                            cache_payload_size = len(payload)
                else:
                    cache_payload_size = len(payload) if payload is not None and hasattr(payload, "__len__") else None
                try:
                    self._append_websocket_cache(
                        direction=direction,
                        id_=socket_id,
                        url=url,
                        payload_preview=_preview_payload(payload, is_binary=is_binary),
                        payload=cache_entry_payload,
                        payload_size=cache_payload_size,
                    )
                except Exception:
                    self.recorder.record("websocket_cache_error", id=socket_id, url=url)

            return _handler

        def _on_close() -> None:
            self.recorder.record("websocket_closed", id=socket_id, url=url)

        def _on_error(error: Any) -> None:
            self.recorder.record(
                "websocket_error",
                id=socket_id,
                url=url,
                error=str(error),
            )

        try:
            websocket.on("framesent", _on_frame("framesent"))
            websocket.on("framereceived", _on_frame("framereceived"))
            websocket.on("close", _on_close)
            websocket.on("socketerror", _on_error)
        except Exception:
            return
