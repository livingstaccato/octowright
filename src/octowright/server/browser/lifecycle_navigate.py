# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Navigate / resize / viewport / open-url tools, split out of
``lifecycle.py`` to keep that module under the repository's LOC ceiling
(it was already at 549/550 before this task's operation-gate wrapping).

Same pattern as ``discovery.py``/``discovery_links.py`` and
``inspect.py``/``inspect_capture.py``: this module registers its own
``@mcp.tool``s and ``lifecycle.py`` imports + re-exports them so
``from octowright.server.browser.lifecycle import browser_navigate`` (used
by ``server/browser/__init__.py``) keeps resolving unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from octowright.defaults import SNAPSHOT_TIMEOUT_SECONDS
from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.browser.inspect import browser_brief, browser_page_outline

__all__ = [
    "browser_navigate",
    "browser_navigate_back",
    "browser_open_url",
    "browser_resize",
    "browser_viewport_status",
    "browser_viewport_sync",
]


@mcp.tool(
    structured_output=False,
    description=(
        "Navigate an instance to a URL. Use this to go to a new page; do NOT use for "
        "in-app routing that the SPA handles via clicks (use browser_click instead). "
        "Equivalent to typing the URL in the address bar and hitting enter. "
        "Pass response_mode='outline' to also return a compact browser_page_outline "
        "(headings, landmarks, links, fields) in the same call, or response_mode='brief' "
        "for the older aria-based browser_brief snapshot."
    ),
)
async def browser_navigate(
    instance_id: str,
    url: str,
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_navigate") as session:
        res: dict[str, Any] = await session.navigate(url)
        if response_mode == "outline":
            res["outline"] = await browser_page_outline(instance_id)
        if response_mode == "brief":
            # asyncio.timeout (not wait_for) — browser_brief re-enters the
            # SAME gate this boundary already holds via exact-task
            # reentrancy; wait_for would run it in a separate Task via
            # ensure_future, which the gate treats as a different owner and
            # queues forever (deadlock) since this task is blocked on it.
            try:
                async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
                    res["brief"] = await browser_brief(instance_id)
            except TimeoutError:
                res["brief_warning"] = (
                    f"browser_brief timed out after {SNAPSHOT_TIMEOUT_SECONDS:.1f}s; "
                    "navigation succeeded, call browser_brief separately or use a scoped snapshot."
                )
        return res


@mcp.tool(
    structured_output=False,
    description=(
        "Navigate back in the browser's history (equivalent to clicking the Back button). "
        "Returns {ok, url, title} — ok is False when there is no previous page in history. "
        "Use this after a browser_navigate or link-click to return to the prior page. "
        "Do NOT use for in-app routing where the SPA manages its own history stack. "
        "Pass response_mode='outline' to include a compact browser_page_outline in the same call."
    ),
)
async def browser_navigate_back(instance_id: str, response_mode: str | None = None) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_navigate_back") as session:
        res = await session.navigate_back()
        out = dict(res)
        if response_mode == "outline":
            out["outline"] = await browser_page_outline(instance_id)
        return out


@mcp.tool(
    structured_output=False,
    description=(
        "Resize the browser viewport to the given width x height in CSS pixels. "
        "Use this to test responsive layouts, simulate mobile screen sizes, or ensure "
        "elements are visible at a specific viewport dimension. Does not resize the OS window "
        "— only the page's viewport."
    ),
)
async def browser_resize(instance_id: str, width: int, height: int) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_resize") as session:
        return await session.resize(width, height)


@mcp.tool(
    structured_output=False,
    description="Return fixed/fluid viewport status and measured page/window dimensions.",
)
async def browser_viewport_status(instance_id: str) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_viewport_status") as session:
        return await session.viewport_status()


@mcp.tool(
    structured_output=False,
    description="Resize a fixed Playwright viewport once to the current measured browser window size.",
)
async def browser_viewport_sync(instance_id: str) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_viewport_sync") as session:
        return await session.viewport_sync()


@mcp.tool(
    structured_output=False,
    description=(
        "Open a URL in a new tab or new window of an existing instance. "
        "target='tab' (default) opens a new page in the same browser context — "
        "behaves like cmd-T then typing a URL. target='window' opens it in a "
        "separate OS window via window.open(...,'popup',...) — useful when the "
        "user explicitly says 'in a new window'. For 'window', width and height "
        "set the popup window size (defaults 1024x768). Returns {ok, target, "
        "page_index, url}; the new page is appended to the instance's page list "
        "and is the same shape page_switch / page_close use. Pass response_mode='outline' "
        "to include a compact browser_page_outline for the newly active page in the same call."
    ),
)
async def browser_open_url(
    instance_id: str,
    url: str,
    target: str = "tab",
    width: int = 1024,
    height: int = 768,
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_open_url") as session:
        res = await session.open_url(url, target=target, width=width, height=height)
        out = dict(res)
        if response_mode == "outline":
            out["outline"] = await browser_page_outline(instance_id)
        return out
