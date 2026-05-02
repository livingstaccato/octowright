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

    return f"{prefix}Perform {kind} action"
