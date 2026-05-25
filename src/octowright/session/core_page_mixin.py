# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from provide.telemetry import get_logger

from octowright._tracing import histogram, span
from octowright.defaults import (
    DEFAULT_ACTION_TIMEOUT_MS,
    DEFAULT_NAV_TIMEOUT_MS,
    REDACTED_INPUT_PLACEHOLDER,
)
from octowright.session._protocols import SessionLike

log = get_logger(__name__)

# Recognised values for the OCTOWRIGHT_REDACT_INPUTS env var. Re-read on
# every type/fill call so monkeypatched env changes (and operator
# adjustments without a restart) take effect immediately.
_REDACTION_MODES: frozenset[str] = frozenset({"off", "passwords", "all"})


def _current_redaction_mode() -> str:
    """Return the effective input-redaction mode for the current call.

    Reads ``OCTOWRIGHT_REDACT_INPUTS`` at call time. Unknown values fall
    back to ``"passwords"`` (safer default than silently disabling
    redaction) and emit a one-time debug log so a typo is at least
    observable.
    """
    raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower() or "passwords"
    if raw not in _REDACTION_MODES:
        log.debug(
            "core_page_mixin.unknown_redaction_mode",
            value=raw,
            fallback="passwords",
            supported=sorted(_REDACTION_MODES),
        )
        return "passwords"
    return raw


_NAVIGATE_DURATION = histogram(
    "octowright_session_navigate_duration_seconds",
    description="Duration of session.navigate() including page.goto",
    unit="s",
)

# Schemes the MCP/HTTP navigate paths refuse to send to Playwright.
# - file://     reads local files; combined with snapshot/read_markdown
#               tools this is a clean local-file exfiltration vector.
# - javascript: executes arbitrary script in the current page context,
#               bypassing the explicit browser_evaluate audit trail.
# - chrome:/chrome-extension:/view-source: similarly privileged browser-
#               internal schemes.
# data:, http, https, ws/wss, about: are not blocked. data: in particular
# is used by tests for in-memory launches and is reasonable for the
# operator-controlled launch URL; it cannot read local files.
_NAV_DENIED_SCHEMES = frozenset({"file", "javascript", "chrome", "chrome-extension", "view-source"})


def _sanitize_url_for_span(url: str) -> str:
    """Strip the query string from ``url`` before stamping it as a span attribute.

    Query strings on navigation targets routinely carry session tokens, signed
    URLs, account IDs, and other PII that we do not want to land in traces /
    exporter backends. The full URL still goes to ``self.url`` and the
    recorder's ``navigate`` event — only the span attribute is sanitized.

    Falls back to the original value if parsing fails for any reason; the
    sanitization is best-effort and must never block a navigation.
    """
    try:
        return urlsplit(url)._replace(query="").geturl()
    except Exception:
        return url


def _reject_unsafe_url(url: str) -> None:
    """Raise ValueError if ``url`` is on the deny-list of unsafe schemes."""
    if not isinstance(url, str) or not url:
        raise ValueError("navigate url must be a non-empty string")
    stripped = url.strip()
    scheme, sep, _rest = stripped.partition(":")
    if not sep:
        raise ValueError(f"navigate url missing scheme: {url!r}")
    if scheme.lower() in _NAV_DENIED_SCHEMES:
        raise ValueError(f"navigate url scheme {scheme!r} is not allowed (blocked: {sorted(_NAV_DENIED_SCHEMES)})")


_WAIT_FOR_POLL_SECONDS = 0.05


async def _body_contains_text(body: Any, text: str) -> bool:
    return text in await body.inner_text(timeout=1000)


async def _evaluate_truthy(target: Any, expression: str) -> bool:
    return bool(await target.evaluate(expression))


