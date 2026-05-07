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


def _format_headline(ev: dict[str, Any]) -> str:
    """Pick the most-salient field's value, rendered + clipped to 60 chars."""
    for field in _WATCH_HEADLINE_FIELDS:
        if field in ev and ev[field] is not None:
            val = ev[field]
            rendered = val if isinstance(val, str) else repr(val)
            if len(rendered) > 60:
                rendered = rendered[:57] + "…"
            return rendered
    return ""


def _format_extras(ev: dict[str, Any]) -> str:
    """Render any non-hidden, non-headline fields as `k=v` pairs."""
    pairs = [
        f"{k}={v!r}"
        for k, v in ev.items()
        if k not in _WATCH_HIDDEN_FIELDS and k not in _WATCH_HEADLINE_FIELDS and v is not None
    ]
    return "  " + " ".join(pairs) if pairs else ""


def _format_watch_event(ev: dict[str, Any]) -> str | None:
    """One-line scenario-watch event format.

    `[HH:MM:SS] persona/role  action  headline   …extras` — or None to skip.
    """
    action = ev.get("action", "?")
    if action == "console":
        return None
    ts = ev.get("ts", "")[11:19] or "--:--:--"
    tag = f"{ev.get('persona', '?')}/{ev.get('role', '?')}"
    return f"[{ts}] {tag:<22}  {action:<14} {_format_headline(ev)}{_format_extras(ev)}"
