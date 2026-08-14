# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pages, frames, and downloads tools."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from octowright.mcp_types import BrowserPageOutlineResult
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile


async def browser_page_outline(instance_id: str) -> BrowserPageOutlineResult:
    from octowright.server.browser.inspect import browser_page_outline as _browser_page_outline

    return await _browser_page_outline(instance_id)


async def _with_outline(instance_id: str, result: dict[str, Any], response_mode: str | None) -> dict[str, Any]:
    if response_mode == "outline":
        result["outline"] = await browser_page_outline(instance_id)
    return result


def _sorted_counts(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count} for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _download_host(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "")
    try:
        parsed = urlparse(url)
    except Exception:
        return "unknown-host"
    return parsed.netloc or "unknown-host"


def _download_extension(item: dict[str, Any]) -> str:
    name = str(item.get("suggested_filename") or item.get("path") or "")
    suffix = Path(name).suffix.lower()
    return suffix or "(none)"


def _download_summary_row(instance_id: str, index: int, item: dict[str, Any]) -> dict[str, Any]:
    out = {
        "index": index,
        "suggested_filename": item.get("suggested_filename"),
        "url": item.get("url"),
        "path": item.get("path"),
        "action": {"tool": "browser_downloads_summary", "args": {"instance_id": instance_id, "after": index}},
    }
    return {key: value for key, value in out.items() if value is not None}


def _download_summary_next_actions(instance_id: str, next_cursor: int) -> list[dict[str, Any]]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_downloads_summary", "args": {"instance_id": instance_id, "after": next_cursor}},
            {"tool": "browser_wait_for_download", "args": {"instance_id": instance_id}},
        ]
    )


def _clean_limit(limit: int) -> int:
    return max(0, min(int(limit), 100))


def _short_text(value: Any, cap: int) -> str | None:
    if value is None:
        return None
    return str(value)[:cap]


def _page_summary_row(instance_id: str, page: dict[str, Any]) -> dict[str, Any]:
    index = int(page.get("index") or 0)
    switch_action = {
        "tool": "page_switch",
        "args": {"instance_id": instance_id, "index": index, "response_mode": "outline"},
    }
    row = {
        "index": index,
        "url": _short_text(page.get("url"), 200),
        "title": _short_text(page.get("title"), 120),
        "is_active": bool(page.get("is_active")),
        "action": switch_action,
        "actions": [
            switch_action,
            {"tool": "page_close", "args": {"instance_id": instance_id, "index": index}},
        ],
    }
    return {key: value for key, value in row.items() if value is not None}


def _frame_switch_action(instance_id: str, frame: dict[str, Any]) -> dict[str, Any]:
    if frame.get("is_main"):
        return {"tool": "browser_reset_frame", "args": {"instance_id": instance_id, "response_mode": "outline"}}
    if frame.get("selector"):
        return {
            "tool": "browser_switch_frame",
            "args": {"instance_id": instance_id, "selector": frame["selector"], "response_mode": "outline"},
        }
    if frame.get("name"):
        return {
            "tool": "browser_switch_frame",
            "args": {"instance_id": instance_id, "name": frame["name"], "response_mode": "outline"},
        }
    return {
        "tool": "browser_switch_frame",
        "args": {
            "instance_id": instance_id,
            "url_pattern": str(frame.get("url") or "")[:200],
            "response_mode": "outline",
        },
    }


def _frame_summary_row(instance_id: str, frame: dict[str, Any]) -> dict[str, Any]:
    row = {
        "index": frame.get("index"),
        "name": _short_text(frame.get("name"), 120),
        "url": _short_text(frame.get("url"), 200),
        "is_active": bool(frame.get("is_active")),
        "is_main": bool(frame.get("is_main") or int(frame.get("index") or 0) == 0),
        "selector": _short_text(frame.get("selector"), 200),
        "action": _frame_switch_action(instance_id, frame),
    }
    return {key: value for key, value in row.items() if value is not None}


