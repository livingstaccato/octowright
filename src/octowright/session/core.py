# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import ast
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, ConsoleMessage, Page, Video

from ..defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from ..recorder import Recorder

# Cap on inline string-payload returns; opt-in full=True overrides.
DEFAULT_PREVIEW_CHARS = 4000


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _looks_like_binary_text(payload: Any) -> bool:
    """Return True for string payloads that look like ``b'...'`` or ``b\"...\"``.

    This is used as a fallback signal for frameworks that expose binary frames
    as text markers instead of ``bytes``.
    """

    return isinstance(payload, str) and (
        (payload.startswith('b"') and payload.endswith('"')) or (payload.startswith("b'") and payload.endswith("'"))
    )


@dataclass
class BrowserSession:
    instance_id: str
    kind: str
    label: str | None
    url: str
    browser: Browser | None
    context: BrowserContext
    page: Page
    recorder: Recorder
    log_path: Path
    profile: str | None = None
    stabilize: bool = False
    trace: bool = False
    har_path: Path | None = None
    console: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=1000))
    video_path: Path | None = None
    trace_path: Path | None = None
    _video: Video | None = field(default=None, repr=False)
    pages: list[Page] = field(default_factory=list)
    _dialog_policy: str = "dismiss"
    _dialog_prompt_text: str | None = None
    _active_routes: dict[str, Any] = field(default_factory=dict)
    active_frame: Any | None = None  # playwright.async_api.Frame when set
    downloads: list[dict[str, Any]] = field(default_factory=list)
    _pending_download_events: list[Any] = field(default_factory=list)
    _bg_tasks: set[Any] = field(default_factory=set, repr=False)
    markdown_path: Path | None = None
    _last_markdown_capture_url: str | None = None
    _last_markdown_capture_key: str | None = None
    _pending_markdown_capture: Any | None = None
    websocket_path: Path | None = None
    _network_requests: list[dict[str, Any]] = field(default_factory=list)
    # Tracks the most-recent URL passed to MCP-initiated ``navigate(url)`` so
    # that the framenavigated listener (installed by pool._wire_listeners)
    # can de-dup user_navigation events against our own goto calls.
    _last_mcp_navigation: str | None = None

    def __post_init__(self) -> None:
        # Ensure the initial page is always index 0.
        if self.page not in self.pages:
            self.pages.insert(0, self.page)

    def _target(self) -> Any:
        """Return the current action target: active frame if set, else the page."""
        return self.active_frame if self.active_frame is not None else self.page

    def _markdown_cache_path(self) -> Path:
        """Path for the markdown cache file for this session."""
        return self.log_path.with_suffix(".markdown.md")

    def _websocket_cache_path(self) -> Path:
        """Path for the websocket payload cache file for this session."""
        return self.log_path.with_suffix(".websocket.jsonl")

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
        self.websocket_path = self._websocket_cache_path()
        cache_path = self.websocket_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def _extract_markdown(self, html: str) -> str:
        """Convert HTML to markdown using MarkItDown if available."""
        try:
            import inspect

            from markitdown import MarkItDown

            converter = MarkItDown()
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
        except Exception:
            pass

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
        except Exception:
            pass

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
            self.recorder.record("console", **entry)

        self.page.on("console", _on_console)

    def _register_popup(self, page: Page) -> None:
        """Called by context's 'page' event. Appends new page and records the event."""
        from .. import pool as _pool

        self.pages.append(page)
        page_index = len(self.pages) - 1
        self.recorder.record("popup_opened", page_index=page_index, url=page.url)

        # Attach console listener so logs from the new tab are collected.
        def _on_console(msg: ConsoleMessage) -> None:
            entry = {"level": msg.type, "text": msg.text, "page_index": page_index}
            self.console.append(entry)
            self.recorder.record("console", **entry)

        page.on("console", _on_console)
        _pool._wire_listeners(self, page)

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
                    payload_size=len(payload) if hasattr(payload, "__len__") else None,
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
                    cache_payload_size = len(payload) if hasattr(payload, "__len__") else None
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

    def list_pages(self) -> list[dict[str, Any]]:
        """Return [{index, url, title, is_active}, ...]. title is None for unloaded pages."""
        result = []
        for i, p in enumerate(self.pages):
            try:
                url = p.url
            except Exception:
                url = None
            result.append(
                {
                    "index": i,
                    "url": url,
                    "title": None,  # title requires async; callers can use browser_evaluate
                    "is_active": p is self.page,
                }
            )
        return result

    async def switch_page(self, index: int) -> dict[str, Any]:
        """Set self.page to self.pages[index]. Raises IndexError if out of bounds."""
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"page index {index} out of range (0..{len(self.pages) - 1})")
        self.page = self.pages[index]
        self.recorder.record("switch_page", index=index, url=self.page.url)
        return {"index": index, "url": self.page.url, "page_count": len(self.pages)}

    async def close_page(self, index: int) -> dict[str, Any]:
        """Close self.pages[index] and remove it from the list.

        If the closed page was active, switches to the first remaining page.
        Raises RuntimeError if this would close the last page.
        """
        if len(self.pages) <= 1:
            raise RuntimeError("cannot close the last remaining page; use browser_close to shut the whole instance")
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"page index {index} out of range (0..{len(self.pages) - 1})")
        target = self.pages[index]
        was_active = target is self.page
        await target.close()
        self.pages.pop(index)
        if was_active:
            self.page = self.pages[0]
        self.recorder.record("close_page", index=index, was_active=was_active)
        return {
            "closed_index": index,
            "was_active": was_active,
            "active_index": self.pages.index(self.page),
            "page_count": len(self.pages),
        }

    async def navigate(self, url: str) -> dict[str, Any]:
        # Tag the upcoming framenavigated event so pool's user_navigation
        # listener skips it (we already record "navigate" below).
        self._last_mcp_navigation = url
        await self.page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
        title = await self.page.title()
        self.url = url
        self._schedule_markdown_capture()
        self.recorder.record("navigate", url=url)
        return {"url": url, "title": title}

    async def _resolve_semantic_metadata(self, selector: str) -> dict[str, str]:
        """Attempt to resolve the role and role_name of the element at selector."""
        try:
            # Playwright 1.50+ aria_snapshot() returns a YAML-like string for the locator.
            # Example: '- button "Confirm Order"'
            loc = self._target().locator(selector)
            snapshot = await loc.aria_snapshot()
            if snapshot and snapshot.startswith("- "):
                line = snapshot[2:].strip()
                # line is e.g. 'button "Confirm Order"'
                parts = line.split(' "', 1)
                role = parts[0]
                role_name = parts[1].rstrip('"') if len(parts) > 1 else ""
                return {"role": role, "role_name": role_name}
        except Exception:
            pass
        return {}

    async def click(self, selector: str) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        await self._target().click(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click", selector=selector, **meta)

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        await self._target().type(selector, text, delay=delay_ms or 0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("type", selector=selector, text=text, delay_ms=delay_ms, **meta)

    async def fill(self, selector: str, value: str) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        await self._target().fill(selector, value, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill", selector=selector, value=value, **meta)

    async def press_key(self, key: str) -> None:
        await self.page.keyboard.press(key)
        self.recorder.record("press_key", key=key)

    async def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path))
        self.recorder.record("screenshot", path=str(path))
        return path

    async def snapshot(self) -> dict[str, Any]:
        # Playwright 1.50+ removed `page.accessibility.snapshot()` in favor of
        # `aria_snapshot()` on a Locator, which returns a YAML-flavored string.
        aria_yaml = await self.page.locator("html").aria_snapshot()
        self.recorder.record("snapshot")
        return {"aria": aria_yaml, "url": self.page.url, "title": await self.page.title()}

    async def evaluate(self, expression: str) -> Any:
        result = await self._target().evaluate(expression)
        self.recorder.record("evaluate", expression=expression)
        return result

    async def wait_for(self, selector: str | None, text: str | None, timeout_ms: int | None) -> None:
        timeout = timeout_ms or DEFAULT_ACTION_TIMEOUT_MS
        target = self._target()
        if selector:
            await target.wait_for_selector(selector, timeout=timeout)
            self.recorder.record("wait_for", selector=selector, timeout_ms=timeout)
        elif text:
            await target.wait_for_function(
                "t => document.body && document.body.innerText.includes(t)",
                arg=text,
                timeout=timeout,
            )
            self.recorder.record("wait_for", text=text, timeout_ms=timeout)
        else:
            await self.page.wait_for_load_state("networkidle", timeout=timeout)
            self.recorder.record("wait_for", timeout_ms=timeout)

    async def expect_url(self, pattern: str, mode: str = "regex") -> str:
        """Check the page URL against *pattern*. Returns the actual URL on success."""
        actual: str = self.page.url
        if mode == "equals":
            if actual != pattern:
                raise RuntimeError(f'URL mismatch: expected "{pattern}" (equals), got "{actual}"')
        elif mode == "contains":
            if pattern not in actual:
                raise RuntimeError(f'URL mismatch: expected substring "{pattern}" (contains), got "{actual}"')
        elif mode == "regex":
            if not re.search(pattern, actual):
                raise RuntimeError(f'URL mismatch: expected pattern "{pattern}" (regex), got "{actual}"')
        else:
            raise ValueError(f"unknown mode {mode!r}; expected 'regex', 'equals', or 'contains'")
        self.recorder.record("expect_url", pattern=pattern, mode=mode)
        return actual

    async def expect_text(
        self,
        selector: str,
        text: str,
        mode: str = "contains",
        timeout_ms: int | None = None,
    ) -> str:
        """Wait for *selector* and assert its inner text matches *text*. Returns actual text."""
        timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS
        try:
            element = await self._target().wait_for_selector(selector, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(f'element never appeared within {timeout}ms: selector="{selector}"') from exc
        if element is None:
            raise RuntimeError(f'element never appeared within {timeout}ms: selector="{selector}"')
        actual: str = await element.inner_text()
        if mode == "contains":
            if text not in actual:
                raise RuntimeError(f'text mismatch on "{selector}": expected to contain "{text}", got "{actual}"')
        elif mode == "equals":
            if actual != text:
                raise RuntimeError(f'text mismatch on "{selector}": expected "{text}" (equals), got "{actual}"')
        elif mode == "regex":
            if not re.search(text, actual):
                raise RuntimeError(f'text mismatch on "{selector}": expected pattern "{text}" (regex), got "{actual}"')
        else:
            raise ValueError(f"unknown mode {mode!r}; expected 'contains', 'equals', or 'regex'")
        self.recorder.record("expect_text", selector=selector, text=text, mode=mode)
        return actual

    async def expect_selector(
        self,
        selector: str,
        present: bool = True,
        timeout_ms: int | None = None,
    ) -> None:
        """Assert that *selector* is present (or absent) in the page."""
        timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS
        if present:
            try:
                await self._target().wait_for_selector(selector, timeout=timeout)
            except Exception as exc:
                raise RuntimeError(f'selector never appeared within {timeout}ms: "{selector}"') from exc
        else:
            # Poll once — if the element exists right now, that's the failure.
            element = await self._target().query_selector(selector)
            if element is not None:
                raise RuntimeError(f'selector should be absent but was found: "{selector}"')
        self.recorder.record("expect_selector", selector=selector, present=present)

    async def expect_js(self, expression: str, equals: Any = None) -> Any:
        """Evaluate *expression* in the page and assert it is truthy (or equals *equals*)."""
        result = await self._target().evaluate(expression)
        if equals is not None:
            if result != equals:
                raise RuntimeError(
                    f"JS assertion failed: expression={expression!r}, expected={equals!r}, got={result!r}"
                )
        else:
            if not result:
                raise RuntimeError(f"JS assertion failed (not truthy): expression={expression!r}, got={result!r}")
        self.recorder.record("expect_js", expression=expression, equals=equals)
        return result

    async def diagnostic_bundle(
        self,
        *,
        screenshot_dir: Path | None = None,
        console_tail: int = 25,
        html_full: bool = False,
    ) -> dict[str, Any]:
        """Capture a screenshot + last N console messages + page HTML metadata.

        HTML is always written to disk (next to the screenshot) so callers can
        fetch it on demand without dragging it through the MCP response. Inline
        fields: html_path, html_size, html_sha256, html_preview (first
        DEFAULT_PREVIEW_CHARS chars). Pass html_full=True to also include the
        full HTML inline (rarely needed; mostly for tests).
        """
        import hashlib

        bundle: dict[str, Any] = {
            "console_tail": list(self.console)[-console_tail:],
            "url": None,
            "title": None,
            "html_path": None,
            "html_size": None,
            "html_sha256": None,
            "html_preview": None,
            "screenshot": None,
        }
        if html_full:
            bundle["html"] = None
        try:
            bundle["url"] = self.page.url
        except Exception:
            pass
        try:
            bundle["title"] = await self.page.title()
        except Exception:
            pass
        try:
            html = await self.page.content()
            h_dir = screenshot_dir or self.log_path.parent
            h_dir.mkdir(parents=True, exist_ok=True)
            h_path = h_dir / f"{self.instance_id}-fail-{_timestamp()}.html"
            h_path.write_text(html, encoding="utf-8")
            bundle["html_path"] = str(h_path)
            bundle["html_size"] = len(html)
            bundle["html_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
            bundle["html_preview"] = html[:DEFAULT_PREVIEW_CHARS]
            if html_full:
                bundle["html"] = html
        except Exception as e:
            bundle["html_error"] = repr(e)
        try:
            target = (screenshot_dir or self.log_path.parent) / f"{self.instance_id}-fail-{_timestamp()}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(target))
            bundle["screenshot"] = str(target)
        except Exception as e:
            bundle["screenshot_error"] = repr(e)
        return bundle

    async def switch_frame(
        self,
        *,
        selector: str | None = None,
        name: str | None = None,
        url_pattern: str | None = None,
    ) -> dict[str, Any]:
        """Switch the active target to an iframe. Exactly one of selector/name/url_pattern must be given."""
        from . import frames as _frames

        frame, info = await _frames.switch_frame_impl(
            self.page,
            selector=selector,
            name=name,
            url_pattern=url_pattern,
        )
        self.active_frame = frame
        self.recorder.record(
            "switch_frame",
            selector=selector,
            name=name,
            url_pattern=url_pattern,
            index=info["index"],
            frame_url=info["url"],
            frame_name=info["name"],
        )
        return info

    async def reset_frame(self) -> dict[str, Any]:
        """Clear active_frame so tools target the top-level page again."""
        self.active_frame = None
        self.recorder.record("reset_frame")
        return {"ok": True, "active_frame": None}

    def list_frames(self) -> list[dict[str, Any]]:
        """Return [{index, name, url, is_active}, ...] for every frame on the active page."""
        from . import frames as _frames

        return _frames.list_frames_impl(self.page, self.active_frame)

    def _handle_download(self, download: Any) -> None:
        """Registered as page.on('download', ...). Schedules an async save, appends a
        record to self.downloads once the file lands on disk."""
        import asyncio

        from . import downloads as _downloads

        # Fire-and-forget: Playwright dispatches downloads synchronously but saving is async.
        # Task reference is kept on the session to prevent GC collecting it mid-flight (RUF006).
        task = asyncio.create_task(_downloads.save_download(self, download))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def list_downloads(self) -> list[dict[str, Any]]:
        return list(self.downloads)

    async def wait_for_download(self, timeout_ms: int = 15000) -> dict[str, Any]:
        """Block until the next download completes (save-to-disk). Raises TimeoutError
        if no download arrives within timeout_ms. Returns the new download record."""
        from . import downloads as _downloads

        return await _downloads.wait_for_download_impl(self, timeout_ms)

    def _handle_dialog(self, dialog: Any) -> None:
        """Registered as page.on('dialog', ...). Consults self._dialog_policy and acts
        accordingly. Records 'dialog_handled' action with type, message, policy, response.
        For 'manual' policy: do nothing (the test/user is expected to handle it).
        accept/dismiss call dialog.accept()/dialog.dismiss(); accept with a prompt needs
        the prompt_text."""
        import asyncio

        async def _act() -> None:
            try:
                if self._dialog_policy == "accept":
                    if dialog.type == "prompt":
                        await dialog.accept(self._dialog_prompt_text or "")
                    else:
                        await dialog.accept()
                elif self._dialog_policy == "dismiss":
                    await dialog.dismiss()
                # manual: do nothing; test-code is expected to handle
                self.recorder.record(
                    "dialog_handled",
                    dtype=dialog.type,
                    message=dialog.message,
                    policy=self._dialog_policy,
                    prompt_text=self._dialog_prompt_text,
                )
            except Exception as e:
                self.recorder.record("dialog_handler_error", error=repr(e))

        task = asyncio.create_task(_act())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def set_dialog_policy(self, policy: str, prompt_text: str | None = None) -> dict[str, Any]:
        """Update the session's dialog-handling policy. policy in {accept, dismiss, manual}."""
        if policy not in ("accept", "dismiss", "manual"):
            raise ValueError(f"policy must be accept|dismiss|manual, got {policy!r}")
        self._dialog_policy = policy
        self._dialog_prompt_text = prompt_text
        self.recorder.record("set_dialog_policy", policy=policy, prompt_text=prompt_text)
        return {"ok": True, "policy": policy, "prompt_text": prompt_text}

    async def mock_route(
        self,
        url_pattern: str,
        *,
        status: int = 200,
        body: str | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Install a page.route handler that fulfills matching requests with the given
        response. Store the handler in self._active_routes keyed by url_pattern so we can
        remove it later."""

        async def _handler(route: Any) -> None:
            await route.fulfill(
                status=status,
                body=body or "",
                content_type=content_type,
                headers=headers or {},
            )

        if url_pattern in self._active_routes:
            await self.page.unroute(url_pattern, self._active_routes[url_pattern])
        await self.page.route(url_pattern, _handler)
        self._active_routes[url_pattern] = _handler
        self.recorder.record("mock_route", pattern=url_pattern, status=status, content_type=content_type)
        return {"ok": True, "pattern": url_pattern, "status": status}

    async def unmock_route(self, url_pattern: str) -> dict[str, Any]:
        """Remove a previously-installed mock for url_pattern."""
        handler = self._active_routes.pop(url_pattern, None)
        if handler is None:
            raise KeyError(f"no active mock for pattern {url_pattern!r}")
        await self.page.unroute(url_pattern, handler)
        self.recorder.record("unmock_route", pattern=url_pattern)
        return {"ok": True, "pattern": url_pattern}

    async def set_input_files(self, selector: str, paths: list[str]) -> dict[str, Any]:
        """Upload one or more files into an <input type=file> element."""
        await self.page.set_input_files(selector, paths)
        self.recorder.record("set_input_files", selector=selector, paths=paths)
        return {"ok": True, "selector": selector, "paths": paths}

    async def hover(self, selector: str) -> None:
        await self._target().hover(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("hover", selector=selector)

    async def select_option(
        self,
        selector: str,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if value is not None:
            kwargs["value"] = value
        if label is not None:
            kwargs["label"] = label
        if index is not None:
            kwargs["index"] = index
        selected = await self._target().select_option(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS, **kwargs)
        self.recorder.record("select_option", selector=selector, value=value, label=label, index=index)
        return {"ok": True, "selected": selected}

    async def drag(self, source_selector: str, target_selector: str) -> None:
        await self._target().drag_and_drop(source_selector, target_selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("drag", source=source_selector, target=target_selector)

    async def navigate_back(self) -> dict[str, Any]:
        response = await self.page.go_back(timeout=DEFAULT_NAV_TIMEOUT_MS)
        url = self.page.url
        title = await self.page.title()
        self.recorder.record("navigate_back", url=url)
        return {"ok": response is not None, "url": url, "title": title}

    async def resize(self, width: int, height: int) -> dict[str, Any]:
        await self.page.set_viewport_size({"width": width, "height": height})
        self.recorder.record("resize", width=width, height=height)
        return {"ok": True, "width": width, "height": height}

    async def open_url(
        self,
        url: str,
        target: str = "tab",
        width: int = 1024,
        height: int = 768,
    ) -> dict[str, Any]:
        """Open ``url`` in a new tab or window of this instance.

        target='tab' creates a new page in the same context (a regular tab).
        target='window' uses ``window.open`` with popup features so chromium and
        firefox open it in a separate OS window. Both are tracked in
        ``self.pages`` via the context-level page listener.
        """
        if target not in ("tab", "window"):
            raise ValueError(f"target must be 'tab' or 'window', got {target!r}")

        if target == "tab":
            new_page = await self.context.new_page()
            try:
                await new_page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
            except Exception:
                # Surface what we have even if the navigation timed out.
                pass
        else:
            async with self.page.expect_popup(timeout=DEFAULT_NAV_TIMEOUT_MS) as popup_info:
                await self.page.evaluate(
                    "({u, w, h}) => window.open(u, '_blank', `popup,width=${w},height=${h}`)",
                    {"u": url, "w": width, "h": height},
                )
            new_page = await popup_info.value
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_NAV_TIMEOUT_MS)
            except Exception:
                pass

        # _register_popup adds the page to self.pages on the context "page"
        # event; if a race left it absent, append it ourselves.
        if new_page not in self.pages:
            self.pages.append(new_page)
        page_index = self.pages.index(new_page)
        self.recorder.record("open_url", url=url, target=target, page_index=page_index)
        return {
            "ok": True,
            "target": target,
            "page_index": page_index,
            "url": new_page.url,
        }

    def _handle_response(self, response: Any) -> None:
        request = response.request
        self._network_requests.append(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": response.status,
                "status_text": response.status_text,
            }
        )

    def _handle_request_failed(self, request: Any) -> None:
        self._network_requests.append(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": None,
                "failure": request.failure,
            }
        )

    def get_network_requests(
        self,
        url_filter: str | None = None,
        method_filter: str | None = None,
        resource_type_filter: str | None = None,
        since: int | None = None,
    ) -> dict[str, Any]:
        start = since or 0
        sliced = list(self._network_requests[start:])
        if url_filter:
            sliced = [r for r in sliced if url_filter in r.get("url", "")]
        if method_filter:
            sliced = [r for r in sliced if r.get("method", "").upper() == method_filter.upper()]
        if resource_type_filter:
            sliced = [r for r in sliced if r.get("resource_type") == resource_type_filter]
        return {
            "requests": sliced,
            "next_cursor": len(self._network_requests),
            "total": len(self._network_requests),
        }

    # ------------------------------------------------------------------
    # Role / label / text / test-id locator methods
    # ------------------------------------------------------------------

    def _locator(self, **finders: Any) -> Any:
        """Return a Playwright Locator for the given finder kwargs.

        Exactly one of role / label / text / test_id must be supplied. Routes
        through _target() so this also works inside iframes when one is active.
        """
        from . import locators as _locators

        return _locators.build_locator(self._target(), **finders)

    async def click_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Click an element matched by role, label, text, or data-testid."""
        locator = self._locator(**finders)
        await locator.click(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click_by", **finders)
        return {"ok": True}

    async def fill_by(self, value: str, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Fill an input matched by role, label, or data-testid."""
        locator = self._locator(**finders)
        await locator.fill(value, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill_by", value=value, **finders)
        return {"ok": True}

    async def get_text_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Return the inner text of the matched element.

        Useful for assertions that need a value rather than just a boolean match.
        """
        locator = self._locator(**finders)
        await locator.wait_for(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        result = await locator.inner_text()
        self.recorder.record("get_text_by", result=result, **finders)
        return {"ok": True, "text": result}

    async def close(self) -> None:
        try:
            if self.trace:
                self.trace_path = self.log_path.with_suffix(".trace.zip")
                try:
                    await self.context.tracing.stop(path=str(self.trace_path))
                except Exception as e:
                    self.recorder.record("trace_stop_error", error=repr(e))
                    self.trace_path = None
            await self.context.close()
            # Resolve video path after context close (Playwright finalises file on close).
            if self._video is not None:
                try:
                    resolved = await self._video.path()
                    self.video_path = Path(resolved)
                except Exception:
                    pass
        finally:
            if self.browser is not None:
                await self.browser.close()
            self.recorder.record(
                "close",
                video_path=str(self.video_path) if self.video_path else None,
                trace_path=str(self.trace_path) if self.trace_path else None,
                markdown_path=str(self.markdown_path) if self.markdown_path else None,
                websocket_path=str(self.websocket_path) if self.websocket_path else None,
            )
            self.recorder.close()