class SessionPageMixin(SessionLike):
    _last_mcp_navigation: str | None

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

        Removal-before-await is intentional: Playwright's ``page.close``
        fires its ``_on_page_close`` listener synchronously, and that
        listener evaluates ``[p for p in session.pages if not p.is_closed()]``
        to decide whether to cascade a full session eviction. If the closing
        page is still in ``self.pages`` when the listener runs AND a sibling
        page closes in the same tick (popup auto-close, concurrent close
        race), the comprehension comes up empty and fires a spurious
        eviction — then the post-await ``self.pages.index(self.page)`` here
        raises ValueError on the cleared session. Pop first so the
        listener's view always reflects the truth: this page is being
        closed deliberately by us.
        """
        if len(self.pages) <= 1:
            raise RuntimeError("cannot close the last remaining page; use browser_close to shut the whole instance")
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"page index {index} out of range (0..{len(self.pages) - 1})")
        target = self.pages[index]
        was_active = target is self.page
        # Reassign self.page BEFORE the pop so neither pop nor the
        # synchronous _on_page_close ever sees self.page dangling at a
        # popped index. The len(self.pages) > 1 guard above means a
        # non-target sibling is always present when ``was_active``; we
        # still snapshot here to defend against a concurrent popup-close
        # listener mutating self.pages between the guard and this lookup.
        if was_active:
            sibling = next((p for p in self.pages if p is not target), None)
            if sibling is None:
                raise RuntimeError("no sibling page available; another close raced ahead")
            self.page = sibling
        self.pages.pop(index)
        await target.close()
        self.recorder.record("close_page", index=index, was_active=was_active)
        return {
            "closed_index": index,
            "was_active": was_active,
            "active_index": self.pages.index(self.page),
            "page_count": len(self.pages),
        }

    async def navigate(self, url: str) -> dict[str, Any]:
        _reject_unsafe_url(url)
        instance_id = getattr(self, "instance_id", None)
        kind = getattr(self, "kind", None)
        t0 = time.perf_counter()
        with span(
            "octowright.session.navigate",
            instance_id=instance_id,
            kind=kind,
            url=_sanitize_url_for_span(url),
        ):
            # Tag the upcoming framenavigated event so pool's user_navigation
            # listener skips it (we already record "navigate" below).
            prior_mcp_navigation = getattr(self, "_last_mcp_navigation", None)
            self._last_mcp_navigation = url
            try:
                await self.page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
            except BaseException:
                # Reset the dedupe tag on failure: if the user then navigates to
                # the same URL manually, that's a genuine user_navigation event
                # and should not be suppressed.
                self._last_mcp_navigation = prior_mcp_navigation
                raise
            title = await self.page.title()
            self.url = url
            self._schedule_markdown_capture()
            self.recorder.record("navigate", url=url)
            _NAVIGATE_DURATION.record(time.perf_counter() - t0, attributes={"kind": kind or "unknown"})
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

    async def _is_password_input(self, selector: str) -> bool:
        """Best-effort check: does *selector* resolve to a credential input?

        Treats both ``type=password`` AND ``autocomplete in {current-password,
        new-password, one-time-code}`` as credential-bearing so SPAs that
        implement custom password fields as ``<input type=text>`` with the
        appropriate autocomplete hint still get scrubbed.

        Uses ``locator.first.evaluate(...)`` so multi-match selectors don't
        raise. Any Playwright/JS error fails closed to ``True`` so a selector
        that disappears around a typing/fill action cannot write cleartext
        credentials into the JSONL recording.
        """
        try:
            loc = self._target().locator(selector).first
            # Read both el.autocomplete (the IDL property — only present on
            # form-control elements) and el.getAttribute('autocomplete') (the
            # raw attribute — present on any element that declares it). Custom
            # elements / <div contenteditable> declare autocomplete via the
            # attribute, not the property, so the property-only read would
            # silently leak.
            info = await loc.evaluate(
                "el => el ? {"
                "  type: el.type ? String(el.type).toLowerCase() : '',"
                "  ac: el.autocomplete ? String(el.autocomplete).toLowerCase() : ''"
                "    || (el.getAttribute && el.getAttribute('autocomplete')"
                "         ? String(el.getAttribute('autocomplete')).toLowerCase() : '')"
                "} : {type: '', ac: ''}"
            )
        except Exception as exc:
            log.debug("core_page_mixin.password_lookup_failed", selector=selector, error=str(exc))
            return True
        # New shape: {type, ac}. Legacy callers / tests may stub `evaluate`
        # to return a bare string (the old behaviour); accept that shape too
        # so the redaction policy stays the same for callers that haven't
        # upgraded their mocks.
        if isinstance(info, str):
            return info == "password"
        if not isinstance(info, dict):
            return True
        if info.get("type") == "password":
            return True
        return info.get("ac") in ("current-password", "new-password", "one-time-code")

    async def _redacted_or_original(self, selector: str, value: str) -> str:
        """Return ``REDACTED_INPUT_PLACEHOLDER`` if the current redaction
        policy says to scrub this value, else *value* unchanged. The page
        action itself always receives the original value — only the JSONL
        record sees the result of this call."""
        mode = _current_redaction_mode()
        if mode == "off":
            return value
        if mode == "all":
            return REDACTED_INPUT_PLACEHOLDER
        # mode == "passwords"
        if await self._is_password_input(selector):
            return REDACTED_INPUT_PLACEHOLDER
        return value

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        recorded_text = await self._redacted_or_original(selector, text)
        await self._target().type(selector, text, delay=delay_ms or 0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("type", selector=selector, text=recorded_text, delay_ms=delay_ms, **meta)

    async def fill(self, selector: str, value: str) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        recorded_value = await self._redacted_or_original(selector, value)
        await self._target().fill(selector, value, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill", selector=selector, value=recorded_value, **meta)

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

    async def wait_for(
        self,
        selector: str | None,
        text: str | None,
        timeout_ms: int | None,
        expression: str | None = None,
    ) -> None:
        """Block until one of the supplied conditions becomes true.

        Exactly one of ``selector`` / ``text`` / ``expression`` may be set;
        with all three None the call waits for ``networkidle``. ``expression``
        is evaluated repeatedly with ``evaluate`` until it returns truthy — useful for
        compound conditions like "spinner removed AND table has rows" that
        a single selector can't express. The text and expression branches avoid
        ``wait_for_function`` because CSP-protected sites can reject its injected
        eval path even when normal page reads and evaluates work.
        """
        # Truthiness, not `is not None`: an empty string from a hand-edited
        # macro shouldn't count as "selector provided" — and the if/elif
        # chain below already routes via truthiness, so the validation needs
        # to match to keep the error message consistent with reality.
        provided = sum(1 for x in (selector, text, expression) if x)
        if provided > 1:
            raise ValueError(
                "wait_for accepts at most one of selector / text / expression; "
                f"got selector={bool(selector)}, text={bool(text)}, expression={bool(expression)}"
            )
        # Distinguish "no timeout supplied" (None → fall back to default) from
        # "wait forever" (0 → Playwright's documented sentinel). The old
        # `timeout_ms or DEFAULT` collapsed both to default.
        timeout = DEFAULT_ACTION_TIMEOUT_MS if timeout_ms is None else timeout_ms
        target = self._target()
        if selector:
            await target.wait_for_selector(selector, timeout=timeout)
            self.recorder.record("wait_for", selector=selector, timeout_ms=timeout)
        elif text:
            body = target.locator("body")
            await self._poll_until(timeout, lambda: _body_contains_text(body, text), f"text={text!r}")
            self.recorder.record("wait_for", text=text, timeout_ms=timeout)
        elif expression:
            await self._poll_until(timeout, lambda: _evaluate_truthy(target, expression), f"expression={expression!r}")
            self.recorder.record("wait_for", expression=expression, timeout_ms=timeout)
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
