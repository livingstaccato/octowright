# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro repair and selector-healing helpers."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from octowright.macros._redact import _redact_action
from octowright.macros.semantic import summarize_action
from octowright.mcp_types import MacroRepairApplyResult, MacroRepairPreviewResult, MacroRepairSuggestion

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike


async def suggest_fix(session: SessionLike, action: dict[str, Any]) -> str | None:
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
        # This suggestion is returned verbatim to the MCP client — redact
        # credential-bearing fields on both the original and the proposed
        # replacement (and the preview string built from it) before it
        # leaves this function. The stored macro on disk is untouched;
        # only the response is scrubbed.
        redacted_replacement = _redact_action(replacement) if replacement is not None else None
        suggestions.append(
            {
                "macro": macro_name,
                "action_index": idx,
                "original_action": _redact_action(copy.deepcopy(action)),
                "source": "stored_heuristic",
                "replacement_action": redacted_replacement,
                "action_preview": replacement_preview(redacted_replacement) if redacted_replacement else None,
                "prompt": prompt,
            }
        )

    return {"macro": macro_name, "suggestions": suggestions}


def repair_apply(
    name: str,
    action_index: int,
    *,
    load_macro: Callable[[str], dict[str, Any]],
    write_macro: Callable[..., Any],
    semantic_keys: tuple[str, ...],
) -> MacroRepairApplyResult:
    """Apply the stored-heuristic semantic replacement for one action and persist the macro.

    Rewrites a brittle selector-based ``click``/``fill`` at ``action_index`` into its
    ``click_by``/``fill_by`` equivalent (built from the role/label/text/test_id captured at
    record time) and writes the macro back in place. Raises ``ValueError`` — before any write —
    when the index is out of range or the action has no stored semantic locator to repair with.
    """
    macro = load_macro(name)
    macro_name = macro.get("name") or name
    actions = macro.get("actions", [])
    if not isinstance(actions, list) or not (0 <= action_index < len(actions)):
        count = len(actions) if isinstance(actions, list) else 0
        raise ValueError(
            f"action_index {action_index} is out of range for macro {macro_name!r} "
            f"({count} actions); run macro_repair_preview to see repairable action indices"
        )

    action = actions[action_index]
    if not isinstance(action, dict):
        raise ValueError(f"action {action_index} in macro {macro_name!r} is not an object; cannot repair")

    replacement = semantic_replacement(action, semantic_keys=semantic_keys)
    if replacement is None:
        raise ValueError(
            f"action {action_index} in macro {macro_name!r} has no stored semantic locator to repair with "
            "(needs a click/fill with a selector plus role/label/text/test_id); "
            "run macro_repair_preview for a manual review prompt, or re-record the macro"
        )

    original = copy.deepcopy(action)
    actions[action_index] = replacement
    macro["actions"] = actions
    # Persist under the name this macro was LOADED under (`name`), never the
    # macro's own internal "name" field (`macro_name`, used above only for
    # display in error messages). load_macro(name) reads the file at
    # macro_path(name); writing back under a different internal name would
    # silently move the macro to a DIFFERENT file, orphaning the one at
    # `name` and potentially colliding with (and clobbering) an unrelated
    # macro whose slug matches that internal name — write_macro's collision
    # guard exists precisely to catch that, so don't hand it the footgun.
    path = write_macro(name=name, macro=macro)
    return {
        "macro": name,
        "action_index": action_index,
        "applied": True,
        # Returned to the MCP client: redact credential-bearing fields.
        # The macro already persisted above (unredacted, as it must be to
        # replay correctly) — only this response is scrubbed.
        "original_action": _redact_action(original),
        "replacement_action": _redact_action(replacement),
        "path": str(path),
    }
