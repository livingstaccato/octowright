# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from octowright.session._protocols import SessionLike


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
