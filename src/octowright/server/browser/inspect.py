# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Inspection tools: screenshot, snapshot, evaluate, console_messages, wait_for, recording_path,
export_script, expect_url / expect_text / expect_selector / expect_js."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from ... import macros as macro_mod
from ...export import export_script as _export_script
from ...recorder import tail_log
from ...session import DEFAULT_PREVIEW_CHARS
from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description="Screenshot an instance to disk. If path omitted, writes next to the recording.",
)
async def browser_screenshot(instance_id: str, path: str | None = None) -> dict[str, Any]:
    session = pool.get(instance_id)
    target = Path(path) if path else session.log_path.with_suffix(".png")
    out = await session.screenshot(target)
    return {"path": str(out)}


@mcp.tool(
    structured_output=False,
    description=(
        "Return an aria-tree snapshot for an instance. By default snapshots the "
        "full document and truncates the YAML at ~4000 chars; pass selector to "
        "scope a subtree (e.g. selector='main') and full=True to skip truncation."
    ),
)
async def browser_snapshot(
    instance_id: str,
    selector: str = "html",
    full: bool = False,
    max_chars: int | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    aria = await session.page.locator(selector).aria_snapshot()
    cap = None if full else (max_chars or DEFAULT_PREVIEW_CHARS)
    out: dict[str, Any] = {
        "url": session.page.url,
        "title": await session.page.title(),
    }
    if cap is not None and len(aria) > cap:
        out["aria"] = aria[:cap]
        out["truncated"] = True
        out["aria_size"] = len(aria)
        out["cap"] = cap
    else:
        out["aria"] = aria
        out["truncated"] = False
        out["aria_size"] = len(aria)
    session.recorder.record("snapshot", selector=selector, truncated=out["truncated"])
    return out


@mcp.tool(
    structured_output=False,
    description=(
        "Evaluate a JavaScript expression in an instance's page. "
        "By default the stringified result is truncated to ~4000 chars to bound "
        "MCP token cost; pass full=True to disable truncation, or max_chars=N "
        "for a custom cap. Truncated payloads include `truncated=True` and the "
        "full size in `result_size`."
    ),
)
async def browser_evaluate(
    instance_id: str,
    expression: str,
    max_chars: int | None = None,
    full: bool = False,
) -> dict[str, Any]:
    result = await pool.get(instance_id).evaluate(expression)
    cap = None if full else (max_chars or DEFAULT_PREVIEW_CHARS)
    rendered = result if isinstance(result, str | bytes) else _json.dumps(result, default=str)
    if isinstance(rendered, bytes):
        rendered = rendered.decode("utf-8", errors="replace")
    if cap is not None and len(rendered) > cap:
        return {
            "result": rendered[:cap],
            "truncated": True,
            "result_size": len(rendered),
            "cap": cap,
        }
    return {"result": result, "truncated": False, "result_size": len(rendered)}


@mcp.tool(
    structured_output=False,
    description=(
        "Return console messages from an instance. Optionally filter by level "
        "(e.g. 'error', 'warning') and pass `since` (a cursor returned from a "
        "previous call) for incremental reads."
    ),
)
def browser_console_messages(
    instance_id: str,
    level: str | None = None,
    since: int | None = None,
) -> dict[str, Any]:
    msgs = list(pool.get(instance_id).console)
    start = since or 0
    sliced = msgs[start:]
    filtered = [m for m in sliced if m.get("level") == level] if level else sliced
    return {
        "messages": filtered,
        "next_cursor": len(msgs),
        "total": len(msgs),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Block until a selector appears, a text becomes visible, or the network goes idle. "
        "Use this when you need to PAUSE before the next action (e.g. wait for a "
        "spinner to disappear, wait for a list to load). For making an ASSERTION "
        "about page state, use browser_expect_selector / expect_text / expect_url instead — "
        "those record a check, not a wait. Provide exactly one of selector or text, "
        "or neither for network-idle."
    ),
)
async def browser_wait_for(
    instance_id: str,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    await pool.get(instance_id).wait_for(selector, text, timeout_ms)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Path to the JSONL action log for an instance.")
def browser_recording_path(instance_id: str) -> dict[str, Any]:
    return {"path": str(pool.get(instance_id).log_path)}


@mcp.tool(
    structured_output=False,
    description=(
        "ONE-SHOT TEARDOWN: Captures a screenshot and page title, then closes the browser. "
        "Use this as the final step of a task to ensure resources are freed. "
        "If snapshot=True, also includes an aria-tree snapshot. "
        "Returns {title, url, screenshot_path, aria (optional), closed: true}."
    ),
)
async def browser_capture_and_close(
    instance_id: str,
    screenshot_path: str | None = None,
    snapshot: bool = True,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    title = await session.page.title()
    url = session.page.url

    # Screenshot
    target = Path(screenshot_path) if screenshot_path else session.log_path.with_suffix(".png")
    await session.screenshot(target)

    # Optional Snapshot
    aria = None
    if snapshot:
        aria_full = await session.page.locator("html").aria_snapshot()
        aria = aria_full[:DEFAULT_PREVIEW_CHARS]

    # Close
    await pool.close(instance_id)

    res = {
        "title": title,
        "url": url,
        "screenshot_path": str(target),
        "closed": True,
    }
    if aria:
        res["aria"] = aria
    return res


@mcp.tool(
    structured_output=False,
    description="Export a replayable Playwright script (python | ts) from an instance's recording.",
)
def browser_export_script(
    instance_id: str,
    format: str = "python",
    out_path: str | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    suffix = ".py" if format == "python" else ".ts"
    target = Path(out_path) if out_path else session.log_path.with_suffix(suffix)
    result = _export_script(session.log_path, target, fmt=format)
    return {"path": str(result)}


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
) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await macro_mod._check_url(session.page, pattern, mode)
    session.recorder.record("expect_url", pattern=pattern, mode=mode)
    return {"ok": True, "url": actual}


@mcp.tool(
    structured_output=False,
    description=(
        "ASSERT an element matching `selector` contains `text`. Polls up to "
        "`timeout_ms` waiting for the element to appear AND its text to match. "
        "mode: 'contains' (default), 'equals', or 'regex'. "
        "Use this to verify rendered output (welcome banner, error message, table cell). "
        "If you only need 'does this element exist?' use browser_expect_selector."
    ),
)
async def browser_expect_text(
    instance_id: str,
    selector: str,
    text: str,
    mode: str = "contains",
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await macro_mod._check_text(session.page, selector, text, mode, timeout_ms)
    session.recorder.record("expect_text", selector=selector, text=text, mode=mode, timeout_ms=timeout_ms)
    return {"ok": True, "text": actual}


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
) -> dict[str, Any]:
    session = pool.get(instance_id)
    await macro_mod._check_selector(session.page, selector, present, timeout_ms)
    session.recorder.record("expect_selector", selector=selector, present=present, timeout_ms=timeout_ms)
    return {"ok": True, "selector": selector, "present": present}


@mcp.tool(
    structured_output=False,
    description=(
        "Assert a JavaScript expression evaluates to a truthy value (or equals `equals` "
        "if supplied). The expression runs in the page, like browser_evaluate."
    ),
)
async def browser_expect_js(
    instance_id: str,
    expression: str,
    equals: Any = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    result = await macro_mod._check_js(session.page, expression, equals)
    session.recorder.record("expect_js", expression=expression, equals=equals)
    return {"ok": True, "result": result}


@mcp.tool(
    structured_output=False,
    description=(
        "Read JSONL events appended to an instance's recording since byte offset `since`. "
        "Use this to STREAM events as they happen; use browser_recording_path if you just "
        "need the file path on disk. Pass the returned `cursor` back as `since` on the next "
        "call to read only new events (cursor pattern). When the file ends mid-line, the "
        "cursor stops at the start of the partial fragment so it will be re-read once "
        "completed; `complete` is True iff cursor == total_bytes."
    ),
)
def browser_tail_recording(
    instance_id: str,
    since: int | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    log_path = Path(session.log_path)
    prev = since or 0

    events, new_cursor, total_bytes = tail_log(log_path, prev)

    return {
        "events": events,
        "cursor": new_cursor,
        "total_bytes": total_bytes,
        "complete": new_cursor >= total_bytes,
    }
