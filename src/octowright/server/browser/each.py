# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Consolidated fan-out tool: browser_each.

Replaces the five individual browser_*_each tools (navigate, resize, evaluate,
wait_for, screenshot) with a single tool dispatched by `action`.

If `instance_ids` is omitted, every live browser in the pool runs the action.
One failing instance is isolated — its error is returned under its key and
does not cancel the others.
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
    """Run work(instance_id) for each id concurrently."""

    async def _one(iid: str) -> tuple[str, dict[str, Any]]:
        try:
            value = await work(iid)
        except Exception as exc:
            return iid, {"ok": False, "error": repr(exc)}
        return iid, {"ok": True, "result": value}

    pairs = await asyncio.gather(*(_one(iid) for iid in instance_ids))
    return dict(pairs)


_ACTIONS = frozenset({"navigate", "resize", "evaluate", "wait_for", "screenshot"})


async def _act_navigate(session: Any, kwargs: dict[str, Any]) -> Any:
    url = kwargs.get("url")
    if url is None:
        raise ValueError("url is required for navigate")
    return await session.navigate(url)


async def _act_resize(session: Any, kwargs: dict[str, Any]) -> Any:
    width, height = kwargs.get("width"), kwargs.get("height")
    if width is None or height is None:
        raise ValueError(f"{'width' if width is None else 'height'} is required for resize")
    return await session.resize(width, height)


async def _act_evaluate(session: Any, kwargs: dict[str, Any]) -> Any:
    expression = kwargs.get("expression")
    if expression is None:
        raise ValueError("expression is required for evaluate")
    return await session.evaluate(expression)


async def _act_wait_for(session: Any, kwargs: dict[str, Any]) -> Any:
    return await session.wait_for(
        selector=kwargs.get("selector"),
        text=kwargs.get("text"),
        timeout_ms=kwargs.get("timeout_ms"),
    )


async def _act_screenshot(session: Any, _kwargs: dict[str, Any]) -> Any:
    target = session.log_path.with_suffix(".png")
    out = await session.screenshot(target)
    return {"path": str(out)}


_DISPATCH: dict[str, Any] = {
    "navigate": _act_navigate,
    "resize": _act_resize,
    "evaluate": _act_evaluate,
    "wait_for": _act_wait_for,
    "screenshot": _act_screenshot,
}


async def _dispatch(action: str, iid: str, **kwargs: Any) -> Any:
    handler = _DISPATCH.get(action)
    if handler is None:
        raise ValueError(f"unknown action {action!r}; expected {' | '.join(sorted(_ACTIONS))}")
    return await handler(pool.get(iid), kwargs)


@mcp.tool(
    structured_output=False,
    description=(
        "Run one browser action across multiple sessions in parallel. "
        "action must be one of: navigate | resize | evaluate | wait_for | screenshot.\n"
        "Pass instance_ids to scope the fan-out; omit to target every live browser.\n"
        "Per-action required params:\n"
        "  navigate  — url (str)\n"
        "  resize    — width (int), height (int)\n"
        "  evaluate  — expression (str)\n"
        "  wait_for  — selector (str) or text (str) or neither for network-idle; "
        "optional timeout_ms (int)\n"
        "  screenshot — no extra params; each screenshot is written next to the "
        "instance's recording and the path is returned.\n"
        "Returns {<instance_id>: {ok, result|error}} — one failing instance does "
        "not cancel the others."
    ),
)
async def browser_each(
    action: str,
    instance_ids: list[str] | None = None,
    url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    expression: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    ids = _select_instance_ids(instance_ids)
    kwargs: dict[str, Any] = {
        "url": url,
        "width": width,
        "height": height,
        "expression": expression,
        "selector": selector,
        "text": text,
        "timeout_ms": timeout_ms,
    }
    return await _gather(ids, lambda iid: _dispatch(action, iid, **kwargs))
