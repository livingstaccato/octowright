# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp


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
        if_summary: list[str] = [f"{prefix}If '{selector}' is {cond}:"]
        for sub in action.get("then", []):
            if_summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        if action.get("else"):
            if_summary.append(f"{prefix}Else:")
            for sub in action["else"]:
                if_summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        return "\n".join(if_summary)
    elif kind == "try":
        try_summary: list[str] = [f"{prefix}Try (ignore errors):"]
        for sub in action.get("actions", []):
            try_summary.append(f"{prefix}  - " + summarize_action(sub, 0))
        return "\n".join(try_summary)
    elif kind == "try_each":
        try_each_summary: list[str] = [f"{prefix}Try each branch until success:"]
        for i, branch in enumerate(action.get("branches", [])):
            try_each_summary.append(f"{prefix}  Branch {i + 1}:")
            for sub in branch:
                try_each_summary.append(f"{prefix}    - " + summarize_action(sub, 0))
        return "\n".join(try_each_summary)

    return f"{prefix}Perform {kind} action"


def get_semantic_intent(actions: list[dict[str, Any]]) -> str:
    """Extract a concise semantic intent from a list of macro actions."""
    if not actions:
        return "Empty macro"

    # Simple heuristic-based intent extraction
    urls = [a["url"] for a in actions if a.get("action") == "navigate"]
    fills = [
        f"{a['selector']}={a.get('value') or a.get('text')}" for a in actions if a.get("action") in ("fill", "type")
    ]

    if any("login" in url.lower() for url in urls):
        creds = ", ".join(fills)
        return f"Login to {urls[0]} with {creds}" if creds else f"Login to {urls[0]}"

    # Check for login-like fields if no login URL found
    if any("email" in f.lower() or "user" in f.lower() for f in fills) and any("pass" in f.lower() for f in fills):
        target = f" on {urls[0]}" if urls else ""
        return f"Login flow{target}"

    if any("search" in url.lower() for url in urls):
        query = next((f.split("=")[1] for f in fills if "search" in f.lower() or "q" in f.lower()), None)
        return f"Search for '{query}' on {urls[0]}" if query else f"Search on {urls[0]}"

    if urls:
        return f"Interact with {urls[0]}"

    return f"Perform {len(actions)} actions"


@mcp.tool(
    structured_output=True,
    description="Explain what a macro does in plain English and provide its semantic intent.",
)
async def macro_explain(actions: list[dict[str, Any]]) -> dict[str, str]:
    """
    Explain what a macro does in plain English and provide its semantic intent.

    Args:
        actions: List of macro actions (JSONL format).
    """
    summary_lines = []
    for action in actions:
        summary_lines.append(summarize_action(action))

    return {
        "summary": "\n".join(summary_lines),
        "intent": get_semantic_intent(actions),
    }
