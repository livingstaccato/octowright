from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, ConsoleMessage, Page, Video

from .defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from .recorder import Recorder

# NOTE: Dialog handling on popup pages (opened via window.open / target=_blank) is
# not wired automatically. When _register_popup is called by the other agent's context
# 'page' listener, a dialog listener on the new page would need to be added there too.
# For now, dialog auto-handling only applies to the initial page of each session.


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


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
    console: list[dict[str, Any]] = field(default_factory=list)
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

    def __post_init__(self) -> None:
        # Ensure the initial page is always index 0.
        if self.page not in self.pages:
            self.pages.insert(0, self.page)

    def _target(self) -> Any:
        """Return the current action target: active frame if set, else the page."""
        return self.active_frame if self.active_frame is not None else self.page

    def attach_console(self) -> None:
        def _on_console(msg: ConsoleMessage) -> None:
            self.console.append({"level": msg.type, "text": msg.text})

        self.page.on("console", _on_console)

    def _register_popup(self, page: Page) -> None:
        """Called by context's 'page' event. Appends new page and records the event."""
        self.pages.append(page)
        page_index = len(self.pages) - 1
        self.recorder.record("popup_opened", page_index=page_index, url=page.url)
        # Attach console listener so logs from the new tab are collected.
        page.on(
            "console",
            lambda msg: self.console.append(
                {"level": msg.type, "text": msg.text, "page_index": page_index}
            ),
        )

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
            raise RuntimeError(
                "cannot close the last remaining page; use browser_close to shut the whole instance"
            )
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
        await self.page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
        title = await self.page.title()
        self.url = url
        self.recorder.record("navigate", url=url)
        return {"url": url, "title": title}

    async def click(self, selector: str) -> None:
        await self._target().click(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click", selector=selector)

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        await self._target().type(selector, text, delay=delay_ms or 0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("type", selector=selector, text=text, delay_ms=delay_ms)

    async def fill(self, selector: str, value: str) -> None:
        await self._target().fill(selector, value, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill", selector=selector, value=value)

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

    async def diagnostic_bundle(
        self,
        *,
        screenshot_dir: Path | None = None,
        console_tail: int = 25,
    ) -> dict[str, Any]:
        """Capture a screenshot + the last N console messages + current HTML + URL.

        Intended for post-failure diagnostics. Errors in capture are swallowed (we're
        already in an error path — don't compound it).
        """
        bundle: dict[str, Any] = {
            "console_tail": self.console[-console_tail:],
            "url": None,
            "title": None,
            "html": None,
            "screenshot": None,
        }
        try:
            bundle["url"] = self.page.url
        except Exception:
            pass
        try:
            bundle["title"] = await self.page.title()
        except Exception:
            pass
        try:
            bundle["html"] = await self.page.content()
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
        import re
        given = [x for x in (selector, name, url_pattern) if x is not None]
        if len(given) != 1:
            raise ValueError("Exactly one of selector, name, or url_pattern must be provided")
        if selector is not None:
            handle = await self.page.frame_locator(selector).owner().element_handle()
            frame = await handle.content_frame()
            if frame is None:
                raise RuntimeError(f"no frame found for selector {selector!r}")
        elif name is not None:
            frame = self.page.frame(name=name)
            if frame is None:
                raise RuntimeError(f"no frame with name={name!r}")
        else:
            assert url_pattern is not None
            frame = self.page.frame(url=re.compile(url_pattern))
            if frame is None:
                raise RuntimeError(f"no frame matching url_pattern={url_pattern!r}")
        self.active_frame = frame
        frames = self.page.frames
        index = frames.index(frame) if frame in frames else -1
        self.recorder.record(
            "switch_frame",
            selector=selector,
            name=name,
            url_pattern=url_pattern,
            index=index,
            frame_url=frame.url,
            frame_name=frame.name,
        )
        return {"index": index, "url": frame.url, "name": frame.name}

    async def reset_frame(self) -> dict[str, Any]:
        """Clear active_frame so tools target the top-level page again."""
        self.active_frame = None
        self.recorder.record("reset_frame")
        return {"ok": True, "active_frame": None}

    def list_frames(self) -> list[dict[str, Any]]:
        """Return [{index, name, url, is_active}, ...] for every frame on the active page."""
        return [
            {
                "index": i,
                "name": f.name,
                "url": f.url,
                "is_active": f is self.active_frame,
            }
            for i, f in enumerate(self.page.frames)
        ]

    def _handle_download(self, download: Any) -> None:
        """Registered as page.on('download', ...). Schedules an async save, appends a
        record to self.downloads once the file lands on disk."""
        import asyncio

        async def _save() -> None:
            from .defaults import RECORDINGS_DIR
            target_dir = RECORDINGS_DIR / "downloads" / self.instance_id
            target_dir.mkdir(parents=True, exist_ok=True)
            suggested = download.suggested_filename
            target = target_dir / f"{len(self.downloads):03d}-{suggested}"
            try:
                await download.save_as(str(target))
                record = {
                    "url": download.url,
                    "suggested_filename": suggested,
                    "path": str(target),
                    "timestamp": _timestamp(),
                }
                self.downloads.append(record)
                self.recorder.record("download_saved", **record)
                for event in self._pending_download_events:
                    event.set()
                self._pending_download_events.clear()
            except Exception as e:
                self.recorder.record("download_save_error", error=repr(e), url=download.url)

        asyncio.create_task(_save())

    def list_downloads(self) -> list[dict[str, Any]]:
        return list(self.downloads)

    async def wait_for_download(self, timeout_ms: int = 15000) -> dict[str, Any]:
        """Block until the next download completes (save-to-disk). Raises TimeoutError
        if no download arrives within timeout_ms. Returns the new download record."""
        import asyncio
        if self.downloads:
            return self.downloads[-1]
        event = asyncio.Event()
        self._pending_download_events.append(event)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            try:
                self._pending_download_events.remove(event)
            except ValueError:
                pass
            raise TimeoutError(f"no download within {timeout_ms}ms")
        return self.downloads[-1]

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

        asyncio.create_task(_act())

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

    # ------------------------------------------------------------------
    # Role / label / text / test-id locator methods
    # ------------------------------------------------------------------

    def _locator(
        self,
        *,
        role: str | None = None,
        role_name: str | None = None,
        role_exact: bool = False,
        label: str | None = None,
        text: str | None = None,
        test_id: str | None = None,
    ) -> Any:
        """Return a Playwright Locator for the given finder.

        Exactly one of role / label / text / test_id must be supplied. Routes
        through _target() so this also works inside iframes when one is active.
        """
        target = self._target()
        provided = [k for k, v in (("role", role), ("label", label), ("text", text),
                                   ("test_id", test_id)) if v is not None]
        if len(provided) != 1:
            raise ValueError(f"exactly one of role/label/text/test_id must be set; got: {provided}")
        if role is not None:
            kwargs: dict[str, Any] = {}
            if role_name is not None:
                kwargs["name"] = role_name
                kwargs["exact"] = role_exact
            return target.get_by_role(role, **kwargs)  # type: ignore[arg-type]
        if label is not None:
            return target.get_by_label(label)
        if text is not None:
            return target.get_by_text(text)
        return target.get_by_test_id(test_id)

    async def click_by(
        self,
        *,
        role: str | None = None,
        role_name: str | None = None,
        role_exact: bool = False,
        label: str | None = None,
        text: str | None = None,
        test_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Click an element matched by role, label, text, or data-testid."""
        locator = self._locator(role=role, role_name=role_name, role_exact=role_exact,
                                label=label, text=text, test_id=test_id)
        await locator.click(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click_by", role=role, role_name=role_name, label=label,
                             text=text, test_id=test_id)
        return {"ok": True}

    async def fill_by(
        self,
        value: str,
        *,
        role: str | None = None,
        role_name: str | None = None,
        label: str | None = None,
        test_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Fill an input matched by role, label, or data-testid."""
        locator = self._locator(role=role, role_name=role_name,
                                label=label, test_id=test_id)
        await locator.fill(value, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill_by", role=role, role_name=role_name, label=label,
                             test_id=test_id, value=value)
        return {"ok": True}

    async def get_text_by(
        self,
        *,
        role: str | None = None,
        role_name: str | None = None,
        role_exact: bool = False,
        label: str | None = None,
        text: str | None = None,
        test_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Return the inner text of the matched element.

        Useful for assertions that need a value rather than just a boolean match.
        """
        locator = self._locator(role=role, role_name=role_name, role_exact=role_exact,
                                label=label, text=text, test_id=test_id)
        await locator.wait_for(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        result = await locator.inner_text()
        self.recorder.record("get_text_by", role=role, role_name=role_name, label=label,
                             text=text, test_id=test_id, result=result)
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
            )
            self.recorder.close()
