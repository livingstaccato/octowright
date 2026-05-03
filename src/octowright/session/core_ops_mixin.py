# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS

DEFAULT_PREVIEW_CHARS = 4000


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class SessionOpsMixin:
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
                har_path=str(self.har_path) if self.har_path else None,
                markdown_path=str(self.markdown_path) if self.markdown_path else None,
                websocket_path=str(self.websocket_path) if self.websocket_path else None,
            )
            self.recorder.close()
