# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Helpers for ``scenario start --watch`` event formatting."""

from __future__ import annotations

from typing import Any

# Watch event fields that don't add information for the human reader.
_WATCH_HIDDEN_FIELDS = frozenset(
    {"ts", "action", "instance_id", "persona", "role", "kind", "label", "profile", "user_data_dir", "viewport"}
)
# Field-name salience order: when an event has one of these, that's the "headline" arg.
_WATCH_HEADLINE_FIELDS = ("url", "selector", "text", "key", "name", "pattern", "expression", "policy", "path")


def _format_watch_event(ev: dict[str, Any]) -> str | None:
    """One-line scenario-watch event format.

    `[HH:MM:SS] persona/role  action  headline   …extras` — or None to skip.
    """
    action = ev.get("action", "?")
    if action == "console":
        return None
    ts = ev.get("ts", "")[11:19] or "--:--:--"
    persona = ev.get("persona", "?")
    role = ev.get("role", "?")

    headline = ""
    for field in _WATCH_HEADLINE_FIELDS:
        if field in ev and ev[field] is not None:
            val = ev[field]
            rendered = val if isinstance(val, str) else repr(val)
            if len(rendered) > 60:
                rendered = rendered[:57] + "…"
            headline = rendered
            break

    extras_pairs = [
        f"{k}={v!r}"
        for k, v in ev.items()
        if k not in _WATCH_HIDDEN_FIELDS and k not in _WATCH_HEADLINE_FIELDS and v is not None
    ]
    extras = "  " + " ".join(extras_pairs) if extras_pairs else ""

    tag = f"{persona}/{role}"
    return f"[{ts}] {tag:<22}  {action:<14} {headline}{extras}"
