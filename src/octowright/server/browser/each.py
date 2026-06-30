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
import json
from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile

DEFAULT_PREVIEW_CHARS = 4000


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
    result = await session.evaluate(expression)
    cap = None if kwargs.get("full") else (kwargs.get("max_chars") or DEFAULT_PREVIEW_CHARS)
    rendered = result if isinstance(result, str | bytes) else json.dumps(result, default=str)
    if isinstance(rendered, bytes):
        rendered = rendered.decode("utf-8", errors="replace")
    if cap is not None and len(rendered) > cap:
        return {
            "result": rendered[:cap],
            "truncated": True,
            "result_size": len(rendered),
            "cap": cap,
            "next_actions": annotate_next_actions_for_profile(
                [
                    {
                        "tool": "browser_evaluate",
                        "args": {"instance_id": session.instance_id, "expression": expression, "full": True},
                    }
                ]
            ),
        }
    return {"result": result, "truncated": False, "result_size": len(rendered)}


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


def _summary_row(instance_id: str, record: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"instance_id": instance_id, "ok": bool(record.get("ok"))}
    if record.get("ok"):
        row["result"] = record.get("result")
    else:
        row["error"] = record.get("error")
    return row


def _clean_next_action_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None and value is not False}


def _summarize_results(
    action: str,
    results: dict[str, dict[str, Any]],
    *,
    limit: int,
    action_args: dict[str, Any],
) -> dict[str, Any]:
    capped = max(0, min(int(limit), 100))
    items = list(results.items())
    rows = items[:capped]
    ok_count = sum(1 for record in results.values() if record.get("ok"))
    next_args = _clean_next_action_args(
        {
            "action": action,
            **action_args,
            "response_mode": "summary",
            "limit": limit,
        }
    )
    return {
        "action": action,
        "total": len(results),
        "returned": len(rows),
        "ok_count": ok_count,
        "error_count": len(results) - ok_count,
        "truncated": len(rows) < len(results),
        "results": [_summary_row(instance_id, record) for instance_id, record in rows],
        "next_actions": [{"tool": "browser_each", "args": next_args}],
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
        "  evaluate  — expression (str); stringified results are truncated to "
        "~4000 chars by default, pass max_chars=N for a custom cap or full=True "
        "to disable truncation\n"
        "  wait_for  — selector (str) or text (str) or neither for network-idle; "
        "optional timeout_ms (int)\n"
        "  screenshot — no extra params; each screenshot is written next to the "
        "instance's recording and the path is returned.\n"
        "Returns {<instance_id>: {ok, result|error}} — one failing instance does "
        "not cancel the others. Pass response_mode='summary' for counts plus bounded "
        "per-instance rows instead of the full keyed result map."
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
    max_chars: int | None = None,
    full: bool = False,
    response_mode: str | None = None,
    limit: int = 20,
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
        "max_chars": max_chars,
        "full": full,
    }
    results = await _gather(ids, lambda iid: _dispatch(action, iid, **kwargs))
    if response_mode == "summary":
        return _summarize_results(
            action,
            results,
            limit=limit,
            action_args={"instance_ids": instance_ids, **kwargs},
        )
    return results
