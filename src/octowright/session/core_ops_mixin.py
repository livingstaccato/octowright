# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import counter, span
from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from octowright.session._protocols import SessionLike

_SESSION_CLOSED = counter(
    "octowright_browser_closed_total",
    description="Browser sessions closed cleanly via session.close()",
)

log = get_logger(__name__)

DEFAULT_PREVIEW_CHARS = 4000


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class SessionOpsMixin(SessionLike):
    active_frame: Any | None
    video_path: Path | None
    trace_path: Path | None
    viewport_mode: str
    viewport_width: int | None
    viewport_height: int | None
    _BG_TASK_DRAIN_TIMEOUT_SECONDS = 1.0

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
        from octowright.session import frames as _frames

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
        from octowright.session import frames as _frames

        return _frames.list_frames_impl(self.page, self.active_frame)

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

    async def viewport_status(self) -> dict[str, Any]:
        measured = await self.page.evaluate(
            """() => ({
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                devicePixelRatio: window.devicePixelRatio
            })"""
        )
        page = {
            "width": int(measured.get("innerWidth") or 0),
            "height": int(measured.get("innerHeight") or 0),
        }
        outer = {
            "width": int(measured.get("outerWidth") or 0),
            "height": int(measured.get("outerHeight") or 0),
        }
        mismatch = (
            self.viewport_mode == "fixed"
            and outer["width"] > 0
            and outer["height"] > 0
            and (abs(outer["width"] - page["width"]) > 24 or abs(outer["height"] - page["height"]) > 80)
        )
        return {
            "mode": self.viewport_mode,
            "fixed": self.viewport_mode == "fixed",
            "fluid": self.viewport_mode == "fluid",
            "configured": {"width": self.viewport_width, "height": self.viewport_height},
            "page": page,
            "outer": outer,
            "device_pixel_ratio": measured.get("devicePixelRatio"),
            "mismatch": mismatch,
        }

    async def viewport_sync(self) -> dict[str, Any]:
        status = await self.viewport_status()
        outer = status["outer"]
        width = int(outer["width"] or status["page"]["width"])
        height = int(outer["height"] or status["page"]["height"])
        if width <= 0 or height <= 0:
            raise ValueError("unable to measure a usable viewport size")
        await self.page.set_viewport_size({"width": width, "height": height})
        self.viewport_mode = "fixed"
        self.viewport_width = width
        self.viewport_height = height
        self.recorder.record("resize", width=width, height=height)
        return {"ok": True, "mode": "fixed", "width": width, "height": height}

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
        from octowright.session.core_page_mixin import _reject_unsafe_url

        _reject_unsafe_url(url)

        nav_error: str | None = None
        if target == "tab":
            new_page = await self.context.new_page()
            try:
                await new_page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
            except Exception as exc:
                # Surface the failure to the caller — open_url is a user-action
                # path, so a swallowed nav must not be reported as ok=True.
                log.warning(
                    "octowright.open_url.nav_failed",
                    target="tab",
                    url=url,
                    error=repr(exc),
                )
                nav_error = str(exc)
        else:
            async with self.page.expect_popup(timeout=DEFAULT_NAV_TIMEOUT_MS) as popup_info:
                await self.page.evaluate(
                    "({u, w, h}) => window.open(u, '_blank', `popup,width=${w},height=${h}`)",
                    {"u": url, "w": width, "h": height},
                )
            new_page = await popup_info.value
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_NAV_TIMEOUT_MS)
            except Exception as exc:
                log.warning(
                    "octowright.open_url.nav_failed",
                    target="window",
                    url=url,
                    error=repr(exc),
                )
                nav_error = str(exc)

        # _register_popup adds the page to self.pages on the context "page"
        # event; if a race left it absent, append it ourselves.
        if new_page not in self.pages:
            self.pages.append(new_page)
        page_index = self.pages.index(new_page)
        self.recorder.record(
            "open_url",
            url=url,
            target=target,
            page_index=page_index,
            error=nav_error,
        )
        result: dict[str, Any] = {
            "ok": nav_error is None,
            "target": target,
            "page_index": page_index,
            "url": new_page.url,
        }
        if nav_error is not None:
            result["error"] = nav_error
        return result

    async def _drain_background_tasks(self) -> None:
        import asyncio
        import contextlib

        current = asyncio.current_task()
        tasks = {task for task in list(self._bg_tasks) if task is not current}
        if not tasks:
            return

        done, pending = await asyncio.wait(tasks, timeout=self._BG_TASK_DRAIN_TIMEOUT_SECONDS)
        for task in done:
            if task.cancelled():
                continue
            with contextlib.suppress(Exception):
                task.result()

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for task in tasks:
            self._bg_tasks.discard(task)

    async def close(self) -> None:
        instance_id = getattr(self, "instance_id", None)
        kind = getattr(self, "kind", None)
        with span("octowright.session.close", instance_id=instance_id, kind=kind):
            try:
                await self._close_impl()
            finally:
                _SESSION_CLOSED.add(1, attributes={"kind": kind or "unknown"})
                # Unbind the session-scoped log context so subsequent
                # unrelated logs don't carry this session's identifiers.
                unbind = getattr(self, "unbind_telemetry_context", None)
                if unbind is not None:
                    unbind()

    async def _close_impl(self) -> None:
        try:
            await self._drain_background_tasks()
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
                except Exception as exc:
                    # Per silent-swallow policy: video_path stays None and the
                    # dashboard can't surface the video. Log so the failure is
                    # diagnosable rather than just missing from the UI.
                    log.debug(
                        "octowright.session.video_path_resolve_failed",
                        instance_id=getattr(self, "instance_id", None),
                        error=repr(exc),
                    )
        finally:
            close_handle = getattr(self, "_browser_for_close", None) or self.browser
            if close_handle is not None:
                # context.close() may have already terminated the underlying
                # browser process (persistent contexts in particular). A
                # second .close() then raises and bypasses the recorder
                # terminal-event write below — log and continue.
                try:
                    await close_handle.close()
                except Exception as exc:
                    log.debug(
                        "octowright.session.browser_close_after_context_close_failed",
                        instance_id=getattr(self, "instance_id", None),
                        error=repr(exc),
                    )
            ws_fh = getattr(self, "_websocket_fh", None)
            if ws_fh is not None:
                try:
                    ws_fh.close()
                except Exception as exc:
                    log.debug(
                        "octowright.session.websocket_fh_close_failed",
                        instance_id=getattr(self, "instance_id", None),
                        error=repr(exc),
                    )
                self._websocket_fh = None
            self.recorder.record(
                "close",
                video_path=str(self.video_path) if self.video_path else None,
                trace_path=str(self.trace_path) if self.trace_path else None,
                har_path=str(self.har_path) if self.har_path else None,
                markdown_path=str(self.markdown_path) if self.markdown_path else None,
                websocket_path=str(self.websocket_path) if self.websocket_path else None,
            )
            self.recorder.close()
