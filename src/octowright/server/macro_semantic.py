# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any


def summarize_action(action: dict[str, Any], indent: int = 0) -> str:
    """Convert a macro action into a human-readable string."""
    kind = action.get("action")
    prefix = "  " * indent

    if kind == "navigate":
        return f"{prefix}Navigate to {action['url']}"
    elif kind == "click":
        return f"{prefix}Click '{action['selector']}'"
    elif kind == "type":
        return f"{prefix}Type '{action.get('text', '')}' into '{action['selector']}'"
    elif kind == "fill":
        return f"{prefix}Fill '{action['selector']}' with '{action.get('value', '')}'"
    elif kind == "press_key":
        return f"{prefix}Press key '{action['key']}'"
    elif kind == "wait_for":
        desc = f"Wait for '{action['selector']}'"
        if action.get("text"):
            desc += f" containing text '{action['text']}'"
        return f"{prefix}{desc} to appear"
    elif kind == "if_selector":
        selector = action["selector"]
        present = action.get("present", True)
        cond = "present" if present else "absent"
        summary = [f"{prefix}If '{selector}' is {cond}:"]
        for sub in action.get("then", []):
            summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        if action.get("else"):
            summary.append(f"{prefix}Else:")
            for sub in action["else"]:
                summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        return "\n".join(summary)
    elif kind == "try":
        summary = [f"{prefix}Try (ignore errors):"]
        for sub in action.get("actions", []):
            summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        return "\n".join(summary)
    elif kind == "try_each":
        summary = [f"{prefix}Try each branch until success:"]
        for i, branch in enumerate(action.get("branches", [])):
            summary.append(f"{prefix}  Branch {i+1}:")
            for sub in branch:
                summary.append(f"{prefix}    - " + summarize_action(sub, 0))
        return "\n".join(summary)

    return f"{prefix}Perform {kind} action"
