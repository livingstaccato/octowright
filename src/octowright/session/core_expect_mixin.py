# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_INPUT_PLACEHOLDER
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
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
        self._network_clean_explicit_mark = self._network_clean_counts()
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
        The error carries counts only, because a failed URL or an exception
        message can carry a credential.
        """
        if since not in ("run", "mark"):
            raise ValueError(f'unknown since={since!r}; expected "run" or "mark"')
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
        return {**counts, "in_flight": in_flight}

    @gated_operation("browser_expect_no_text")
    async def expect_no_text(self, text: str, selector: str = "body", timeout_ms: int | None = None) -> None:
        """Assert *text* does not appear in *selector*'s rendered text (case-sensitive).

        Reads ``innerText``: what the page renders, not input values or
        attributes. *text* is treated as a secret -- it is usually the password
        the check exists to keep off screen -- so neither the error nor the
        recording repeats it.
        """
        if not text:
            raise ValueError("expect_no_text: text is empty, and an empty string is in every page")
        timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS
        rendered: str = await self._target().inner_text(selector, timeout=timeout)
        if text in rendered:
            raise RuntimeError(f'forbidden text ({len(text)} chars) is rendered in "{selector}"')
        self.recorder.record("expect_no_text", selector=selector, text=REDACTED_INPUT_PLACEHOLDER)
