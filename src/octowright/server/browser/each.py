# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fan-out variants of common per-browser actions.

`browser_navigate`, `browser_resize`, etc. operate on one instance. When you
want the same action across N live browsers (e.g. a responsive sweep), the
LLM had to fire N parallel tool calls. These variants take the same args
plus an optional `instance_ids` list and dispatch concurrently via
`asyncio.gather`, returning a dict keyed by instance_id.

If `instance_ids` is omitted, every live browser in the pool runs the action.
"""

from __future__ import annotations

import asyncio
from typing import Any

from octowright.server._state import mcp, pool


def _select_instance_ids(instance_ids: list[str] | None) -> list[str]:
    if instance_ids:
        return list(instance_ids)
    return [session["instance_id"] for session in pool.list_sessions()]


async def _gather(
    instance_ids: list[str],
    work: Any,
) -> dict[str, dict[str, Any]]:
    """Run `work(instance_id)` for each id concurrently. Catch per-instance
    errors so one failure doesn't tank the whole call."""

    async def _one(iid: str) -> tuple[str, dict[str, Any]]:
        try:
            value = await work(iid)
        except Exception as exc:
            return iid, {"ok": False, "error": repr(exc)}
        return iid, {"ok": True, "result": value}

    pairs = await asyncio.gather(*(_one(iid) for iid in instance_ids))
    return dict(pairs)


@mcp.tool(
    structured_output=False,
    description=(
        "Navigate multiple browsers to the same URL in parallel. Pass "
        "instance_ids to scope; omit to navigate every live browser. "
        "Returns {<instance_id>: {ok, result|error}}."
    ),
)
async def browser_navigate_each(
    url: str,
    instance_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    return await _gather(ids, lambda iid: pool.get(iid).navigate(url))


@mcp.tool(
    structured_output=False,
    description=(
        "Resize multiple browser viewports to width x height CSS pixels in "
        "parallel. Pass instance_ids to scope; omit to resize every live "
        "browser. Returns {<instance_id>: {ok, result|error}}."
    ),
)
async def browser_resize_each(
    width: int,
    height: int,
    instance_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    return await _gather(ids, lambda iid: pool.get(iid).resize(width, height))


@mcp.tool(
    structured_output=False,
    description=(
        "Evaluate a JavaScript expression across multiple browsers in "
        "parallel. Same evaluate semantics as browser_evaluate; result per "
        "instance is the stringified return value (truncated at 4000 chars "
        "per instance). Pass instance_ids to scope; omit for all live "
        "browsers. Returns {<instance_id>: {ok, result|error}}."
    ),
)
async def browser_evaluate_each(
    expression: str,
    instance_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    return await _gather(ids, lambda iid: pool.get(iid).evaluate(expression))


@mcp.tool(
    structured_output=False,
    description=(
        "Wait for a selector or text to appear across multiple browsers in "
        "parallel. Provide exactly one of selector or text, or neither for "
        "network-idle. Pass instance_ids to scope; omit for all live "
        "browsers. Returns {<instance_id>: {ok, result|error}}."
    ),
)
async def browser_wait_for_each(
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
    instance_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    return await _gather(
        ids,
        lambda iid: pool.get(iid).wait_for(selector=selector, text=text, timeout_ms=timeout_ms),
    )


async def _shoot(iid: str) -> dict[str, str]:
    session = pool.get(iid)
    target = session.log_path.with_suffix(".png")
    out = await session.screenshot(target)
    return {"path": str(out)}


@mcp.tool(
    structured_output=False,
    description=(
        "Take a screenshot of multiple browsers in parallel. Each screenshot "
        "is written next to that instance's recording (path is auto-derived "
        "from the log_path). Pass instance_ids to scope; omit for all live "
        "browsers. Returns {<instance_id>: {ok, result|error}}, where "
        "result.path is the written file path."
    ),
)
async def browser_screenshot_each(
    instance_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    return await _gather(ids, _shoot)
