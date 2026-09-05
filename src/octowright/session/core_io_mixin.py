# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import ast
import base64
import importlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from playwright.async_api import ConsoleMessage, Page
from provide.telemetry import get_logger

from octowright._wire_utils import looks_like_binary_text as _looks_like_binary_text
from octowright.defaults import WEBSOCKET_CACHE_FLUSH_FRAMES, WEBSOCKET_CACHE_FLUSH_SECONDS
from octowright.session import websocket_view
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
from octowright.session.timeouts import bounded

_BYTE_LIMIT_OFF_TOKENS = {"", "0", "off", "never", "none", "disabled", "false", "no"}


#: Sockets kept in the live registry. A page that opens a socket per retry
#: would otherwise grow it without limit for the life of the session. Closed
#: sockets are evicted before open ones, so "what is connected right now"
#: survives a churny page.
WEBSOCKET_REGISTRY_MAX = 64
#: Chars of a frame preview written to the MAIN session JSONL. The sidecar
#: keeps a long one (that is the file the read tools serve from); this file has
#: no ceiling on by default and is read by ``browser_tail_recording``, the
#: dashboard event stream and ``capture_create(kind="recording")``, none of
#: which asked about websockets. It was ``""`` for every frame until payload
#: capture was fixed, so nothing had ever measured what a real one costs there.
WEBSOCKET_RECORD_PREVIEW_CHARS = 128


def _websocket_max_bytes() -> int:
    """``OCTOWRIGHT_WEBSOCKET_MAX_BYTES`` — per-session WS sidecar byte ceiling.

    OFF (0) by default. A positive value stops appending frames once the sidecar
    file would exceed it, writing a single ``websocket_truncated`` marker so
    replay/inspection see the cut. Falsey/unparsable/non-positive keeps it off.
    """
    raw = os.environ.get("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", "").strip().lower()
    if raw in _BYTE_LIMIT_OFF_TOKENS:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


if TYPE_CHECKING:
    from octowright.session.core import BrowserSession

log = get_logger(__name__)


