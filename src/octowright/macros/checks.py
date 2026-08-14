# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

# Every helper below acquires the same fixed "macro_check" lease before
# touching the session's active target -- a caller already holding a root
# lease (e.g. a macro's "macro_run") re-enters it in the same task without
# queueing.


async def _check_url(session: SessionLike, pattern: str, mode: str = "regex") -> str:
    async with session.operation("macro_check"):
        actual: str = session.page.url
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
    session: SessionLike,
    selector: str,
    text: str,
    mode: str = "contains",
    timeout_ms: int | None = None,
) -> str:
    async with session.operation("macro_check"):
        element = await session._target().wait_for_selector(selector, timeout=timeout_ms or 10000)
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


async def _check_selector(
    session: SessionLike, selector: str, present: bool = True, timeout_ms: int | None = None
) -> None:
    async with session.operation("macro_check"):
        if present:
            await session._target().wait_for_selector(selector, timeout=timeout_ms or 10000)
        else:
            element = await session._target().query_selector(selector)
            if element is not None:
                raise RuntimeError(f'selector should be absent but was found: "{selector}"')


async def _check_js(session: SessionLike, expression: str, equals: Any = None) -> Any:
    async with session.operation("macro_check"):
        result = await session._target().evaluate(expression)
        if equals is not None:
            if result != equals:
                raise RuntimeError(
                    f"JS assertion failed: expression={expression!r}, expected={equals!r}, got={result!r}"
                )
        elif not result:
            raise RuntimeError(f"JS assertion failed (not truthy): expression={expression!r}, got={result!r}")
        return result
