# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from playwright.async_api import Error as PlaywrightError
from provide.telemetry import get_logger

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_ASSERTION_TEXT
from octowright.drawn_text import (
    COLLECT_RENDERED_TEXT_JS,
    ELEMENT_LIMIT,
    check_forbidden_text,
    collect_args,
    contains,
    fold_frame_result,
    leak_message,
    new_scan_summary,
    resolve_element_limit,
    skip_gone_frame,
    truncation_message,
)
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
from octowright.session.rendered_text import snapshot_drawn_text
from octowright.session.timeouts import bounded

_WAIT_FOR_POLL_SECONDS = 0.05
#: How long expect_network_clean waits, by default, for in-flight requests to end.
NETWORK_SETTLE_TIMEOUT_MS = 5000
#: Quiet time after the last in-flight request ends, for the follow-up it triggers.
_NETWORK_QUIET_SECONDS = 0.1

log = get_logger(__name__)


class SessionExpectMixin(SessionLike):
    # Written by mark_network_clean below; declared so the assignment does not
    # narrow the dataclass field's Optional type.
    _network_clean_explicit_mark: tuple[int, int, int] | None
    _inflight_evicted: int
    _inflight_evicted_explicit_mark: int

    @gated_operation("browser_expect_poll")
    async def _poll_until(self, timeout_ms: int, predicate: Any, label: str) -> None:
        deadline = None if timeout_ms == 0 else time.monotonic() + (timeout_ms / 1000)
        last_error: Exception | None = None
        while True:
            try:
                if await predicate():
                    return
                last_error = None
            except Exception as exc:
                last_error = exc
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = f": {last_error}" if last_error is not None else ""
                    raise TimeoutError(f"condition not met within {timeout_ms}ms: {label}{detail}") from last_error
                await asyncio.sleep(min(_WAIT_FOR_POLL_SECONDS, remaining))
            else:
                await asyncio.sleep(_WAIT_FOR_POLL_SECONDS)

    @gated_operation("browser_expect_url")
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

    @gated_operation("browser_expect_text")
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

    @gated_operation("browser_expect_selector")
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

    @gated_operation("browser_expect_js")
    async def expect_js(
        self,
        expression: str,
        equals: Any = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        """Evaluate *expression* and assert it, bounded by ``timeout_ms`` when set."""
        timeout = None if timeout_ms is None else timeout_ms / 1000
        result = await bounded(
            self._target().evaluate(expression),
            operation="browser_expect_js",
            timeout=timeout,
        )
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

    async def _settle_network(self, timeout_ms: int) -> int:
        """Wait until no request is in flight for a quiet interval; return what is still pending.

        Bounded: a page that long-polls never settles, and the check then judges
        what has happened so far instead of hanging or failing on a request
        that has not failed.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self.pending_requests() == 0:
                await asyncio.sleep(min(_NETWORK_QUIET_SECONDS, max(0.0, deadline - time.monotonic())))
                if self.pending_requests() == 0:
                    return 0
            else:
                await asyncio.sleep(min(_WAIT_FOR_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        return self.pending_requests()

    @gated_operation("browser_mark_network_clean")
    async def mark_network_clean(self) -> None:
        """Start the window ``expect_network_clean(since="mark")`` judges; it spans macro runs."""
        # A mark means a check will follow, so the journey's requests are waited for.
        self.enable_inflight_tracking()
        self._network_clean_explicit_mark = self._network_clean_counts()
        self._inflight_evicted_explicit_mark = self._inflight_evicted
        self.recorder.record("mark_network_clean")

    @gated_operation("browser_expect_network_clean")
    async def expect_network_clean(
        self, http_errors: bool = False, since: str = "run", settle_timeout_ms: int | None = None
    ) -> dict[str, int]:
        """Assert no failed requests (aborts excepted) and no page errors in the window.

        ``since="run"`` (default) judges the current macro run; ``since="mark"``
        judges everything since the last ``mark_network_clean`` step, across
        runs, so a separate verify macro can judge the journey before it.
        "Failed" is Playwright's ``requestfailed``: the request got no response.
        ``http_errors=True`` also fails on a 4xx/5xx page load or API call
        (``request_failures.HTTP_ERROR_RESOURCE_TYPES``); off by default because
        a 4xx is sometimes the answer a journey expects. Requests still in
        flight are waited for, up to ``settle_timeout_ms`` (``0`` judges at
        once); any still pending then are reported as ``in_flight``, not failed.
        Tracking what is in flight starts at a ``mark_network_clean`` step or a
        macro run containing this check; called outside both, this call starts
        it and so waits only for requests started from now. Requests dropped
        from that bounded tracking are reported as ``in_flight_untracked`` (only
        when there are any): they may still be running after the wait returns.
        The error carries counts only, because a failed URL or an exception
        message can carry a credential.
        """
        if since not in ("run", "mark"):
            raise ValueError(f'unknown since={since!r}; expected "run" or "mark"')
        self.enable_inflight_tracking()
        settle = NETWORK_SETTLE_TIMEOUT_MS if settle_timeout_ms is None else settle_timeout_ms
        in_flight = await self._settle_network(settle) if settle > 0 else self.pending_requests()
        failed, page_errors, http_error_count = self.network_failures_since(since)
        counts = {"failed_requests": failed, "page_errors": page_errors}
        if http_errors:
            counts["http_errors"] = http_error_count
        if any(counts.values()):
            detail = f"{failed} failed request(s), {page_errors} page error(s)"
            if http_errors:
                detail += f", {http_error_count} HTTP error(s)"
            raise RuntimeError(f"network not clean: {detail}")
        options = {"http_errors": http_errors, "since": since, "settle_timeout_ms": settle_timeout_ms}
        defaults_ = {"http_errors": False, "since": "run", "settle_timeout_ms": None}
        self.recorder.record("expect_network_clean", **{k: v for k, v in options.items() if v != defaults_[k]})
        result = {**counts, "in_flight": in_flight}
        untracked = self.untracked_requests_since(since)
        if untracked > 0:
            result["in_flight_untracked"] = untracked
        return result

    @gated_operation("browser_expect_no_text_scan")
    async def _scan_drawn_text(
        self, frames: list[Any], text: str, selector: str, timeout: float, limit: int = ELEMENT_LIMIT
    ) -> dict[str, Any]:
        """Raise if any frame draws *text*; return what the scan covered.

        ``frames[0]`` is the page's main frame, or the one frame the check is
        scoped to; the rules are ``drawn_text``'s, which the exported CLI shares.
        """
        summary = new_scan_summary()
        for position, frame in enumerate(frames):
            try:
                found = await bounded(
                    frame.evaluate(COLLECT_RENDERED_TEXT_JS, collect_args(selector, limit)),
                    operation="browser_expect_no_text",
                    timeout=timeout,
                )
            except PlaywrightError as exc:
                if not skip_gone_frame(summary, position, frame.is_detached(), exc):
                    raise
                log.debug("expect_no_text.frame_skipped", reason="frame_gone")
                continue
            if not fold_frame_result(summary, position, found, text, selector):
                log.debug("expect_no_text.frame_skipped", reason="no_result")
        return summary

    @gated_operation("browser_expect_no_text_snapshot")
    async def _snapshot_leaks(self, text: str, timeout: float) -> bool:
        """Chromium only: whether its DOM snapshot draws *text*, closed shadow roots included."""
        # Local import: the macros package imports the session stack.
        from octowright.macros.rendered_surface import SNAPSHOT_PARAMS

        cdp = await bounded(
            self.page.context.new_cdp_session(self.page), operation="browser_expect_no_text", timeout=timeout
        )
        try:
            snapshot = await bounded(
                cdp.send("DOMSnapshot.captureSnapshot", SNAPSHOT_PARAMS),
                operation="browser_expect_no_text",
                timeout=timeout,
            )
        finally:
            await cdp.detach()
        # Drawn text only, overlays left out (see snapshot_drawn_text): the
        # screenshot scanner's findings err toward refusal, and a check that
        # failed on Chromium alone for a URL nobody can read would mean a
        # different thing on each engine.
        return contains(snapshot_drawn_text(snapshot), text)

    @gated_operation("browser_expect_no_text")
    async def expect_no_text(
        self, text: str, selector: str = "body", timeout_ms: int | None = None, element_limit: int | None = None
    ) -> dict[str, Any]:
        """Assert *text* is not drawn in any element matching *selector*; return what was checked.

        Drawn means text a reader can see (``octowright.drawn_text`` has the
        full definition): rendered text of every match, open shadow roots,
        visible form values and placeholders, a broken image's alt text, a
        select's option labels and CSS generated content -- in every frame when
        *selector* is ``body``. Password fields, attribute text such as a
        resource address, and anything not rendered do not count. On Chromium
        the page's DOM snapshot is also checked, which reaches closed shadow
        roots; other engines cannot. Canvas, video and other pixel-only content
        cannot be text-checked. Text compares ignoring case, whitespace and
        invisible characters (``drawn_text.normalize``). octowright's
        own overlays are not the page and are left out of both scans.

        Returns ``{matched, frames_scanned, frames_skipped, truncated,
        snapshot}``, ``snapshot`` being ``"checked"``, ``"skipped"`` (the check
        is scoped to a selector or a frame) or ``"unsupported"`` (not Chromium).
        ``matched == 0`` passes -- nothing matched, so nothing is drawn -- and
        the result is how a caller tells that from a page checked and clean. A
        page with more elements under the selector than the limit is refused
        unless the text was found: a security check does not pass on a page it
        only partly read. The limit is *element_limit*, else
        ``OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT``, else ``ELEMENT_LIMIT``
        (``drawn_text.resolve_element_limit``). *text* is treated as a secret, so neither the
        error, the result nor the recording repeats it.
        """
        check_forbidden_text(text)
        limit = resolve_element_limit(element_limit, os.environ)
        timeout = (timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS) / 1000
        target = self._target()
        whole_page = selector == "body" and target is self.page
        # The whole page is every frame; a selector or an active frame is just the target.
        frames = list(self.page.frames) if whole_page else [target]
        result = await self._scan_drawn_text(frames, text, selector, timeout, limit)
        result["snapshot"] = "unsupported" if self.kind != "chromium" else "checked" if whole_page else "skipped"
        if result["snapshot"] == "checked" and await self._snapshot_leaks(text, timeout):
            raise RuntimeError(leak_message(text, selector, "DOM snapshot"))
        if result["truncated"]:
            raise RuntimeError(truncation_message(selector, limit))
        # The marker, never the text; the keyed digest is what lets save_macro
        # bind the marker to the parameter it stood for (see
        # macros.privacy.assertion_text_digest). Local import: macros imports
        # the session stack.
        from octowright.macros.privacy import assertion_text_digest

        self.recorder.record(
            "expect_no_text",
            selector=selector,
            text=REDACTED_ASSERTION_TEXT,
            text_digest=assertion_text_digest(text),
            # A step's own limit is an input, so replay keeps it; the default is not recorded.
            **({"element_limit": element_limit} if element_limit is not None else {}),
            **result,
        )
        return result
