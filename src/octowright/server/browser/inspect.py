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
from typing import Any

from octowright import captures as _captures
from octowright._paths import reject_unsafe_path
from octowright.defaults import RECORDINGS_DIR, SNAPSHOT_TIMEOUT_SECONDS
from octowright.export import export_script as _export_script
from octowright.mcp_types import (
    BrowserBriefResult,
    BrowserEvaluateResult,
    BrowserOkResult,
    BrowserPathResult,
    BrowserReadMarkdownResult,
    BrowserScreenshotResult,
    BrowserSnapshotResult,
    BrowserToolAction,
)
from octowright.server._state import mcp, pool
from octowright.server.browser.discovery import (
    _outline_next_actions,
    browser_fields,
    browser_find_field,
    browser_page_outline,
)
from octowright.server.browser.discovery_links import browser_find_link, browser_links
from octowright.server.browser.inspect_assertions import (
    browser_expect_js,
    browser_expect_selector,
    browser_expect_text,
    browser_expect_url,
)
from octowright.server.browser.inspect_capture import browser_capture_and_close
from octowright.server.browser.inspect_console import browser_console_messages, browser_console_summary
from octowright.server.browser.inspect_recording import browser_tail_recording
from octowright.server.browser.network import browser_network_summary
from octowright.server.browser.views import browser_downloads_summary
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session import DEFAULT_PREVIEW_CHARS

# Module-level alias so tests can monkeypatch the snapshot timeout cheaply.
SNAPSHOT_TIMEOUT_S = SNAPSHOT_TIMEOUT_SECONDS

__all__ = [
    "_outline_next_actions",
    "browser_capture_and_close",
    "browser_console_messages",
    "browser_console_summary",
    "browser_expect_js",
    "browser_expect_selector",
    "browser_expect_text",
    "browser_expect_url",
    "browser_fields",
    "browser_find_field",
    "browser_find_link",
    "browser_links",
    "browser_page_outline",
    "browser_tail_recording",
]


def _snapshot_compact_actions(instance_id: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id}},
            {
                "tool": "browser_read_markdown",
                "args": {"instance_id": instance_id, "response_mode": "summary"},
            },
            {"tool": "browser_snapshot", "args": {"instance_id": instance_id, "selector": "main"}},
        ]
    )


def _snapshot_timeout_result(instance_id: str) -> BrowserSnapshotResult:
    return {
        "snapshot_timed_out": True,
        "timeout_s": SNAPSHOT_TIMEOUT_S,
        "hint": (
            "aria snapshot timed out on a heavy DOM — use browser_page_outline, "
            "browser_read_markdown(response_mode='summary'), or browser_snapshot with "
            "a scoped selector (e.g. selector='main')"
        ),
        "actions": _snapshot_compact_actions(instance_id),
    }


def _evaluate_truncated_actions(instance_id: str, expression: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {
                "tool": "capture_create",
                "args": {
                    "instance_id": instance_id,
                    "source": "evaluate",
                    "expression": expression,
                    "response_mode": "summary",
                },
            },
            {
                "tool": "browser_evaluate",
                "args": {"instance_id": instance_id, "expression": expression, "full": True},
            },
        ]
    )


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
        return _snapshot_timeout_result(instance_id)
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
        out["actions"] = _snapshot_compact_actions(instance_id)
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
            "next_actions": _evaluate_truncated_actions(instance_id, expression),
        }
    return {"result": result, "truncated": False, "result_size": len(rendered)}


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
        "Passing more than one is a 400-equivalent error. "
        "Pass response_mode='outline' to include a compact browser_page_outline after the wait succeeds."
    ),
)
async def browser_wait_for(
    instance_id: str,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
    expression: str | None = None,
    response_mode: str | None = None,
) -> BrowserOkResult:
    await pool.get(instance_id).wait_for(selector, text, timeout_ms, expression=expression)
    result: BrowserOkResult = {"ok": True}
    if response_mode == "outline":
        result["outline"] = await browser_page_outline(instance_id)
    return result


@mcp.tool(structured_output=False, description="Path to the JSONL action log for an instance.")
def browser_recording_path(instance_id: str) -> BrowserPathResult:
    return {"path": str(pool.get(instance_id).log_path)}


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


