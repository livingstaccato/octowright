# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Inspection tools: screenshot, snapshot, evaluate, console_messages, wait_for, recording_path,
export_script, expect_url / expect_text / expect_selector / expect_js."""

from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path
from typing import Any, cast

from octowright._paths import reject_unsafe_path
from octowright.defaults import RECORDINGS_DIR, SNAPSHOT_TIMEOUT_SECONDS
from octowright.export import export_script as _export_script
from octowright.mcp_types import (
    BrowserBriefResult,
    BrowserCaptureAndCloseResult,
    BrowserConsoleMessagesResult,
    BrowserEvaluateResult,
    BrowserExpectJsResult,
    BrowserExpectSelectorResult,
    BrowserExpectTextResult,
    BrowserExpectUrlResult,
    BrowserOkResult,
    BrowserPathResult,
    BrowserReadMarkdownResult,
    BrowserScreenshotResult,
    BrowserSnapshotResult,
    BrowserTailRecordingResult,
    ConsoleMessage,
)
from octowright.recorder import tail_log
from octowright.server._state import mcp, pool
from octowright.session import DEFAULT_PREVIEW_CHARS

# Module-level alias so tests can monkeypatch the snapshot timeout cheaply.
SNAPSHOT_TIMEOUT_S = SNAPSHOT_TIMEOUT_SECONDS


@mcp.tool(
    structured_output=False,
    description="Screenshot an instance to disk. If path omitted, writes next to the recording.",
)
async def browser_screenshot(instance_id: str, path: str | None = None) -> BrowserScreenshotResult:
    session = pool.get(instance_id)
    target = Path(path) if path else session.log_path.with_suffix(".png")
    # MCP-supplied path could escape RECORDINGS_DIR; confine before writing.
    target = reject_unsafe_path(target, RECORDINGS_DIR, label=f"screenshot path {str(target)!r}")
    out = await session.screenshot(target)
    return {"path": str(out)}


@mcp.tool(
    structured_output=False,
    description=(
        "Return an aria-tree snapshot for an instance. By default snapshots the "
        "page body and truncates the YAML at ~4000 chars; pass selector to "
        "scope a subtree (e.g. selector='main') and full=True to skip truncation."
    ),
)
async def browser_snapshot(
    instance_id: str,
    selector: str = "body",
    full: bool = False,
    max_chars: int | None = None,
) -> BrowserSnapshotResult:
    session = pool.get(instance_id)
    # Route through session.snapshot so the JSONL gets a "snapshot" event;
    # bypassing it would make MCP-tool snapshots invisible to macro replay,
    # golden diffs, and the audit trail.
    try:
        snap = await asyncio.wait_for(session.snapshot(selector=selector), timeout=SNAPSHOT_TIMEOUT_S)
    except TimeoutError:
        # A heavy DOM can make aria_snapshot() run past the bridge request timeout,
        # which the agent can't distinguish from a disconnect. Degrade to a typed
        # result that points at the cheaper observe paths instead of hanging.
        return {
            "snapshot_timed_out": True,
            "timeout_s": SNAPSHOT_TIMEOUT_S,
            "hint": (
                "aria snapshot timed out on a heavy DOM — use browser_read_markdown, "
                "browser_brief, or browser_snapshot with a scoped selector (e.g. selector='main')"
            ),
        }
    aria = snap["aria"]
    cap = None if full else (max_chars or DEFAULT_PREVIEW_CHARS)
    out: BrowserSnapshotResult = {
        "url": snap["url"],
        "title": snap["title"],
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
) -> BrowserEvaluateResult:
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
) -> BrowserConsoleMessagesResult:
    msgs = list(pool.get(instance_id).console)
    start = since or 0
    sliced = msgs[start:]
    filtered = [m for m in sliced if m.get("level") == level] if level else sliced
    return {
        "messages": cast("list[ConsoleMessage]", filtered),
        "next_cursor": len(msgs),
        "total": len(msgs),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Block until a condition is met. Use this when you need to PAUSE before the "
        "next action — e.g. wait for a spinner to disappear, a list to load, or a "
        "compound state like 'spinner gone AND table has > 0 rows'. For making an "
        "ASSERTION about page state, use browser_expect_selector / expect_text / "
        "expect_url instead — those record a check, not a wait.\n\n"
        "Provide exactly one of:\n"
        "  - `selector`: wait until the selector matches at least one element.\n"
        "  - `text`:     wait until document.body.innerText contains this string.\n"
        "  - `expression`: a JS expression that's polled inside the page until it "
        "returns truthy. Use for compound conditions a selector can't express, "
        "e.g. `\"!document.querySelector('.spinner') && document.querySelectorAll('tbody tr').length > 0\"`.\n"
        "  - none of the above → wait for network-idle.\n"
        "Passing more than one is a 400-equivalent error."
    ),
)
async def browser_wait_for(
    instance_id: str,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
    expression: str | None = None,
) -> BrowserOkResult:
    await pool.get(instance_id).wait_for(selector, text, timeout_ms, expression=expression)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Path to the JSONL action log for an instance.")
def browser_recording_path(instance_id: str) -> BrowserPathResult:
    return {"path": str(pool.get(instance_id).log_path)}


@mcp.tool(
    structured_output=False,
    description=(
        "ONE-SHOT TEARDOWN: Captures a screenshot and page title, then closes the browser. "
        "Use this as the final step of a task to ensure resources are freed. "
        "If snapshot=True, also includes an aria-tree snapshot. "
        "If the browser is protected, pass force=True to confirm before any capture side effects run. "
        "Returns {title, url, screenshot_path, aria (optional), closed: true}; protected refusal returns {error}."
    ),
)
async def browser_capture_and_close(
    instance_id: str,
    screenshot_path: str | None = None,
    snapshot: bool = True,
    force: bool = False,
) -> BrowserCaptureAndCloseResult:
    session = pool.get(instance_id)
    if getattr(session, "protected", False) and not force:
        return {
            "error": (
                f"browser {instance_id!r} is protected; pass force=True to capture and close it. "
                "Protected browsers are meant to stay open for the user."
            )
        }
    title = await session.page.title()
    # url + aria follow the active frame (like browser_snapshot); the screenshot
    # stays page-level since it captures the rendered viewport, and title is page-only.
    frame_target = session._target()
    url = frame_target.url

    # Screenshot — MCP-supplied path is confined to RECORDINGS_DIR.
    target = Path(screenshot_path) if screenshot_path else session.log_path.with_suffix(".png")
    target = reject_unsafe_path(target, RECORDINGS_DIR, label=f"screenshot_path {str(target)!r}")
    await session.screenshot(target)

    # Optional Snapshot
    aria = None
    if snapshot:
        aria_full = await frame_target.locator("html").aria_snapshot()
        aria = aria_full[:DEFAULT_PREVIEW_CHARS]

    # Close
    await pool.close(instance_id, force=force)

    res: BrowserCaptureAndCloseResult = {
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
) -> BrowserPathResult:
    session = pool.get(instance_id)
    suffix = ".py" if format == "python" else ".ts"
    target = Path(out_path) if out_path else session.log_path.with_suffix(suffix)
    # MCP-supplied path could escape RECORDINGS_DIR; confine before writing.
    target = reject_unsafe_path(target, RECORDINGS_DIR, label=f"export_script out_path {str(target)!r}")
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
) -> BrowserExpectUrlResult:
    session = pool.get(instance_id)
    actual = await session.expect_url(pattern, mode)
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
) -> BrowserExpectTextResult:
    session = pool.get(instance_id)
    actual = await session.expect_text(selector, text, mode, timeout_ms)
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
) -> BrowserExpectSelectorResult:
    session = pool.get(instance_id)
    await session.expect_selector(selector, present, timeout_ms)
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
) -> BrowserExpectJsResult:
    session = pool.get(instance_id)
    result = await session.expect_js(expression, equals)
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
) -> BrowserTailRecordingResult:
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


@mcp.tool(
    structured_output=False,
    description=(
        "Read the cached Markdown representation of the page. "
        "Highly token-efficient way to read article content or documentation."
    ),
)
async def browser_read_markdown(
    instance_id: str,
    max_chars: int | None = None,
) -> BrowserReadMarkdownResult:
    session = pool.get(instance_id)
    if max_chars is not None and max_chars < 0:
        raise ValueError("max_chars must be >= 0")

    # Always refresh on explicit reads so SPA/in-page changes do not serve a
    # stale markdown cache from a prior render.
    path = await session.capture_markdown(force=True)

    if not path or not path.exists():
        raise RuntimeError(
            "markdown generation failed or is unavailable; "
            "ensure markitdown is installed and the page rendered HTML content"
        )

    text = path.read_text(encoding="utf-8")
    original_size = len(text)
    cap = DEFAULT_PREVIEW_CHARS if max_chars is None else max_chars

    truncated = False
    if original_size > cap:
        text = text[:cap]
        truncated = True

    return {
        # _target().url so the reported url matches the frame the markdown came from.
        "url": session._target().url,
        "markdown": text,
        "truncated": truncated,
        "markdown_size": original_size,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Return a brief summary of the current page state, including URL, title, "
        "and highly truncated snapshot of actionable elements."
    ),
)
async def browser_brief(instance_id: str) -> BrowserBriefResult:
    session = pool.get(instance_id)
    # Route through _target() so brief reflects a switched frame, matching snapshot
    # and every action tool. title stays page-level — Playwright Frames have none.
    target = session._target()
    title = await session.page.title()
    # Pull a tiny slice of the body snapshot to provide basic orientation
    aria = await target.locator("body").aria_snapshot()
    elements = aria[:500] + ("..." if len(aria) > 500 else "")

    return {
        "url": target.url,
        "title": title,
        "elements": elements,
    }
