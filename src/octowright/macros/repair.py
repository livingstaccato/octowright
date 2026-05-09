# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro repair and selector-healing helpers."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from octowright.mcp_types import MacroRepairPreviewResult, MacroRepairSuggestion
from octowright.server.macro_semantic import summarize_action

if TYPE_CHECKING:
    from octowright.session import BrowserSession


async def suggest_fix(session: BrowserSession, action: dict[str, Any]) -> str | None:
    """Build an A11y-context prompt for fixing a failed selector action."""
    selector = action.get("selector")
    if not selector:
        return None

    try:
        snapshot = await session.snapshot()
        aria = snapshot["aria"]
    except Exception:
        return None

    summary = summarize_action(action)
    return (
        f"I was trying to {summary}, but '{selector}' failed.\n\n"
        f"Current A11y tree:\n---\n{aria}\n---\n\n"
        "Based on the A11y tree, what should I use instead?"
    )


def semantic_replacement(action: dict[str, Any], *, semantic_keys: tuple[str, ...]) -> dict[str, Any] | None:
    kind = action.get("action")
    if kind not in {"click", "fill"} or not action.get("selector"):
        return None

    semantic = {k: action[k] for k in semantic_keys if k in action and action[k] is not None}
    if not semantic:
        return None

    replacement: dict[str, Any] = {"action": f"{kind}_by", **semantic}
    if kind == "fill" and "value" in action:
        replacement["value"] = action["value"]
    if "timeout_ms" in action:
        replacement["timeout_ms"] = action["timeout_ms"]
    return replacement


def replacement_preview(action: dict[str, Any]) -> str:
    kind = action.get("action")
    if kind == "click_by":
        target = action.get("role_name") or action.get("label") or action.get("text") or action.get("test_id")
        return f"Click by {target!r}" if target else "Click by semantic locator"
    if kind == "fill_by":
        target = action.get("role_name") or action.get("label") or action.get("text") or action.get("test_id")
        value = action.get("value", "")
        return f"Fill by {target!r} with {value!r}" if target else f"Fill by semantic locator with {value!r}"
    return summarize_action(action)


def repair_preview(
    name: str,
    *,
    load_macro: Callable[[str], dict[str, Any]],
    semantic_keys: tuple[str, ...],
) -> MacroRepairPreviewResult:
    """Return non-mutating repair suggestions for selector-based macro actions."""
    macro = load_macro(name)
    macro_name = macro.get("name") or name
    suggestions: list[MacroRepairSuggestion] = []
    for idx, action in enumerate(macro.get("actions", [])):
        if not isinstance(action, dict) or "selector" not in action:
            continue

        replacement = semantic_replacement(action, semantic_keys=semantic_keys)
        selector = action.get("selector")
        prompt = (
            f"Review selector {selector!r} for action {idx}. "
            "If it no longer matches, compare the stored semantic fields against the current page."
        )
        suggestions.append(
            {
                "macro": macro_name,
                "action_index": idx,
                "original_action": copy.deepcopy(action),
                "source": "stored_heuristic",
                "replacement_action": replacement,
                "action_preview": replacement_preview(replacement) if replacement else None,
                "prompt": prompt,
            }
        )

    return {"macro": macro_name, "suggestions": suggestions}
