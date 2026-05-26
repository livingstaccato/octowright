# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from collections import Counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

DEFAULT_DIGEST_CHARS = 4000
MAX_LABEL_CHARS = 80
MAX_ACTION_COUNTS = 20
INVALID_URL = "(invalid-url)"
SAFE_SCALAR_TYPES = (str, int, float, bool)


def truncate_text(text: str, *, max_chars: int = DEFAULT_DIGEST_CHARS) -> dict[str, Any]:
    cap = max(0, int(max_chars))
    truncated = len(text) > cap
    return {
        "summary": text[:cap] if truncated else text,
        "truncated": truncated,
        "source_size": len(text),
        "cap": cap,
    }


def digest_macro(macro: dict[str, Any], *, max_chars: int = DEFAULT_DIGEST_CHARS) -> dict[str, Any]:
    raw_actions = macro.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    counts = Counter(_safe_label(action.get("action", "unknown")) for action in actions if isinstance(action, dict))
    raw_parameters = macro.get("parameters")
    parameters = raw_parameters if isinstance(raw_parameters, list) else []
    lines = [
        f"Macro {_safe_label(macro.get('name', '(unnamed)'))}",
        f"parameters: {_format_parameters(parameters)}",
        f"actions: {len(actions)}",
    ]
    lines.extend(f"{name}: {count}" for name, count in sorted(counts.items())[:MAX_ACTION_COUNTS])
    return truncate_text("\n".join(lines), max_chars=max_chars)


def digest_recording_text(text: str, *, max_chars: int = DEFAULT_DIGEST_CHARS) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    events = 0
    malformed = 0
    first_url = ""
    last_url = ""
    for line in text.splitlines():
        malformed_line, entry = _parse_recording_line(line)
        if entry is None:
            malformed += int(malformed_line)
            continue
        events += 1
        action = _safe_label(entry.get("action", "unknown"))
        counts[action] += 1
        if url := _sanitize_url(_entry_url(entry)):
            first_url = first_url or url
            last_url = url
    lines = [f"events: {events}", f"malformed: {malformed}"]
    if first_url:
        lines.append(f"first_url: {first_url}")
    if last_url:
        lines.append(f"last_url: {last_url}")
    lines.extend(f"{name}: {count}" for name, count in sorted(counts.items())[:MAX_ACTION_COUNTS])
    return truncate_text("\n".join(lines), max_chars=max_chars)


def _parse_recording_line(line: str) -> tuple[bool, dict[str, Any] | None]:
    if not line.strip():
        return False, None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return True, None
    if not isinstance(entry, dict):
        return True, None
    return False, entry


def _entry_url(entry: dict[str, Any]) -> str:
    url = entry.get("url")
    return url if isinstance(url, str) else ""


def _format_parameters(parameters: list[Any]) -> str:
    if not parameters:
        return "(none)"
    return ", ".join(_safe_label(parameter) for parameter in parameters)


def _safe_label(value: Any) -> str:
    if not isinstance(value, SAFE_SCALAR_TYPES):
        return "(invalid)"
    text = str(value)
    return text[:MAX_LABEL_CHARS]


def _sanitize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return INVALID_URL
    hostname = parts.hostname or ""
    if parts.netloc and not hostname:
        return INVALID_URL
    if not hostname:
        return _sanitize_relative_url(parts)
    netloc = _sanitize_hostname(hostname)
    try:
        port = parts.port
    except ValueError:
        return INVALID_URL
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _sanitize_hostname(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def _sanitize_relative_url(parts: Any) -> str:
    if parts.scheme:
        return INVALID_URL
    relative_url = parts._replace(query="", fragment="").geturl()
    return INVALID_URL if _looks_like_relative_userinfo(relative_url) else relative_url[:MAX_LABEL_CHARS]


def _looks_like_relative_userinfo(url: str) -> bool:
    if not url.startswith("/"):
        return False
    userinfo_end = url.find("@")
    return userinfo_end != -1 and ":" in url[:userinfo_end]
