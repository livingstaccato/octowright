# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Console inspection tools for browser sessions."""

from __future__ import annotations

from collections import Counter
from typing import Any, cast

from octowright.mcp_types import BrowserConsoleMessagesResult, ConsoleMessage
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session._constants import is_diagnostic_console_message


@mcp.tool(
    structured_output=False,
    description=(
        "Return console messages from an instance. Optionally filter by level "
        "(e.g. 'error', 'warning') and pass `since` (a cursor returned from a "
        "previous call) for incremental reads. Pass response_mode='summary' to "
        "return browser_console_summary with the same filters instead of raw rows."
    ),
)
def browser_console_messages(
    instance_id: str,
    level: str | None = None,
    since: int | None = None,
    response_mode: str | None = None,
) -> BrowserConsoleMessagesResult | dict[str, Any]:
    # response_mode='summary' returns browser_console_summary's shape, not the
    # raw-rows result — declared honestly so callers/type-checkers see both.
    if response_mode == "summary":
        return browser_console_summary(instance_id, since=since, level=level)
    msgs = list(pool.get(instance_id).console)
    start = since or 0
    sliced = msgs[start:]
    filtered = [m for m in sliced if m.get("level") == level] if level else sliced
    return {
        "messages": cast("list[ConsoleMessage]", filtered),
        "next_cursor": len(msgs),
        "total": len(msgs),
    }


def _count_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count} for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _console_text(message: dict[str, Any], cap: int) -> str:
    text = message.get("text")
    if text is None:
        text = message.get("message", "")
    return str(text)[:cap]


def _console_message_action(instance_id: str, index: int, message: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {"instance_id": instance_id, "since": index}
    if message.get("level"):
        args["level"] = message.get("level")
    return {"tool": "browser_console_summary", "args": args}


def _console_summary_next_actions(instance_id: str, next_cursor: int) -> list[dict[str, Any]]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_console_summary", "args": {"instance_id": instance_id, "since": next_cursor}},
            {"tool": "browser_console_summary", "args": {"instance_id": instance_id, "level": "error"}},
            {
                "tool": "capture_create",
                "args": {"instance_id": instance_id, "source": "console", "response_mode": "summary"},
            },
        ]
    )


def _filter_console_messages(
    msgs: list[dict[str, Any]], *, start: int, level_filter: str | None
) -> list[dict[str, Any]]:
    return [msg for msg in msgs[start:] if level_filter is None or str(msg.get("level") or "").lower() == level_filter]


def _important_console_messages(
    msgs: list[dict[str, Any]], *, start: int, level_filter: str | None
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, msg)
        for index, msg in enumerate(msgs[start:], start=start)
        if (level_filter is None or str(msg.get("level") or "").lower() == level_filter)
        and is_diagnostic_console_message(msg)
    ]


def _console_recent_rows(
    instance_id: str, recent: list[tuple[int, dict[str, Any]]], text_chars: int
) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "level": msg.get("level"),
            "text": _console_text(msg, text_chars),
            "action": _console_message_action(instance_id, index, msg),
        }
        for index, msg in recent
    ]


def _console_warning_count(messages: list[dict[str, Any]]) -> int:
    return sum(1 for msg in messages if str(msg.get("level", "")).lower() in {"warning", "warn"})


@mcp.tool(
    structured_output=False,
    description=(
        "Return a compact console summary without dumping every message. "
        "Use before browser_console_messages to inspect level counts and recent warnings/errors."
    ),
)
def browser_console_summary(
    instance_id: str,
    since: int | None = None,
    level: str | None = None,
    recent_limit: int = 8,
    text_chars: int = 240,
) -> dict[str, Any]:
    msgs = list(pool.get(instance_id).console)
    start = max(0, since or 0)
    level_filter = str(level).lower() if level else None
    sliced = _filter_console_messages(msgs, start=start, level_filter=level_filter)
    capped_recent = max(0, min(int(recent_limit), 25))
    capped_text = max(0, min(int(text_chars), 1_000))
    important = _important_console_messages(msgs, start=start, level_filter=level_filter)
    recent = important[-capped_recent:] if capped_recent else []
    return {
        "total": len(msgs),
        "count": len(sliced),
        "next_cursor": len(msgs),
        "by_level": _count_items(Counter(str(msg.get("level") or "unknown") for msg in sliced)),
        "error_count": sum(1 for msg in sliced if str(msg.get("level", "")).lower() == "error"),
        "warning_count": _console_warning_count(sliced),
        "recent": _console_recent_rows(instance_id, recent, capped_text),
        "recent_limit": capped_recent,
        "next_actions": _console_summary_next_actions(instance_id, len(msgs)),
    }