@mcp.tool(
    structured_output=False,
    description=(
        "List all pages/tabs for an instance. The active page (the one every other "
        "per-instance tool targets) has is_active=True. Popups opened by the browser "
        "are tracked automatically and appear here. Pass response_mode='summary' for "
        "bounded rows with page_switch/page_close action payloads."
    ),
)
async def page_list(instance_id: str, response_mode: str | None = None, limit: int = 20) -> Any:
    pages = await pool.get(instance_id).list_pages()
    if response_mode != "summary":
        return pages
    capped = _clean_limit(limit)
    rows = pages[:capped]
    return {
        "total": len(pages),
        "count": len(rows),
        "truncated": len(rows) < len(pages),
        "pages": [_page_summary_row(instance_id, page) for page in rows],
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Switch the active page for an instance. Subsequent tool calls (click, fill, "
        "evaluate, etc.) target the newly-active page. Pass response_mode='outline' "
        "to include a compact browser_page_outline for the newly active page."
    ),
)
async def page_switch(instance_id: str, index: int, response_mode: str | None = None) -> dict[str, Any]:
    res = await pool.get(instance_id).switch_page(index)
    return await _with_outline(instance_id, dict(res), response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Close one page/tab for an instance. Refuses if it's the only remaining page "
        "(use browser_close to shut the whole instance instead)."
    ),
)
async def page_close(instance_id: str, index: int) -> dict[str, Any]:
    return await pool.get(instance_id).close_page(index)


@mcp.tool(
    structured_output=False,
    description=(
        "Switch the active target to an iframe. Subsequent click/fill/type/evaluate/wait_for "
        "calls target the frame instead of the top-level page. Exactly one of selector, name, "
        "or url_pattern. Use browser_reset_frame to switch back. Pass response_mode='outline' "
        "to include a compact browser_page_outline for the active frame."
    ),
)
async def browser_switch_frame(
    instance_id: str,
    selector: str | None = None,
    name: str | None = None,
    url_pattern: str | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    res = await pool.get(instance_id).switch_frame(
        selector=selector,
        name=name,
        url_pattern=url_pattern,
    )
    return await _with_outline(instance_id, dict(res), response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Reset the active target to the top-level page. Pass response_mode='outline' "
        "to include a compact browser_page_outline for the top-level page."
    ),
)
async def browser_reset_frame(instance_id: str, response_mode: str | None = None) -> dict[str, Any]:
    res = await pool.get(instance_id).reset_frame()
    return await _with_outline(instance_id, dict(res), response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "List all frames on the active page (including main). Pass response_mode='summary' "
        "for bounded rows with browser_switch_frame/browser_reset_frame action payloads."
    ),
)
async def browser_list_frames(instance_id: str, response_mode: str | None = None, limit: int = 20) -> Any:
    frames = await pool.get(instance_id).list_frames()
    if response_mode != "summary":
        return frames
    capped = _clean_limit(limit)
    rows = frames[:capped]
    return {
        "total": len(frames),
        "count": len(rows),
        "truncated": len(rows) < len(frames),
        "frames": [_frame_summary_row(instance_id, frame) for frame in rows],
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Return downloads captured by an instance. Pass `after` (a cursor from a previous call) "
        "for incremental reads. Pass response_mode='summary' to return browser_downloads_summary "
        "instead of raw rows."
    ),
)
def browser_downloads(
    instance_id: str,
    after: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    if response_mode == "summary":
        return browser_downloads_summary(instance_id, after=after)
    items = pool.get(instance_id).list_downloads()
    start = after or 0
    return {
        "downloads": items[start:],
        "next_cursor": len(items),
        "total": len(items),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Return a compact summary of captured downloads without dumping every row. "
        "Use before browser_downloads to inspect file types, source hosts, and recent downloads."
    ),
)
def browser_downloads_summary(
    instance_id: str,
    after: int | None = None,
    recent_limit: int = 8,
) -> dict[str, Any]:
    items = pool.get(instance_id).list_downloads()
    start = max(0, after or 0)
    sliced = items[start:]
    capped_recent = max(0, min(int(recent_limit), 25))
    recent = sliced[-capped_recent:] if capped_recent else []
    recent_start = start + len(sliced) - len(recent)
    return {
        "total": len(items),
        "count": len(sliced),
        "next_cursor": len(items),
        "by_extension": _sorted_counts(Counter(_download_extension(item) for item in sliced)),
        "by_host": _sorted_counts(Counter(_download_host(item) for item in sliced)),
        "recent": [
            _download_summary_row(instance_id, recent_start + offset, item) for offset, item in enumerate(recent)
        ],
        "recent_limit": capped_recent,
        "next_actions": _download_summary_next_actions(instance_id, len(items)),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Block until the next download completes for an instance, or raise if timeout exceeded. "
        "Returns the new download record."
    ),
)
async def browser_wait_for_download(
    instance_id: str,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    return await pool.get(instance_id).wait_for_download(timeout_ms=timeout_ms)
