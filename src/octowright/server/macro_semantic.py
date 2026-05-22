# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from octowright.server._state import mcp

# --- summarize_action: per-kind formatters + dispatch ---------------------


def _sum_navigate(a: dict, p: str) -> str:
    return f"{p}Navigate to {a['url']}"


def _sum_click(a: dict, p: str) -> str:
    return f"{p}Click '{a['selector']}'"


def _sum_type(a: dict, p: str) -> str:
    return f"{p}Type '{a.get('text', '')}' into '{a['selector']}'"


def _sum_fill(a: dict, p: str) -> str:
    return f"{p}Fill '{a['selector']}' with '{a.get('value', '')}'"


def _sum_press_key(a: dict, p: str) -> str:
    return f"{p}Press key '{a['key']}'"


def _sum_wait_for(a: dict, p: str) -> str:
    desc = f"Wait for '{a.get('selector', '')}'"
    if a.get("text"):
        desc += f" containing text '{a['text']}'"
    return f"{p}{desc} to appear"


def _sum_if_selector(a: dict, p: str) -> str:
    cond = "present" if a.get("present", True) else "absent"
    lines = [f"{p}If '{a['selector']}' is {cond}:"]
    for sub in a.get("then", []):
        lines.append(f"{p}  - " + summarize_action(sub, 0))
    if a.get("else"):
        lines.append(f"{p}Else:")
        for sub in a["else"]:
            lines.append(f"{p}  - " + summarize_action(sub, 0))
    return "\n".join(lines)


def _sum_try(a: dict, p: str) -> str:
    lines = [f"{p}Try (ignore errors):"]
    for sub in a.get("actions", []):
        lines.append(f"{p}  - " + summarize_action(sub, 0))
    return "\n".join(lines)


def _sum_try_each(a: dict, p: str) -> str:
    lines = [f"{p}Try each branch until success:"]
    for i, branch in enumerate(a.get("branches", [])):
        lines.append(f"{p}  Branch {i + 1}:")
        for sub in branch:
            lines.append(f"{p}    - " + summarize_action(sub, 0))
    return "\n".join(lines)


def _sum_macro_call(a: dict, p: str) -> str:
    name = a["name"]
    args = a.get("args") or {}
    if args:
        arg_summary = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{p}Call macro '{name}' with args {{ {arg_summary} }}"
    return f"{p}Call macro '{name}'"


_SUMMARIZERS: dict[str, Callable[[dict, str], str]] = {
    "navigate": _sum_navigate,
    "click": _sum_click,
    "type": _sum_type,
    "fill": _sum_fill,
    "press_key": _sum_press_key,
    "wait_for": _sum_wait_for,
    "if_selector": _sum_if_selector,
    "try": _sum_try,
    "try_each": _sum_try_each,
    "macro_call": _sum_macro_call,
}


def summarize_action(action: dict[str, Any], indent: int = 0) -> str:
    """Convert a macro action into a human-readable string."""
    kind = action.get("action")
    prefix = "  " * indent
    fmt = _SUMMARIZERS.get(kind or "")
    if fmt is not None:
        return fmt(action, prefix)
    return f"{prefix}Perform {kind} action"


# --- get_semantic_intent: per-shape detectors + first-match dispatch ------


def _intent_login_url(urls: list[str], fills: list[str]) -> str | None:
    if not any("login" in url.lower() for url in urls):
        return None
    creds = ", ".join(fills)
    return f"Login to {urls[0]} with {creds}" if creds else f"Login to {urls[0]}"


def _intent_login_fields(urls: list[str], fills: list[str]) -> str | None:
    has_user = any("email" in f.lower() or "user" in f.lower() for f in fills)
    has_pass = any("pass" in f.lower() for f in fills)
    if not (has_user and has_pass):
        return None
    target = f" on {urls[0]}" if urls else ""
    return f"Login flow{target}"


def _intent_search(urls: list[str], fills: list[str]) -> str | None:
    if not any("search" in url.lower() for url in urls):
        return None
    query = next((f.split("=")[1] for f in fills if "search" in f.lower() or "q" in f.lower()), None)
    return f"Search for '{query}' on {urls[0]}" if query else f"Search on {urls[0]}"


def _intent_url_fallback(urls: list[str], _fills: list[str]) -> str | None:
    return f"Interact with {urls[0]}" if urls else None


_INTENT_DETECTORS: tuple[Callable[[list[str], list[str]], str | None], ...] = (
    _intent_login_url,
    _intent_login_fields,
    _intent_search,
    _intent_url_fallback,
)


def get_semantic_intent(actions: list[dict[str, Any]]) -> str:
    """Extract a concise semantic intent from a list of macro actions."""
    if not actions:
        return "Empty macro"
    urls = [a["url"] for a in actions if a.get("action") == "navigate"]
    fills = [
        f"{a['selector']}={a.get('value') or a.get('text')}" for a in actions if a.get("action") in ("fill", "type")
    ]
    for detector in _INTENT_DETECTORS:
        result = detector(urls, fills)
        if result is not None:
            return result
    return f"Perform {len(actions)} actions"


@mcp.tool(
    structured_output=False,
    description="Explain what a macro does in plain English and provide its semantic intent.",
)
async def macro_explain(actions: list[dict[str, Any]]) -> dict[str, str]:
    """
    Explain what a macro does in plain English and provide its semantic intent.

    Args:
        actions: List of macro actions (JSONL format).
    """
    summary_lines = [summarize_action(action) for action in actions]
    return {
        "summary": "\n".join(summary_lines),
        "intent": get_semantic_intent(actions),
    }
