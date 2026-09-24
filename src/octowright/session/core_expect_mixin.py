# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_ASSERTION_TEXT
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
from octowright.session.rendered_text import COLLECT_RENDERED_TEXT_JS, collect_args, contains
from octowright.session.timeouts import bounded

_WAIT_FOR_POLL_SECONDS = 0.05
#: How long expect_network_clean waits, by default, for in-flight requests to end.
NETWORK_SETTLE_TIMEOUT_MS = 5000
#: Quiet time after the last in-flight request ends, for the follow-up it triggers.
_NETWORK_QUIET_SECONDS = 0.1


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
    async def _scan_drawn_text(self, frames: list[Any], text: str, selector: str, timeout: float) -> str:
        """Raise if any frame draws *text*; return the text octowright's overlays show."""
        overlay = ""
        for frame in frames:
            found = await bounded(
                frame.evaluate(COLLECT_RENDERED_TEXT_JS, collect_args(selector)),
                operation="browser_expect_no_text",
                timeout=timeout,
            )
            if not isinstance(found, dict):
                continue
            if contains(found.get("pieces", []), text):
                raise RuntimeError(f'forbidden text ({len(text)} chars) is rendered in "{selector}"')
            overlay += str(found.get("overlay", ""))
        return overlay

    @gated_operation("browser_expect_no_text_snapshot")
    async def _snapshot_leaks(self, text: str, timeout: float) -> list[str]:
        """Chromium only: what its DOM snapshot says is drawn, closed shadow roots included."""
        # Local import: the macros package imports the session stack.
        from octowright.macros.rendered_surface import OPAQUE_ELEMENTS, SNAPSHOT_PARAMS, rendered_leaks

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
        # The snapshot scan was built to refuse screenshots, so two of its reasons
        # do not mean "this text is drawn". Form values are the in-page scan's to
        # judge (it knows a password field draws only mask characters). And a
        # shown iframe/canvas/video is refused there whatever it holds, because
        # a screenshot cannot see into it; here frames are scanned directly and
        # a canvas holds no text to compare.
        opaque = {f"visible {name.lower()}" for name in OPAQUE_ELEMENTS}
        return [r for r in rendered_leaks(snapshot, [text]) if r != "form value" and r not in opaque]

    @gated_operation("browser_expect_no_text")
    async def expect_no_text(self, text: str, selector: str = "body", timeout_ms: int | None = None) -> None:
        """Assert *text* is not drawn in any element matching *selector*.

        Drawn means what a reader can see: rendered text of every match, open
        shadow roots, visible form values and placeholders, and CSS generated
        content -- in every frame when *selector* is ``body``. Password fields
        and hidden elements do not count. On Chromium the page's DOM snapshot is
        also checked, which reaches closed shadow roots. Canvas, video and other
        pixel-only content cannot be text-checked. Text compares as the
        screenshot scanner compares (``macros.redaction_text.normalize``):
        ignoring case, whitespace and invisible characters. No match means
        nothing is drawn, so it passes. octowright's own overlays are not the
        page and are skipped. *text* is treated as a secret, so neither the
        error nor the recording repeats it.
        """
        _check_forbidden_text(text)
        timeout = (timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS) / 1000
        target = self._target()
        whole_page = selector == "body" and target is self.page
        frames = _frames_to_scan(target, getattr(target, "frames", None) if whole_page else None)
        overlay = await self._scan_drawn_text(frames, text, selector, timeout)
        # The snapshot cannot tell octowright's overlays from the page, so skip it
        # when an overlay itself shows the text rather than fail on our own badge.
        if whole_page and self.kind == "chromium" and not contains([overlay], text):
            leaks = await self._snapshot_leaks(text, timeout)
            if leaks:
                raise RuntimeError(f"forbidden text ({len(text)} chars) is rendered: {', '.join(leaks)}")
        # The marker, never the text; the keyed digest is what lets save_macro
        # bind the marker to the parameter it stood for (see
        # macros.privacy.assertion_text_digest). Local import: macros imports
        # the session stack.
        from octowright.macros.privacy import assertion_text_digest

        self.recorder.record(
            "expect_no_text", selector=selector, text=REDACTED_ASSERTION_TEXT, text_digest=assertion_text_digest(text)
        )


def _check_forbidden_text(text: str) -> None:
    if not text:
        raise ValueError("expect_no_text: text is empty, and an empty string is in every page")
    if text == REDACTED_ASSERTION_TEXT:
        raise ValueError(
            "expect_no_text: this step was recorded with its text redacted; set text to the value "
            "or a {{parameter}} before replaying it"
        )


def _frames_to_scan(target: Any, frames: Any) -> list[Any]:
    """Every frame when the check is about the whole page (*frames* given); otherwise only the target."""
    return list(frames) if isinstance(frames, list) and frames else [target]