class SessionIOMixin(SessionLike):
    markdown_path: Path | None
    websocket_path: Path | None
    _last_markdown_capture_url: str | None
    _last_markdown_capture_key: str | None
    _pending_markdown_capture: Any | None
    _last_markdown_capture_error: Exception | None

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
                payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
                if normalized_size is None:
                    normalized_size = len(payload_bytes)
            elif isinstance(payload, str) and _looks_like_binary_text(payload):
                try:
                    decoded = ast.literal_eval(payload)
                except Exception:
                    decoded = None
                if isinstance(decoded, bytes | bytearray | memoryview):
                    decoded_bytes = bytes(decoded)
                    payload_b64 = base64.b64encode(decoded_bytes).decode("ascii")
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
        # See defaults.WEBSOCKET_CACHE_FLUSH_FRAMES / SECONDS — imported at
        # module scope above so the hot path doesn't pay a sys.modules
        # lookup per frame.
        if self.websocket_path is None:
            self.websocket_path = self._websocket_cache_path()
            self.websocket_path.parent.mkdir(parents=True, exist_ok=True)
        fh = getattr(self, "_websocket_fh", None)
        # One time.monotonic() per call, reused for both the elapsed-time
        # check and (on first write) the init timestamp / (on flush) the
        # new last_flush stamp.
        now = time.monotonic()
        if fh is None:
            fh = self.websocket_path.open("a", encoding="utf-8")
            self._websocket_fh = fh
            self._websocket_last_flush_ts = now
            self._websocket_frames_since_flush = 0
            self._websocket_bytes = 0
            self._websocket_truncated = False
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        if self._ws_over_ceiling(fh, len(line.encode("utf-8"))):
            return
        fh.write(line)
        # Both fields are dataclass attributes initialized to 0 / 0.0 and
        # are set to live values in the `fh is None` branch above before we
        # ever reach this point, so direct attribute access is safe. The
        # previous getattr / `or time.monotonic()` defensive style masked
        # any future regression that broke the init invariant.
        frames = self._websocket_frames_since_flush + 1
        last_flush = self._websocket_last_flush_ts
        if frames >= WEBSOCKET_CACHE_FLUSH_FRAMES or (now - last_flush) >= WEBSOCKET_CACHE_FLUSH_SECONDS:
            fh.flush()
            self._mark_websocket_flushed(now)
        else:
            self._websocket_frames_since_flush = frames

    def _ws_over_ceiling(self, fh: Any, line_bytes: int) -> bool:
        """Enforce ``OCTOWRIGHT_WEBSOCKET_MAX_BYTES``. Return True if this frame
        must be dropped because the sidecar reached the ceiling, writing a
        one-time ``websocket_truncated`` marker on the edge. Off → always False."""
        limit = _websocket_max_bytes()
        if limit <= 0:
            return False
        if self._websocket_truncated:
            return True
        if self._websocket_bytes + line_bytes > limit:
            marker = {
                "action": "websocket_truncated",
                "limit_bytes": limit,
                "bytes_written": self._websocket_bytes,
            }
            fh.write(json.dumps(marker) + "\n")
            fh.flush()
            self._websocket_truncated = True
            return True
        self._websocket_bytes += line_bytes
        return False

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

    @gated_operation("markdown_capture")
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
            # Cap at 10s: pages with busy JS (SPAs retrying auth, WebSocket floods)
            # can hold the CDP evaluation lock for 60+ seconds, which stalls the
            # asyncio event loop and delays every other MCP response in the process.
            # operation="markdown_capture" -- the enclosing gate name, and the
            # honest label here: this runs automatically after nearly every
            # navigate/page-load/launch via _schedule_markdown_capture, so the
            # caller is very often not browser_read_markdown at all.
            html = await bounded(target.content(), operation="markdown_capture", timeout=10.0)
            markdown = await self._extract_markdown(html)
            temp_path.write_text(markdown, encoding="utf-8")
            temp_path.replace(path)
            self.markdown_path = path
            self._last_markdown_capture_url = current_url
            self._last_markdown_capture_key = key
            self._last_markdown_capture_error = None
            self.recorder.record("markdown_cached", path=str(path), url=current_url)
            return path
        except Exception as exc:
            # Recorded so a caller that gets None back (this method never
            # raises) can tell a hung target apart from an ordinary
            # rendering failure -- see _last_markdown_capture_error's
            # docstring in session/core.py. The automatic path (scheduled
            # after navigate/launch) stays best-effort either way: nothing
            # here re-raises, so an ordinary navigate never fails because a
            # markdown cache refresh timed out in the background.
            self._last_markdown_capture_error = exc
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

    def _register_websocket(self, socket_id: Any, url: Any, binding_id: Any = None) -> None:
        """Record a newly opened socket, evicting a closed one if at capacity.

        Keys by ``str(socket_id)`` HERE rather than coercing the caller's
        variable, so one place decides the registry's key type and the caller
        keeps whatever it was given.
        """
        socket_id = str(socket_id)
        if socket_id not in self._websockets and len(self._websockets) >= WEBSOCKET_REGISTRY_MAX:
            self._evict_one_websocket()
        self._websockets[socket_id] = {
            "id": socket_id,
            # What the binding called it, when it says anything at all. Never
            # the key -- see ``_next_websocket_id``.
            "binding_id": binding_id,
            "url": url,
            "opened_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "closed_at": None,
            "framesent": 0,
            "framereceived": 0,
            "bytes": 0,
            "error": None,
        }

    def _next_websocket_id(self) -> str:
        """A key this session owns, never an object address and never a guess.

        ``id(websocket)`` was the fallback, and CPython reissues an address
        once the object is freed -- so a page opening a socket per retry could
        hand a NEW socket the key of a finished one, overwriting its record
        and merging two sockets' frames into one stream under a single
        socket_id. A per-session counter cannot collide however churny the
        page is.

        A binding-supplied ``.id`` is deliberately NOT used as the key either.
        Nothing guarantees such a value is unique within the session, and
        ``_register_websocket`` overwrites on a repeat -- so believing a
        binding that handed out a duplicate (or the literal ``ws-1``) would
        reopen exactly the merging bug the counter closes. It is kept beside
        the key as ``binding_id`` instead, where it can be correlated without
        deciding identity.
        """
        self._websocket_seq += 1
        return f"ws-{self._websocket_seq}"

    def _evict_one_websocket(self) -> None:
        """Drop the oldest CLOSED socket, else the oldest of any state.

        Preferring closed ones is the whole point of a bounded registry here:
        the question this answers is "what is connected right now", and
        evicting a live socket to retain a finished one would answer it wrong.
        Insertion order is open order, so the first match is the oldest.
        """
        victim = next((key for key, entry in self._websockets.items() if entry["closed_at"]), None)
        if victim is None:
            victim = next(iter(self._websockets), None)
        if victim is not None:
            del self._websockets[victim]
            self._websockets_dropped += 1

    def _note_websocket_frame(self, socket_id: Any, direction: str, size: int | None) -> None:
        entry = self._websockets.get(str(socket_id))
        if entry is None:
            return
        entry[direction] = entry.get(direction, 0) + 1
        entry["bytes"] = entry.get("bytes", 0) + (size or 0)

    def _flush_websocket_cache(self) -> None:
        """Push buffered frames to disk so a read sees them.

        Writes are batched (see ``_append_websocket_cache``), so without this a
        reader gets everything except the most recent frames -- which are the
        ones someone watching a live stream actually wants. Best-effort: a
        failed flush must not fail the read.
        """
        handle = getattr(self, "_websocket_fh", None)
        if handle is None:
            return
        try:
            handle.flush()
        except Exception as exc:
            log.debug("octowright.session.websocket_flush_failed", error=repr(exc))
        else:
            self._mark_websocket_flushed()

    def _mark_websocket_flushed(self, now: float | None = None) -> None:
        """Restart BOTH halves of the batching decision.

        ``_append_websocket_cache`` flushes on frames-since OR seconds-since,
        so a caller that reset only the counter left the next frame written
        seeing a stale stamp, taking the time branch and flushing again
        immediately -- one batch's worth of syscall batching undone by every
        read. One method rather than the pair written out twice, since the
        defect being repaired WAS a second copy resetting one field.
        """
        self._websocket_frames_since_flush = 0
        self._websocket_last_flush_ts = time.monotonic() if now is None else now

    def get_websocket_messages(
        self,
        *,
        cursor: int = 0,
        socket_id: str | None = None,
        direction: str | None = None,
        include_payloads: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Frames this page's websockets carried. Flushes first, then reads."""
        self._flush_websocket_cache()
        return websocket_view.read_frames(
            self.websocket_path,
            cursor=cursor,
            socket_id=socket_id,
            direction=direction,
            include_payloads=include_payloads,
            limit=limit,
            capture_truncated=self._websocket_truncated,
        )

    def get_websocket_summary(self) -> dict[str, Any]:
        """Which sockets this page has opened, and which are still connected."""
        return websocket_view.summarize_sockets(self._websockets, self._websockets_dropped)

    def _handle_websocket(self, websocket: Any) -> None:
        """Attach frame handlers to a Playwright websocket and record lifecycle events."""
        url = getattr(websocket, "url", None)
        socket_id = self._next_websocket_id()
        binding_id = getattr(websocket, "id", None)

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

        def _on_frame(direction: str) -> Any:
            def _handler(frame: Any) -> None:
                # playwright-python emits the payload ITSELF -- a str, or bytes
                # for a binary opcode (``_network.WebSocket._on_frame_sent``
                # calls ``emit(FrameSent, data)``). Only Node's API wraps it in
                # a frame object carrying ``.payload``, which is where the
                # original attribute read came from -- and since neither str
                # nor bytes has that attribute, it resolved to None for EVERY
                # frame. The sidecar, its byte ceiling and its batched flush
                # were therefore all persisting rows with no content in them.
                # Keep the attribute read as the fallback so a frame-object
                # shape still works if the binding ever grows one.
                payload = getattr(frame, "payload", frame)
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
                    # Short here, long in the sidecar: see
                    # WEBSOCKET_RECORD_PREVIEW_CHARS. ``payload_size`` is the
                    # frame's real length either way, so capping the text
                    # costs nothing a reader of this file needed.
                    payload_preview=_preview_payload(
                        payload, is_binary=is_binary, max_chars=WEBSOCKET_RECORD_PREVIEW_CHARS
                    ),
                    payload_size=(len(payload) if payload is not None and hasattr(payload, "__len__") else None),
                )
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
                self._note_websocket_frame(socket_id, direction, cache_payload_size)
                try:
                    self._append_websocket_cache(
                        direction=direction,
                        id_=socket_id,
                        url=url,
                        payload_preview=_preview_payload(payload, is_binary=is_binary),
                        payload=payload,
                        payload_size=cache_payload_size,
                    )
                except Exception:
                    self.recorder.record("websocket_cache_error", id=socket_id, url=url)

            return _handler

        def _on_close() -> None:
            entry = self._websockets.get(socket_id)
            if entry is not None:
                entry["closed_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            self.recorder.record("websocket_closed", id=socket_id, url=url)

        def _on_error(error: Any) -> None:
            entry = self._websockets.get(socket_id)
            if entry is not None:
                entry["error"] = str(error)
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
            # Also the "this object has no ``.on`` at all" case, which used to
            # need its own guard above and reaches the identical outcome here.
            return

        # Registered only once the listeners are attached. Registering first
        # left a socket whose wiring failed in the table with no ``close``
        # handler to ever set ``closed_at`` -- permanently "open", and since
        # eviction prefers closed entries, evicted LAST, so a page that tripped
        # this repeatedly pushed out live sockets instead.
        self._register_websocket(socket_id, url, binding_id)
        self.recorder.record("websocket_opened", id=socket_id, url=url)
