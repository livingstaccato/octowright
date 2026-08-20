# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.console_levels import is_diagnostic_console_message
from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from octowright.session._constants import DEFAULT_PREVIEW_CHARS
from octowright.session._protocols import SessionLike
from octowright.session.operation_gate import gated_operation
from octowright.session.viewport_ops import SessionViewportMixin

log = get_logger(__name__)

# Re-exported so existing ``from octowright.session.core_ops_mixin import
# DEFAULT_PREVIEW_CHARS`` callers keep resolving the same singleton from
# ``session._constants`` — the prior in-module shadow could drift out of
# sync with the canonical value in ``session.core`` (both were 4000 but the
# duplication was an accident waiting to happen).
__all__ = ["DEFAULT_PREVIEW_CHARS", "SessionOpsMixin"]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _select_console_tail(messages: list[Any], limit: int) -> list[Any]:
    """Pick ``limit`` console messages, never dropping an error for a log line.

    A plain tail is fragile exactly where it matters: the one line that
    explains a failure (``net::ERR_NETWORK_CHANGED``) can be pushed out of the
    window by a chatty page before anything reads it, leaving the caller with a
    bare selector timeout and no cause. Diagnostic-level messages are therefore
    claimed ahead of ordinary ones.

    But claiming them FIRST, unbounded, is the same failure mirrored: a page
    that logs ``limit`` warnings at load (favicon 404, CSP report, deprecation
    notices -- routine) fills the whole window, and a click that fails twenty
    minutes later ships ten load-time warnings and nothing from around the
    failure. So part of the budget is reserved for the most recent messages
    whatever their level, diagnostics fill the rest newest-first, and anything
    still spare falls back to the plain tail. The result stays in chronological
    order so it reads as a log.

    Entries are COPIED. ``list(session.console)`` copies the list, not the
    dicts inside it, so returning the originals hands a caller live ring-buffer
    entries -- and one consumer editing an entry (to cap its length, say)
    silently rewrote the session's console history for every later reader. The
    entries are flat ``{level, text}`` (plus ``page_index``), so a shallow copy
    covers every value there is; see ``core_io_mixin.attach_console``.
    """
    if limit <= 0:
        return []
    keep = set(range(max(0, len(messages) - _console_recent_reserve(limit)), len(messages)))
    for wanted in (is_diagnostic_console_message, lambda _message: True):
        for index in reversed(range(len(messages))):
            if len(keep) >= limit:
                break
            if wanted(messages[index]):
                keep.add(index)
    return [_copy_console_message(messages[index]) for index in sorted(keep)]


