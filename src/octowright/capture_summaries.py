# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compact summaries for stored captures."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from octowright.console_levels import count_errors, count_warnings, is_diagnostic_console_message

MAX_LINE_SLICE_LINES = 200
MAX_SUMMARY_ITEMS = 100
SUMMARY_TEXT_CHARS = 240
JSON_SUMMARY_TEXT_CHARS = 88


def _summary_kind(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("#"):
        return "heading"
    if re.match(
        r"^[-*]\s+(button|link|textbox|combobox|checkbox|radio|heading|navigation|main|form)\b", stripped, re.I
    ):
        return "aria"
    if re.match(r"^[-*]\s+", stripped):
        return "list"
    if re.search(r"\[[^\]]+\]\([^)]+\)", stripped) or re.search(r"https?://", stripped):
        return "link"
    if re.match(r'^\s*"[^"]+"\s*:', line) or re.match(r"^\s*[{[]", line):
        return "json"
    return None


def _capture_search_action(capture_id: str, query: str) -> dict[str, Any]:
    return {"tool": "capture_search", "args": {"capture_id": capture_id, "query": query}}


def _sorted_counts(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count} for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _status_class_key(request: dict[str, Any]) -> str:
    status = request.get("status")
    if status is None:
        return "failed"
    if isinstance(status, int):
        if 200 <= status <= 299:
            return "2xx"
        if 300 <= status <= 399:
            return "3xx"
        if 400 <= status <= 499:
            return "4xx"
        if 500 <= status <= 599:
            return "5xx"
    return "other"


def _ordered_status_class_counts(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(_status_class_key(request) for request in requests)
    order = ["2xx", "3xx", "4xx", "5xx", "failed", "other"]
    return [{"key": key, "count": counter[key]} for key in order if counter[key] > 0]


def _request_host(request: dict[str, Any]) -> str:
    url = str(request.get("url") or "")
    try:
        parsed = urlparse(url)
    except Exception:
        return "unknown-host"
    return parsed.netloc or "unknown-host"


def _is_request_failure(request: dict[str, Any]) -> bool:
    status = request.get("status")
    return status is None or (isinstance(status, int) and status >= 400)


def _extract_network_requests(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        raw = data.get("requests")
    elif isinstance(data, list):
        raw = data
    else:
        raw = None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _load_json_object(content: str) -> Any | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _network_problem_hosts(capture_id: str, failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failure_hosts = Counter(_request_host(request) for request in failures)
    return [
        {"host": host, "failure_count": count, "action": _capture_search_action(capture_id, host)}
        for host, count in sorted(failure_hosts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _network_error_count(requests: list[dict[str, Any]]) -> int:
    return sum(1 for request in requests if request.get("status") is None)


def _http_error_count(requests: list[dict[str, Any]]) -> int:
    return sum(1 for request in requests if isinstance(request.get("status"), int) and request["status"] >= 400)


def _network_json_summary(capture_id: str, content: str) -> dict[str, Any] | None:
    data = _load_json_object(content)
    requests = _extract_network_requests(data)
    if not requests:
        return None
    failures = [request for request in requests if _is_request_failure(request)]
    return {
        "type": "network",
        "request_count": len(requests),
        "host_count": len({_request_host(request) for request in requests}),
        "http_error_count": _http_error_count(requests),
        "network_error_count": _network_error_count(requests),
        "has_failures": bool(failures),
        "by_status_class": _ordered_status_class_counts(requests),
        "problem_hosts": _network_problem_hosts(capture_id, failures),
    }


def _message_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if text is None:
        text = message.get("message", "")
    return str(text)[:JSON_SUMMARY_TEXT_CHARS]


def _extract_console_messages(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _console_recent(capture_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    important = [message for message in messages if is_diagnostic_console_message(message)]
    return [
        {
            "level": message.get("level"),
            "text": _message_text(message),
            "action": _capture_search_action(capture_id, _message_text(message)),
        }
        for message in important[-5:]
    ]


def _console_json_summary(capture_id: str, content: str) -> dict[str, Any] | None:
    messages = _extract_console_messages(_load_json_object(content))
    if not messages:
        return None
    return {
        "type": "console",
        "message_count": len(messages),
        "error_count": count_errors(messages),
        "warning_count": count_warnings(messages),
        "by_level": _sorted_counts(Counter(str(message.get("level") or "unknown") for message in messages)),
        "recent": _console_recent(capture_id, messages),
    }


def _capture_lines_action(capture_id: str, line: int, limit: int = 1) -> dict[str, Any]:
    return {"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": line, "limit": limit}}


def _capture_summary_next_actions(capture_id: str, default_slice_chars: int) -> list[dict[str, Any]]:
    return [
        {"tool": "capture_search", "args": {"capture_id": capture_id, "query": "<query>", "limit": 20}},
        {"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": 1, "limit": 80}},
        {"tool": "capture_get", "args": {"capture_id": capture_id, "offset": 0, "limit": default_slice_chars}},
        {"tool": "capture_list", "args": {"limit": 50}},
    ]


def _recording_target(event: dict[str, Any]) -> str | None:
    for key in ("selector", "url", "text", "label", "role_name"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value[:JSON_SUMMARY_TEXT_CHARS]
    return None


def _parse_jsonl_events(content: str) -> list[tuple[int, dict[str, Any]]]:
    events: list[tuple[int, dict[str, Any]]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append((line_no, event))
    return events


def _recording_json_summary(capture_id: str, content: str) -> dict[str, Any] | None:
    events = _parse_jsonl_events(content)
    if not events:
        return None
    action_counter = Counter(str(event.get("action") or "unknown") for _, event in events)
    urls = {event["url"] for _, event in events if isinstance(event.get("url"), str) and event["url"]}
    recent: list[dict[str, Any]] = []
    for line_no, event in events[-3:]:
        row: dict[str, Any] = {
            "action": str(event.get("action") or "unknown"),
            "line": line_no,
            "follow_up": _capture_lines_action(capture_id, line_no),
        }
        target = _recording_target(event)
        if target is not None:
            row["target"] = target
        recent.append(row)
    return {
        "type": "recording",
        "event_count": len(events),
        "url_count": len(urls),
        "by_action": _sorted_counts(action_counter),
        "recent": recent,
    }


def _json_summary(capture_id: str, kind: str, content: str) -> dict[str, Any] | None:
    if kind == "network":
        return _network_json_summary(capture_id, content)
    if kind == "console":
        return _console_json_summary(capture_id, content)
    if kind == "recording":
        return _recording_json_summary(capture_id, content)
    return None


def _attach_outline_line_actions(outline: list[dict[str, Any]], *, capture_id: str, line_count: int) -> None:
    for index, item in enumerate(outline):
        start_line = int(item["line"])
        if index + 1 < len(outline):
            next_line = int(outline[index + 1]["line"])
            limit = max(1, next_line - start_line)
        else:
            limit = min(MAX_LINE_SLICE_LINES, max(1, line_count - start_line + 1))
        item["action"] = {
            "tool": "capture_lines",
            "args": {"capture_id": capture_id, "start_line": start_line, "limit": limit},
        }


def summarize_capture_payload(
    *,
    capture_id: str,
    payload: dict[str, Any],
    path: Path,
    default_slice_chars: int,
    limit: int = 40,
) -> dict[str, Any]:
    content = str(payload.get("content", ""))
    lines = content.splitlines()
    capped_limit = max(1, min(int(limit), MAX_SUMMARY_ITEMS))
    outline: list[dict[str, Any]] = []
    matched_total = 0
    for index, line in enumerate(lines, start=1):
        kind = _summary_kind(line)
        if kind is None:
            continue
        matched_total += 1
        if len(outline) >= capped_limit:
            continue
        outline.append({"line": index, "kind": kind, "text": " ".join(line.strip().split())[:SUMMARY_TEXT_CHARS]})
    _attach_outline_line_actions(outline, capture_id=capture_id, line_count=len(lines))
    result: dict[str, Any] = {
        "capture_id": capture_id,
        "kind": str(payload.get("kind", "unknown")),
        "host": str(payload.get("host", "unknown-host")),
        "url": payload.get("url") if isinstance(payload.get("url"), str) else None,
        "title": payload.get("title") if isinstance(payload.get("title"), str) else None,
        "size_chars": len(content),
        "line_count": len(lines),
        "nonempty_line_count": sum(1 for line in lines if line.strip()),
        "outline_count": matched_total,
        "returned": len(outline),
        "truncated": matched_total > len(outline),
        "outline": outline,
        "next_actions": _capture_summary_next_actions(capture_id, default_slice_chars),
        "path": str(path),
    }
    json_summary = _json_summary(capture_id, result["kind"], content)
    if json_summary is not None:
        result["json_summary"] = json_summary
    return result
