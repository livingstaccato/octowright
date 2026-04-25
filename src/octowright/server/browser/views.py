# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pages, frames, and downloads tools."""

from __future__ import annotations

from typing import Any

from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "List all pages/tabs for an instance. The active page (the one every other "
        "per-instance tool targets) has is_active=True. Popups opened by the browser "
        "are tracked automatically and appear here."
    ),
)
def page_list(instance_id: str) -> list[dict[str, Any]]:
    return pool.get(instance_id).list_pages()


@mcp.tool(
    structured_output=False,
    description=(
        "Switch the active page for an instance. Subsequent tool calls (click, fill, "
        "evaluate, etc.) target the newly-active page."
    ),
)
async def page_switch(instance_id: str, index: int) -> dict[str, Any]:
    return await pool.get(instance_id).switch_page(index)


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
        "or url_pattern. Use browser_reset_frame to switch back."
    ),
)
async def browser_switch_frame(
    instance_id: str,
    selector: str | None = None,
    name: str | None = None,
    url_pattern: str | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).switch_frame(
        selector=selector,
        name=name,
        url_pattern=url_pattern,
    )


@mcp.tool(structured_output=False, description="Reset the active target to the top-level page.")
async def browser_reset_frame(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).reset_frame()


@mcp.tool(structured_output=False, description="List all frames on the active page (including main).")
def browser_list_frames(instance_id: str) -> list[dict[str, Any]]:
    return pool.get(instance_id).list_frames()


@mcp.tool(
    structured_output=False,
    description=(
        "Return downloads captured by an instance. Pass `after` (a cursor from a previous call) for incremental reads."
    ),
)
def browser_downloads(
    instance_id: str,
    after: int | None = None,
) -> dict[str, Any]:
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
        "Block until the next download completes for an instance, or raise if timeout exceeded. "
        "Returns the new download record."
    ),
)
async def browser_wait_for_download(
    instance_id: str,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    return await pool.get(instance_id).wait_for_download(timeout_ms=timeout_ms)
