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


class SessionExpectMixin(SessionLike):
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

    @gated_operation("browser_expect_network_clean")
    async def expect_network_clean(self, http_errors: bool = False) -> dict[str, int]:
        """Assert no failed requests (aborts excepted) and no page errors since the mark.

        The mark is the start of the current macro run (see
        ``mark_network_clean_window``). "Failed" is Playwright's
        ``requestfailed``: the request got no response. ``http_errors=True``
        also fails on a 4xx/5xx page load or API call (``request_failures.
        HTTP_ERROR_RESOURCE_TYPES``); off by default because a 4xx is sometimes
        the answer a journey expects. The error carries counts only, because a
        failed URL or an exception message can carry a credential.
        """
        failed, page_errors, http_error_count = self.network_failures_since_mark()
        counts = {"failed_requests": failed, "page_errors": page_errors}
        if http_errors:
            counts["http_errors"] = http_error_count
        if any(counts.values()):
            detail = f"{failed} failed request(s), {page_errors} page error(s)"
            if http_errors:
                detail += f", {http_error_count} HTTP error(s)"
            raise RuntimeError(f"network not clean: {detail}")
        if http_errors:
            self.recorder.record("expect_network_clean", http_errors=True)
        else:
            self.recorder.record("expect_network_clean")
        return counts

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
