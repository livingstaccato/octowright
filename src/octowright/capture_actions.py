# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follow-up action payload helpers for cached captures."""

from __future__ import annotations

from typing import Any


def base_capture_next_actions(capture_id: str, default_slice_chars: int) -> list[dict[str, Any]]:
    return [
        {"tool": "capture_summary", "args": {"capture_id": capture_id, "limit": 40}},
        {"tool": "capture_search", "args": {"capture_id": capture_id, "query": "<query>", "limit": 20}},
        {"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": 1, "limit": 80}},
        {"tool": "capture_get", "args": {"capture_id": capture_id, "offset": 0, "limit": default_slice_chars}},
    ]


def capture_search_next_actions(capture_id: str, default_slice_chars: int) -> list[dict[str, Any]]:
    return [
        action
        for action in base_capture_next_actions(capture_id, default_slice_chars)
        if action["tool"] != "capture_search"
    ]


def listed_capture_actions(capture_id: str, default_slice_chars: int) -> list[dict[str, Any]]:
    return [
        {"tool": "capture_summary", "args": {"capture_id": capture_id, "limit": 40}},
        {"tool": "capture_search", "args": {"capture_id": capture_id, "query": "<query>", "limit": 20}},
        {"tool": "capture_get", "args": {"capture_id": capture_id, "offset": 0, "limit": default_slice_chars}},
    ]
