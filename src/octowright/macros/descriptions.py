# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Human-friendly action descriptions.

Used by the macro-status pill (browser_pool/visuals.py) and by the macro
runtime (macros/execution.py) to produce a one-line summary of a single
macro action. Lives in ``octowright.macros`` so the pool layer can pull
this from the macro layer without the inverse import direction.
"""

from __future__ import annotations

from typing import Any


def describe_action(action: dict[str, Any]) -> str:
    """One-line human hint for a macro action — ``"<verb> <key>=<value>"``.

    Picks the first informative locator/value field (``name`` → ``text`` →
    ``role`` → ``selector`` → ``url`` → ``key`` → ``value``) so the pill
    stays single-line. Long values are clipped with an ellipsis to fit the
    pill's max-width.
    """
    name = str(action.get("action") or "?")
    for key in ("name", "text", "role", "selector", "url", "key", "value"):
        val = action.get(key)
        if val in (None, "", [], {}):
            continue
        s = str(val)
        if len(s) > 40:
            s = s[:39] + "…"
        return f"{name} {key}={s}"
    return name
