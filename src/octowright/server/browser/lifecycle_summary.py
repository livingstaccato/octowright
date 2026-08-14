# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compact browser-list result helpers."""

from __future__ import annotations

from typing import Any


def _short_text(value: Any, cap: int) -> str | None:
    if value is None:
        return None
    return str(value)[:cap]


def browser_list_summary_row(session: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(session.get("instance_id") or "")
    row = {
        "instance_id": instance_id,
        "kind": session.get("kind"),
        "label": session.get("label"),
        "profile": session.get("profile"),
        "url": _short_text(session.get("url"), 200),
        "title": _short_text(session.get("title"), 120),
        "protected": bool(session.get("protected")),
        "operation_gate": session.get("operation_gate"),
        "actions": [
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id}},
            {"tool": "browser_close", "args": {"instance_id": instance_id}},
        ],
    }
    return {key: value for key, value in row.items() if value is not None}
