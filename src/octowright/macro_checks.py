# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from typing import Any


async def _check_url(page: Any, pattern: str, mode: str = "regex") -> str:
    actual: str = page.url
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
    return actual


async def _check_text(
    page: Any,
    selector: str,
    text: str,
    mode: str = "contains",
    timeout_ms: int | None = None,
) -> str:
    element = await page.wait_for_selector(selector, timeout=timeout_ms or 10000)
    if element is None:
        raise RuntimeError(f'element never appeared: selector="{selector}"')
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
    return actual


async def _check_selector(page: Any, selector: str, present: bool = True, timeout_ms: int | None = None) -> None:
    if present:
        await page.wait_for_selector(selector, timeout=timeout_ms or 10000)
    else:
        element = await page.query_selector(selector)
        if element is not None:
            raise RuntimeError(f'selector should be absent but was found: "{selector}"')


async def _check_js(page: Any, expression: str, equals: Any = None) -> Any:
    result = await page.evaluate(expression)
    if equals is not None:
        if result != equals:
            raise RuntimeError(f"JS assertion failed: expression={expression!r}, expected={equals!r}, got={result!r}")
    elif not result:
        raise RuntimeError(f"JS assertion failed (not truthy): expression={expression!r}, got={result!r}")
    return result