def _markdown_summary_next_actions(capture_id: str, summary_limit: int) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "capture_summary", "args": {"capture_id": capture_id, "limit": summary_limit}},
            {"tool": "capture_search", "args": {"capture_id": capture_id, "query": "<query>", "limit": 20}},
            {"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": 1, "limit": 80}},
            {
                "tool": "capture_get",
                "args": {"capture_id": capture_id, "offset": 0, "limit": _captures.DEFAULT_SLICE_CHARS},
            },
        ]
    )


def _annotate_capture_result_actions(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("next_actions"), list):
        result["next_actions"] = annotate_next_actions_for_profile(result["next_actions"])
    if isinstance(result.get("next_action"), dict):
        annotated = annotate_next_actions_for_profile([result["next_action"]])
        if annotated:
            result["next_action"] = annotated[0]
    for value in result.values():
        if isinstance(value, dict):
            _annotate_capture_result_actions(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _annotate_capture_result_actions(item)
    return result


def _markdown_truncated_next_actions(instance_id: str, original_size: int) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_read_markdown", "args": {"instance_id": instance_id, "response_mode": "summary"}},
            {"tool": "browser_read_markdown", "args": {"instance_id": instance_id, "max_chars": original_size}},
            {
                "tool": "capture_create",
                "args": {"instance_id": instance_id, "source": "markdown", "response_mode": "summary"},
            },
        ]
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Read the cached Markdown representation of the page. "
        "Highly token-efficient way to read article content or documentation. "
        "Pass response_mode='summary' to save the full markdown as a capture and "
        "return a compact outline plus capture_id instead of inline markdown."
    ),
)
async def browser_read_markdown(
    instance_id: str,
    max_chars: int | None = None,
    response_mode: str | None = None,
    summary_limit: int = 40,
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

    text = path.read_text(encoding="utf-8", errors="replace")
    original_size = len(text)

    if response_mode == "summary":
        target = session._target()
        saved = _captures.save_capture(
            kind="markdown",
            content=text,
            url=target.url,
            title=await session.page.title(),
            instance_id=instance_id,
            source={"source": "markdown", "path": str(path)},
        )
        _annotate_capture_result_actions(saved)
        capture_id = str(saved["capture_id"])
        summary = _annotate_capture_result_actions(_captures.summarize_capture(capture_id, limit=summary_limit))
        return {
            "url": target.url,
            "title": saved.get("title") if isinstance(saved.get("title"), str) else None,
            "capture_id": capture_id,
            "kind": "markdown",
            "markdown_size": original_size,
            "size_chars": original_size,
            "summary": summary,
            "actions": ["capture_summary", "capture_search", "capture_lines", "capture_get"],
            "next_actions": _markdown_summary_next_actions(capture_id, summary_limit),
        }

    cap = DEFAULT_PREVIEW_CHARS if max_chars is None else max_chars

    truncated = False
    if original_size > cap:
        text = text[:cap]
        truncated = True

    result: BrowserReadMarkdownResult = {
        # _target().url so the reported url matches the frame the markdown came from.
        "url": session._target().url,
        "markdown": text,
        "truncated": truncated,
        "markdown_size": original_size,
    }
    if truncated:
        result["next_actions"] = _markdown_truncated_next_actions(instance_id, original_size)
    return result


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


def _observe_next_actions(instance_id: str, limit: int) -> list[dict[str, Any]]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id, "limit": limit}},
            {"tool": "browser_find_link", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {"tool": "browser_find_field", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {
                "tool": "browser_read_markdown",
                "args": {"instance_id": instance_id, "response_mode": "summary"},
            },
            {
                "tool": "capture_create",
                "args": {"instance_id": instance_id, "source": "snapshot", "response_mode": "summary"},
            },
        ]
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return one compact observation bundle for the active page: page outline plus optional "
        "console, network, and download summaries. Use this when you need orientation and diagnostics "
        "in one low-token call before deciding whether a heavier snapshot or raw request list is needed."
    ),
)
async def browser_observe(
    instance_id: str,
    limit: int = 20,
    include_console: bool = True,
    include_network: bool = True,
    include_downloads: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "instance_id": instance_id,
        "outline": await browser_page_outline(instance_id, limit=limit),
        "actions": ["browser_page_outline", "browser_find_link", "browser_find_field", "browser_read_markdown"],
        "next_actions": _observe_next_actions(instance_id, limit),
    }
    if include_console:
        result["console"] = browser_console_summary(instance_id)
    if include_network:
        result["network"] = browser_network_summary(instance_id)
    if include_downloads:
        result["downloads"] = browser_downloads_summary(instance_id)
    return result
