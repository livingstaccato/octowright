# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Browser assertion tools."""

from __future__ import annotations

import json as _json
from typing import Any

from octowright.mcp_types import (
    BrowserExpectJsResult,
    BrowserExpectSelectorResult,
    BrowserExpectTextResult,
    BrowserExpectUrlResult,
    BrowserToolAction,
)
from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session import DEFAULT_PREVIEW_CHARS


def _expect_text_truncated_actions(instance_id: str, selector: str, text: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {
                "tool": "browser_expect_text",
                "args": {"instance_id": instance_id, "selector": selector, "text": text, "full": True},
            }
        ]
    )


def _expect_js_truncated_actions(instance_id: str, expression: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {
                "tool": "browser_expect_js",
                "args": {"instance_id": instance_id, "expression": expression, "full": True},
            }
        ]
    )


def _bounded_rendered_value(value: Any, *, field: str, max_chars: int | None, full: bool) -> dict[str, Any]:
    cap = None if full else (max_chars if max_chars is not None else DEFAULT_PREVIEW_CHARS)
    if cap is not None and cap < 0:
        raise ValueError("max_chars must be >= 0")
    rendered = value if isinstance(value, str | bytes) else _json.dumps(value, default=str)
    if isinstance(rendered, bytes):
        rendered = rendered.decode("utf-8", errors="replace")
    size_key = f"{field}_size"
    if cap is not None and len(rendered) > cap:
        return {field: rendered[:cap], "truncated": True, size_key: len(rendered), "cap": cap}
    return {field: value, "truncated": False, size_key: len(rendered)}


@mcp.tool(
    structured_output=False,
    description=(
        "ASSERT the current page URL matches `pattern`. Raises on mismatch — use this "
        "to verify navigation reached the right place (e.g. after a successful login). "
        "`pattern` is a regex by default; pass mode='equals' for exact match or "
        "mode='contains' for substring. Don't use this just to read the URL — that's `browser_evaluate('location.href')`."
    ),
)
async def browser_expect_url(
    instance_id: str,
    pattern: str,
    mode: str = "regex",
) -> BrowserExpectUrlResult:
    async with browser_operation(pool, instance_id, "browser_expect_url") as session:
        actual = await session.expect_url(pattern, mode)
        return {"ok": True, "url": actual}


@mcp.tool(
    structured_output=False,
    description=(
        "ASSERT an element matching `selector` contains `text`. Polls up to "
        "`timeout_ms` waiting for the element to appear AND its text to match. "
        "mode: 'contains' (default), 'equals', or 'regex'. "
        "Use this to verify rendered output (welcome banner, error message, table cell). "
        "If you only need 'does this element exist?' use browser_expect_selector. "
        "Returned actual text is capped by default to bound token cost; pass "
        "max_chars=N for a custom cap or full=True to disable truncation."
    ),
)
async def browser_expect_text(
    instance_id: str,
    selector: str,
    text: str,
    mode: str = "contains",
    timeout_ms: int | None = None,
    max_chars: int | None = None,
    full: bool = False,
) -> BrowserExpectTextResult:
    async with browser_operation(pool, instance_id, "browser_expect_text") as session:
        actual = await session.expect_text(selector, text, mode, timeout_ms)
        bounded = _bounded_rendered_value(actual, field="text", max_chars=max_chars, full=full)
        out: BrowserExpectTextResult = {"ok": True}
        out["text"] = str(bounded["text"])
        out["truncated"] = bool(bounded["truncated"])
        out["text_size"] = int(bounded["text_size"])
        if "cap" in bounded:
            out["cap"] = int(bounded["cap"])
            out["next_actions"] = _expect_text_truncated_actions(instance_id, selector, text)
        return out


@mcp.tool(
    structured_output=False,
    description=(
        "ASSERT at least one element matching `selector` exists (or DOES NOT exist, if "
        "present=False). Polls up to `timeout_ms` for the condition. "
        "Use this for existence checks (modal opened, error banner appeared/disappeared). "
        "If you also need to check the text inside, use browser_expect_text in one call."
    ),
)
async def browser_expect_selector(
    instance_id: str,
    selector: str,
    present: bool = True,
    timeout_ms: int | None = None,
) -> BrowserExpectSelectorResult:
    async with browser_operation(pool, instance_id, "browser_expect_selector") as session:
        await session.expect_selector(selector, present, timeout_ms)
        return {"ok": True, "selector": selector, "present": present}


@mcp.tool(
    structured_output=False,
    description=(
        "Assert a JavaScript expression evaluates to a truthy value (or equals `equals` "
        "if supplied). The expression runs in the page, like browser_evaluate. "
        "The stringified result is capped by default to bound token cost; pass "
        "max_chars=N for a custom cap or full=True to disable truncation."
    ),
)
async def browser_expect_js(
    instance_id: str,
    expression: str,
    equals: Any = None,
    max_chars: int | None = None,
    full: bool = False,
) -> BrowserExpectJsResult:
    async with browser_operation(pool, instance_id, "browser_expect_js") as session:
        result = await session.expect_js(expression, equals)
        bounded = _bounded_rendered_value(result, field="result", max_chars=max_chars, full=full)
        out: BrowserExpectJsResult = {"ok": True}
        out["result"] = bounded["result"]
        out["truncated"] = bool(bounded["truncated"])
        out["result_size"] = int(bounded["result_size"])
        if "cap" in bounded:
            out["cap"] = int(bounded["cap"])
            out["next_actions"] = _expect_js_truncated_actions(instance_id, expression)
        return out
