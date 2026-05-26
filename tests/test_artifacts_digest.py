# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright.artifacts.digest import digest_macro, digest_recording_text, truncate_text


def test_truncate_text_reports_size_and_cap() -> None:
    result = truncate_text("abcdef", max_chars=3)

    assert result == {
        "summary": "abc",
        "truncated": True,
        "source_size": 6,
        "cap": 3,
    }


def test_digest_macro_summarizes_actions_and_params() -> None:
    macro = {
        "name": "login",
        "parameters": ["email", "password"],
        "actions": [
            {"action": "navigate", "url": "https://example.test"},
            {"action": "fill", "selector": "#email", "value": "{{email}}"},
            {"action": "click", "selector": "button"},
        ],
    }

    result = digest_macro(macro, max_chars=4000)

    assert result["truncated"] is False
    assert "Macro login" in result["summary"]
    assert "parameters: email, password" in result["summary"]
    assert "navigate: 1" in result["summary"]
    assert "fill: 1" in result["summary"]


def test_digest_recording_text_counts_jsonl_actions(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"action": "launch", "url": "https://example.test"}),
                json.dumps({"action": "click"}),
                "not-json",
                json.dumps({"action": "click"}),
            ]
        ),
        encoding="utf-8",
    )

    result = digest_recording_text(path.read_text(), max_chars=4000)

    assert "events: 3" in result["summary"]
    assert "malformed: 1" in result["summary"]
    assert "click: 2" in result["summary"]