def _console_recent_reserve(limit: int) -> int:
    """How much of the window is held for the most recent messages."""
    return max(1, limit // 2)


def _copy_console_message(message: Any) -> Any:
    return dict(message) if isinstance(message, dict) else message


def _html_preview(html: str, html_preview_chars: int) -> str | None:
    if html_preview_chars <= 0:
        return None
    return html[: min(html_preview_chars, DEFAULT_PREVIEW_CHARS)]


# SessionViewportMixin carries resize / viewport_status / viewport_sync, split
# out when this module reached the 550-LOC ceiling. It is inherited here rather
# than listed alongside the other mixins on BrowserSession so that anything
# holding a SessionOpsMixin — the ops tests build one directly — still finds
# the viewport ops where they have always been.
class SessionOpsMixin(SessionViewportMixin, SessionLike):
    active_frame: Any | None
    video_path: Path | None
    trace_path: Path | None
    _BG_TASK_DRAIN_TIMEOUT_SECONDS = 1.0
    # Max iterations of the iterative drain loop. Bg-task callbacks may
    # schedule more bg work (markdown capture rescheduled by a
    # framenavigated event firing mid-close, for example); without this
    # bound a misbehaving producer could pin _drain_background_tasks
    # indefinitely. Three passes is enough for the realistic chains we
    # ship (the producer dies after the closed page rejects its work).
    _BG_TASK_DRAIN_MAX_PASSES = 3

    @gated_operation("browser_diagnostic_bundle")
    async def diagnostic_bundle(
        self,
        *,
        screenshot_dir: Path | None = None,
        console_tail: int = 0,
        html_preview_chars: int = 0,
        html_full: bool = False,
    ) -> dict[str, Any]:
        """Capture a screenshot + last N console messages + page HTML metadata.

        HTML is always written to disk (next to the screenshot) so callers can
        fetch it on demand without dragging it through the MCP response. Inline
        fields are opt-in: console_tail=N includes the last N console messages,
        html_preview_chars=N includes the first N HTML chars, and html_full=True
        includes the full HTML inline (rarely needed; mostly for tests).
        """
        bundle: dict[str, Any] = {
            "console_tail": _select_console_tail(list(self.console), console_tail),
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
        await self._capture_diagnostic_page_meta(bundle)
        await self._capture_diagnostic_html(
            bundle,
            screenshot_dir=screenshot_dir,
            html_preview_chars=html_preview_chars,
            html_full=html_full,
        )
        await self._capture_diagnostic_screenshot(bundle, screenshot_dir=screenshot_dir)
        return bundle

    @gated_operation("browser_diagnostic_bundle")
    async def _capture_diagnostic_page_meta(self, bundle: dict[str, Any]) -> None:
        try:
            bundle["url"] = self.page.url
        except Exception:
            pass
        try:
            bundle["title"] = await self.page.title()
        except Exception:
            pass

    @gated_operation("browser_diagnostic_bundle")
    async def _capture_diagnostic_html(
        self,
        bundle: dict[str, Any],
        *,
        screenshot_dir: Path | None,
        html_preview_chars: int,
        html_full: bool,
    ) -> None:
        import hashlib

        try:
            html = await self.page.content()
            h_dir = screenshot_dir or self.log_path.parent
            h_dir.mkdir(parents=True, exist_ok=True)
            h_path = h_dir / f"{self.instance_id}-fail-{_timestamp()}.html"
            h_path.write_text(html, encoding="utf-8")
            bundle["html_path"] = str(h_path)
            bundle["html_size"] = len(html)
            bundle["html_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
            bundle["html_preview"] = _html_preview(html, html_preview_chars)
            if html_full:
                bundle["html"] = html
        except Exception as e:
            bundle["html_error"] = repr(e)

    @gated_operation("browser_diagnostic_bundle")
    async def _capture_diagnostic_screenshot(
        self,
        bundle: dict[str, Any],
        *,
        screenshot_dir: Path | None,
    ) -> None:
        try:
            target = (screenshot_dir or self.log_path.parent) / f"{self.instance_id}-fail-{_timestamp()}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(target))
            bundle["screenshot"] = str(target)
        except Exception as e:
            bundle["screenshot_error"] = repr(e)

    @gated_operation("browser_switch_frame")
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
            self,
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

    @gated_operation("browser_reset_frame")
    async def reset_frame(self) -> dict[str, Any]:
        """Clear active_frame so tools target the top-level page again."""
        self.active_frame = None
        self.recorder.record("reset_frame")
        return {"ok": True, "active_frame": None}

    @gated_operation("browser_list_frames")
    async def list_frames(self) -> list[dict[str, Any]]:
        """Return [{index, name, url, is_active}, ...] for every frame on the active page."""
        from octowright.session import frames as _frames

        return await _frames.list_frames_impl(self)

    @gated_operation("browser_hover")
    async def hover(self, selector: str) -> None:
        await self._target().hover(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("hover", selector=selector)

    @gated_operation("browser_select_option")
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
        # value/label can carry a secret dropdown option; they have no inspectable
        # field, so they follow the same selector-less sink policy as press_key /
        # evaluate (scrub only under blanket ``all`` mode). index is a positional
        # int and never a secret. The page still received the real value above.
        from octowright.session.core_page_mixin import _redact_sink_value

        self.recorder.record(
            "select_option",
            selector=selector,
            value=_redact_sink_value(value),
            label=_redact_sink_value(label),
            index=index,
        )
        return {"ok": True, "selected": selected}

    @gated_operation("browser_drag")
    async def drag(self, source_selector: str, target_selector: str) -> None:
        await self._target().drag_and_drop(source_selector, target_selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("drag", source=source_selector, target=target_selector)

    @gated_operation("browser_navigate_back")
    async def navigate_back(self) -> dict[str, Any]:
        response = await self.page.go_back(timeout=DEFAULT_NAV_TIMEOUT_MS)
        url = self.page.url
        title = await self.page.title()
        self.recorder.record("navigate_back", url=url)
        return {"ok": response is not None, "url": url, "title": title}

    @gated_operation("browser_open_url")
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
        # Iterative drain: a bg task whose done-callback schedules a fresh
        # bg task (e.g. _schedule_markdown_capture firing from a
        # framenavigated event mid-close) would not appear in a single
        # snapshot of self._bg_tasks. Loop until the set has nothing new,
        # bounded by _BG_TASK_DRAIN_MAX_PASSES so a pathological producer
        # can't pin the close path indefinitely.
        import asyncio

        current = asyncio.current_task()
        drained: set[Any] = set()
        for _ in range(self._BG_TASK_DRAIN_MAX_PASSES):
            tasks = {task for task in list(self._bg_tasks) if task is not current and task not in drained}
            if not tasks:
                return
            await self._drain_one_pass(tasks)
            for task in tasks:
                self._bg_tasks.discard(task)
                drained.add(task)
        self._warn_if_drain_limit_left_tasks(current, drained)

    def _warn_if_drain_limit_left_tasks(self, current: Any, drained: set[Any]) -> None:
        """Surface a bounded-exit that left bg tasks behind.

        The iteration budget on the drain loop prevents an infinite spin
        from a pathological producer, but on bound-exit any still-spawning
        tasks stay attached to a closed session — invisible to operators
        unless we say so. Pulled out of the loop body to keep its xenon
        rank flat.
        """
        leftover = {task for task in list(self._bg_tasks) if task is not current and task not in drained}
        if not leftover:
            return
        log.warning(
            "octowright.session.bg_task_drain_limit_reached",
            instance_id=getattr(self, "instance_id", None),
            kind=getattr(self, "kind", None),
            max_passes=self._BG_TASK_DRAIN_MAX_PASSES,
            undrained_count=len(leftover),
        )

    async def _drain_one_pass(self, tasks: set[Any]) -> None:
        """Await one batch of bg tasks: collect results from finished ones,
        cancel + await the stragglers. Called repeatedly by
        ``_drain_background_tasks`` until ``_bg_tasks`` is quiescent."""
        import asyncio
        import contextlib

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

    async def close(self) -> None:
        """Tear the session down exactly once, through the durable cutoff.

        A pool-launched session carries a ``_pool_close_requester`` (set by
        ``launch_pipeline._build_session_object``) that routes here through
        the identity-aware pool coordinator -- which owns the
        ``octowright.session.close`` span and the ``octowright_browser_
        closed_total`` counter itself (see ``lifecycle._coordinate_close``),
        so this method does not duplicate either. A session built directly
        (test-only -- no production code constructs ``BrowserSession``
        outside a pool) falls back to ``core_ops_standalone_close``'s
        session-owned coordinator, which reuses the same gate reservation/
        outcome/cancellation mechanics with no pool registry -- and no
        telemetry owner -- to hand off to.
        """
        requester = getattr(self, "_pool_close_requester", None)
        if requester is not None:
            await requester()
        elif getattr(self, "_operation_gate", None) is not None:
            from octowright.session.core_ops_standalone_close import close_standalone

            await close_standalone(self)
        else:
            # A bare mixin double with no gate at all (unit tests that
            # construct SessionOpsMixin.__new__() directly) -- nothing to
            # coordinate, run the teardown body verbatim.
            await self._teardown_after_close_cutoff()

    async def _teardown_after_close_cutoff(self, *, reason: str | None = None) -> None:
        from octowright.session import core_teardown_helpers as _teardown

        try:
            await self._drain_background_tasks()
            await _teardown.stop_trace_if_enabled(self)
            await self.context.close()
            await _teardown.resolve_video_path_after_close(self)
        finally:
            await _teardown.close_browser_handle_after_context_close(self)
            _teardown.flush_and_close_websocket_fh(self)
            self.recorder.record("close", **_teardown.close_recorder_fields(self, reason))
            self.recorder.close()
