# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compact human-readable summaries for live-state tool results.

Each MCP tool that returns a list of live entities (browsers, scenarios,
participants) prefixes its payload with a short `summary` string so the MCP
client sees a one-line gist before the structured data.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def short_url(url: str | None, max_chars: int = 48) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url[:max_chars]
    host = parsed.netloc or ""
    path = parsed.path or ""
    if path == "/":
        path = ""
    rendered = f"{host}{path}" if host else url
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 1] + "…"


def short_id(instance_id: str) -> str:
    return instance_id[:6]


def browser_summary(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "0 browsers"
    parts = []
    for s in sessions:
        tag = s.get("profile") or s.get("label") or short_id(s.get("instance_id", "?"))
        kind = s.get("kind", "?")
        url = short_url(s.get("url"))
        suffix = f" @ {url}" if url else ""
        parts.append(f"{tag}/{kind}{suffix}")
    return f"{len(sessions)} browser{'s' if len(sessions) != 1 else ''}: " + " · ".join(parts)


def participant_summary(participants: list[dict[str, Any]]) -> str:
    parts = []
    for p in participants:
        role = p.get("role", "?")
        persona = p.get("persona", "?")
        kind = p.get("kind", "?")
        parts.append(f"{role}[{persona}]/{kind}")
    return " · ".join(parts)


def scenario_summary(scenarios: list[dict[str, Any]]) -> str:
    if not scenarios:
        return "0 live scenarios"
    if len(scenarios) == 1:
        s = scenarios[0]
        ps = s.get("participants", [])
        return f"scenario {s.get('name', '?')!r} ({len(ps)} participants): {participant_summary(ps)}"
    lines = []
    for s in scenarios:
        ps = s.get("participants", [])
        lines.append(f"  {s.get('name', '?')!r} ({len(ps)}): {participant_summary(ps)}")
    return f"{len(scenarios)} live scenarios:\n" + "\n".join(lines)
