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


def test_digest_recording_text_sanitizes_token_bearing_urls() -> None:
    text = json.dumps(
        {
            "action": "navigate",
            "url": "https://user:pass@example.test/path?token=secret#frag",
        }
    )

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://example.test/path" in result["summary"]
    assert "last_url: https://example.test/path" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_reports_same_first_and_last_url() -> None:
    text = json.dumps({"action": "navigate", "url": "https://example.test/path?token=secret"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://example.test/path" in result["summary"]
    assert "last_url: https://example.test/path" in result["summary"]


def test_digest_recording_text_handles_malformed_url_ports() -> None:
    text = json.dumps({"action": "navigate", "url": "https://example.test:bad/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_no_host_userinfo_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://user:pass@/path?token=x#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_protocol_relative_no_host_userinfo_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "//user:pass@/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_rejects_multi_slash_userinfo_like_relative_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "////user:pass@host/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_unclosed_ipv6_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[::1/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_preserves_bracketed_ipv6_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[::1]/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://[::1]/path" in result["summary"]
    assert "last_url: https://[::1]/path" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_preserves_bracketed_ipv6_urls_with_port() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[2001:db8::1]:8443/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://[2001:db8::1]:8443/path" in result["summary"]
    assert "last_url: https://[2001:db8::1]:8443/path" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_invalid_nfkc_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://exa\u2100mple.test/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_macro_uses_fallback_for_non_scalar_action_values() -> None:
    macro = {
        "name": "bad-actions",
        "actions": [
            {"action": {"nested": "secret"}},
            {"action": ["secret"]},
            {"action": "click"},
        ],
    }

    result = digest_macro(macro, max_chars=4000)

    assert "(invalid): 2" in result["summary"]
    assert "click: 1" in result["summary"]
    assert "secret" not in result["summary"]
    assert "nested" not in result["summary"]


def test_digest_macro_uses_fallback_for_non_scalar_parameters() -> None:
    macro = {
        "name": "bad-params",
        "parameters": ["email", {"credential": "hidden-value"}, ["hidden-list"]],
        "actions": [],
    }

    result = digest_macro(macro, max_chars=4000)

    assert "parameters: email, (invalid), (invalid)" in result["summary"]
    assert "credential" not in result["summary"]
    assert "hidden-value" not in result["summary"]
    assert "hidden-list" not in result["summary"]
