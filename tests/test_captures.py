# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import time
from pathlib import Path

from octowright import captures


def test_save_get_search_and_list_capture(tmp_path: Path) -> None:
    saved = captures.save_capture(
        kind="text",
        content="alpha\nEnter your alias\nomega",
        url="https://warp.undef.games/customize",
        title="Warp",
        instance_id="abc123",
        root=tmp_path,
        max_total_bytes=10_000,
        ttl_seconds=3600,
        preview_chars=5,
    )

    assert saved["truncated"] is True
    assert saved["preview"] == "alpha"
    assert saved["host"] == "warp.undef.games"
    assert saved["actions"] == ["capture_summary", "capture_search", "capture_lines", "capture_get", "capture_list"]
    assert saved["next_actions"] == [
        {"tool": "capture_summary", "args": {"capture_id": saved["capture_id"], "limit": 40}},
        {"tool": "capture_search", "args": {"capture_id": saved["capture_id"], "query": "<query>", "limit": 20}},
        {"tool": "capture_lines", "args": {"capture_id": saved["capture_id"], "start_line": 1, "limit": 80}},
        {
            "tool": "capture_get",
            "args": {"capture_id": saved["capture_id"], "offset": 0, "limit": captures.DEFAULT_SLICE_CHARS},
        },
    ]

    sliced = captures.get_capture_slice(saved["capture_id"], offset=6, limit=5, root=tmp_path)
    assert sliced["content"] == "Enter"
    assert sliced["next_offset"] == 11

    found = captures.search_capture(saved["capture_id"], "alias", root=tmp_path, context_chars=8)
    assert found["count"] == 1
    assert "alias" in found["matches"][0]["context"]
    assert found["matches"][0]["action"] == {
        "tool": "capture_get",
        "args": {
            "capture_id": saved["capture_id"],
            "offset": found["matches"][0]["context_start"],
            "limit": found["matches"][0]["context_end"] - found["matches"][0]["context_start"],
        },
    }
    assert found["next_actions"] == [
        {"tool": "capture_summary", "args": {"capture_id": saved["capture_id"], "limit": 40}},
        {"tool": "capture_lines", "args": {"capture_id": saved["capture_id"], "start_line": 1, "limit": 80}},
        {
            "tool": "capture_get",
            "args": {"capture_id": saved["capture_id"], "offset": 0, "limit": captures.DEFAULT_SLICE_CHARS},
        },
    ]

    listed = captures.list_captures(root=tmp_path, instance_id="abc123")
    assert listed["count"] == 1
    assert listed["captures"][0]["capture_id"] == saved["capture_id"]
    assert listed["captures"][0]["actions"] == [
        {"tool": "capture_summary", "args": {"capture_id": saved["capture_id"], "limit": 40}},
        {"tool": "capture_search", "args": {"capture_id": saved["capture_id"], "query": "<query>", "limit": 20}},
        {
            "tool": "capture_get",
            "args": {"capture_id": saved["capture_id"], "offset": 0, "limit": captures.DEFAULT_SLICE_CHARS},
        },
    ]


def test_cleanup_captures_prunes_by_age_and_size(tmp_path: Path) -> None:
    old = captures.save_capture(kind="text", content="old", root=tmp_path, max_total_bytes=10_000, ttl_seconds=3600)
    new = captures.save_capture(kind="text", content="x" * 100, root=tmp_path, max_total_bytes=10_000, ttl_seconds=3600)

    old_path = Path(old["path"])
    old_time = time.time() - 10_000
    os.utime(old_path, (old_time, old_time))

    dry = captures.cleanup_captures(root=tmp_path, ttl_seconds=100, max_total_bytes=10_000, apply=False)
    assert dry["eligible_count"] == 1
    assert old_path.exists()

    applied = captures.cleanup_captures(root=tmp_path, ttl_seconds=100, max_total_bytes=10_000, apply=True)
    assert applied["removed_count"] == 1
    assert not old_path.exists()
    assert Path(new["path"]).exists()

    size_prune = captures.cleanup_captures(root=tmp_path, ttl_seconds=3600, max_total_bytes=1, apply=True)
    assert size_prune["removed_count"] == 1


def test_capture_get_and_search_enforce_response_caps(tmp_path: Path) -> None:
    saved = captures.save_capture(
        kind="text",
        content="a" * 20_000,
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    sliced = captures.get_capture_slice(saved["capture_id"], limit=1_000_000, root=tmp_path)
    assert len(sliced["content"]) == captures.MAX_SLICE_CHARS
    assert sliced["limit"] == captures.MAX_SLICE_CHARS
    assert sliced["truncated"] is True
    assert sliced["next_action"] == {
        "tool": "capture_get",
        "args": {
            "capture_id": saved["capture_id"],
            "offset": captures.MAX_SLICE_CHARS,
            "limit": captures.MAX_SLICE_CHARS,
        },
    }
    assert sliced["next_actions"] == [sliced["next_action"]]

    found = captures.search_capture(
        saved["capture_id"],
        "a",
        context_chars=1_000_000,
        limit=1_000_000,
        root=tmp_path,
    )
    assert found["count"] == captures.MAX_SEARCH_MATCHES
    assert all(len(match["context"]) <= (captures.MAX_SEARCH_CONTEXT_CHARS * 2) + 1 for match in found["matches"])


def test_summarize_capture_returns_bounded_structure_without_full_payload(tmp_path: Path) -> None:
    content = "\n".join(
        [
            "# Overview",
            "Intro paragraph",
            "## Install",
            "Use the setup guide",
            '- button "Save"',
            '- link "Pricing"',
            '- textbox "Email"',
            "[Docs](https://example.com/docs)",
            "tail " + ("x" * 5_000),
        ]
    )
    saved = captures.save_capture(
        kind="markdown",
        content=content,
        url="https://example.com/docs",
        title="Docs",
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    summary = captures.summarize_capture(saved["capture_id"], root=tmp_path, limit=5)

    assert summary["capture_id"] == saved["capture_id"]
    assert summary["kind"] == "markdown"
    assert summary["size_chars"] == len(content)
    assert summary["line_count"] == 9
    assert summary["nonempty_line_count"] == 9
    assert summary["returned"] == 5
    assert summary["truncated"] is True
    assert summary["next_actions"] == [
        {"tool": "capture_search", "args": {"capture_id": saved["capture_id"], "query": "<query>", "limit": 20}},
        {"tool": "capture_lines", "args": {"capture_id": saved["capture_id"], "start_line": 1, "limit": 80}},
        {
            "tool": "capture_get",
            "args": {"capture_id": saved["capture_id"], "offset": 0, "limit": captures.DEFAULT_SLICE_CHARS},
        },
        {"tool": "capture_list", "args": {"limit": 50}},
    ]
    assert summary["outline"][0] == {
        "line": 1,
        "kind": "heading",
        "text": "# Overview",
        "action": {
            "tool": "capture_lines",
            "args": {"capture_id": saved["capture_id"], "start_line": 1, "limit": 2},
        },
    }
    assert {
        "line": 5,
        "kind": "aria",
        "text": '- button "Save"',
        "action": {
            "tool": "capture_lines",
            "args": {"capture_id": saved["capture_id"], "start_line": 5, "limit": 1},
        },
    } in summary["outline"]
    assert "x" * 500 not in str(summary)


def test_summarize_network_capture_preparses_json_diagnostics(tmp_path: Path) -> None:
    content = """
{
  "requests": [
    {"url": "https://example.com/", "method": "GET", "resource_type": "document", "status": 200},
    {"url": "https://example.com/api/users", "method": "POST", "resource_type": "xhr", "status": 500},
    {"url": "https://api.example.com/slow", "method": "GET", "resource_type": "fetch", "status": null, "failure": "net::ERR_TIMED_OUT"}
  ]
}
""".strip()
    saved = captures.save_capture(
        kind="network",
        content=content,
        url="https://example.com",
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    summary = captures.summarize_capture(saved["capture_id"], root=tmp_path)

    assert summary["json_summary"] == {
        "type": "network",
        "request_count": 3,
        "host_count": 2,
        "http_error_count": 1,
        "network_error_count": 1,
        "has_failures": True,
        "by_status_class": [
            {"key": "2xx", "count": 1},
            {"key": "5xx", "count": 1},
            {"key": "failed", "count": 1},
        ],
        "problem_hosts": [
            {
                "host": "api.example.com",
                "failure_count": 1,
                "action": {
                    "tool": "capture_search",
                    "args": {"capture_id": saved["capture_id"], "query": "api.example.com"},
                },
            },
            {
                "host": "example.com",
                "failure_count": 1,
                "action": {
                    "tool": "capture_search",
                    "args": {"capture_id": saved["capture_id"], "query": "example.com"},
                },
            },
        ],
    }
    assert "api/users" not in str(summary["json_summary"])


def test_summarize_console_capture_preparses_json_diagnostics(tmp_path: Path) -> None:
    content = """
[
  {"level": "info", "text": "boot"},
  {"level": "warning", "text": "slow render"},
  {"level": "error", "text": "first error"},
  {"level": "error", "text": "second error xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
]
""".strip()
    saved = captures.save_capture(
        kind="console",
        content=content,
        url="https://example.com",
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    summary = captures.summarize_capture(saved["capture_id"], root=tmp_path)

    assert summary["json_summary"] == {
        "type": "console",
        "message_count": 4,
        "error_count": 2,
        "warning_count": 1,
        "by_level": [
            {"key": "error", "count": 2},
            {"key": "info", "count": 1},
            {"key": "warning", "count": 1},
        ],
        "recent": [
            {
                "level": "warning",
                "text": "slow render",
                "action": {
                    "tool": "capture_search",
                    "args": {"capture_id": saved["capture_id"], "query": "slow render"},
                },
            },
            {
                "level": "error",
                "text": "first error",
                "action": {
                    "tool": "capture_search",
                    "args": {"capture_id": saved["capture_id"], "query": "first error"},
                },
            },
            {
                "level": "error",
                "text": "second error xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "action": {
                    "tool": "capture_search",
                    "args": {
                        "capture_id": saved["capture_id"],
                        "query": "second error xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    },
                },
            },
        ],
    }
    assert "x" * 100 not in str(summary["json_summary"])


def test_summarize_recording_capture_preparses_jsonl_actions(tmp_path: Path) -> None:
    content = "\n".join(
        [
            '{"ts":"2026-01-01T00:00:00Z","action":"launch","url":"https://example.com"}',
            '{"ts":"2026-01-01T00:00:01Z","action":"navigate","url":"https://example.com/login"}',
            '{"ts":"2026-01-01T00:00:02Z","action":"fill","selector":"#email","value":"secret@example.com"}',
            '{"ts":"2026-01-01T00:00:03Z","action":"click","selector":"button[type=submit]"}',
            '{"ts":"2026-01-01T00:00:04Z","action":"snapshot"}',
        ]
    )
    saved = captures.save_capture(
        kind="recording",
        content=content,
        url="https://example.com",
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    summary = captures.summarize_capture(saved["capture_id"], root=tmp_path)

    assert summary["json_summary"] == {
        "type": "recording",
        "event_count": 5,
        "url_count": 2,
        "by_action": [
            {"key": "click", "count": 1},
            {"key": "fill", "count": 1},
            {"key": "launch", "count": 1},
            {"key": "navigate", "count": 1},
            {"key": "snapshot", "count": 1},
        ],
        "recent": [
            {
                "action": "fill",
                "line": 3,
                "target": "#email",
                "follow_up": {
                    "tool": "capture_lines",
                    "args": {"capture_id": saved["capture_id"], "start_line": 3, "limit": 1},
                },
            },
            {
                "action": "click",
                "line": 4,
                "target": "button[type=submit]",
                "follow_up": {
                    "tool": "capture_lines",
                    "args": {"capture_id": saved["capture_id"], "start_line": 4, "limit": 1},
                },
            },
            {
                "action": "snapshot",
                "line": 5,
                "follow_up": {
                    "tool": "capture_lines",
                    "args": {"capture_id": saved["capture_id"], "start_line": 5, "limit": 1},
                },
            },
        ],
    }
    assert "secret@example.com" not in str(summary["json_summary"])


def test_get_capture_lines_returns_bounded_line_range(tmp_path: Path) -> None:
    content = "\n".join(f"line {index}" for index in range(1, 21))
    saved = captures.save_capture(
        kind="text",
        content=content,
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    out = captures.get_capture_lines(saved["capture_id"], start_line=3, limit=4, root=tmp_path)

    assert out["capture_id"] == saved["capture_id"]
    assert out["start_line"] == 3
    assert out["end_line"] == 6
    assert out["next_start_line"] == 7
    assert out["line_count"] == 20
    assert out["lines"] == [
        {"line": 3, "text": "line 3"},
        {"line": 4, "text": "line 4"},
        {"line": 5, "text": "line 5"},
        {"line": 6, "text": "line 6"},
    ]
    assert out["truncated"] is True
    assert out["next_action"] == {
        "tool": "capture_lines",
        "args": {"capture_id": saved["capture_id"], "start_line": 7, "limit": 4},
    }
    assert out["next_actions"] == [out["next_action"]]


def test_get_capture_lines_clips_very_long_single_lines(tmp_path: Path) -> None:
    saved = captures.save_capture(
        kind="text",
        content="x" * (captures.MAX_LINE_TEXT_CHARS + 25),
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    out = captures.get_capture_lines(saved["capture_id"], start_line=1, limit=1, root=tmp_path)

    assert out["lines"] == [{"line": 1, "text": "x" * captures.MAX_LINE_TEXT_CHARS}]
    assert out["truncated"] is True
    assert out["line_text_chars"] == captures.MAX_LINE_TEXT_CHARS
    assert out["clipped_lines"] == [{"line": 1, "original_chars": captures.MAX_LINE_TEXT_CHARS + 25}]


def test_save_capture_prunes_after_write_to_enforce_total_size(tmp_path: Path) -> None:
    first = captures.save_capture(
        kind="text",
        content="older" * 120,
        root=tmp_path,
        max_total_bytes=10_000,
        ttl_seconds=3600,
    )
    first_path = Path(first["path"])
    old_time = time.time() - 10
    os.utime(first_path, (old_time, old_time))

    second = captures.save_capture(
        kind="text",
        content="x" * 100,
        root=tmp_path,
        max_total_bytes=900,
        ttl_seconds=3600,
    )

    assert not first_path.exists()
    assert Path(second["path"]).exists()
    assert captures.cleanup_captures(root=tmp_path, ttl_seconds=3600, max_total_bytes=900)["eligible_count"] == 0


def test_storage_report_counts_known_roots(tmp_path: Path) -> None:
    recordings = tmp_path / "state" / "sessions"
    config = tmp_path / "config"
    cache = tmp_path / "cache" / "captures"
    (recordings / "videos").mkdir(parents=True)
    (config / "profiles").mkdir(parents=True)
    cache.mkdir(parents=True)
    (recordings / "a.jsonl").write_text("{}\n")
    (config / "profiles" / "profile.yaml").write_text("name: demo\n")
    (cache / "capture.json").write_text("{}")

    report = captures.storage_report(recordings_dir=recordings, config_dir=config, captures_dir=cache)

    assert report["recordings"]["files"] == 1
    assert report["profiles"]["files"] == 1
    assert report["captures"]["files"] == 1


def test_save_capture_does_not_follow_symlink_at_target(monkeypatch, tmp_path: Path) -> None:
    """A symlink at the capture destination must be replaced atomically, not followed."""
    import json as _json

    root = tmp_path / "captures"
    root.mkdir()
    sentinel = tmp_path / "outside.json"
    sentinel.write_text("KEEP", encoding="utf-8")
    target = root / "cap.json"
    target.symlink_to(sentinel)
    monkeypatch.setattr(captures, "_capture_path", lambda *a, **k: target)

    captures.save_capture(
        kind="text",
        content="hello",
        url="https://x.test",
        root=root,
        max_total_bytes=10_000,
        ttl_seconds=3600,
        preview_chars=10,
    )

    assert sentinel.read_text(encoding="utf-8") == "KEEP"  # outside file untouched
    assert not target.is_symlink()  # symlink replaced by a real file
    assert _json.loads(target.read_text(encoding="utf-8"))["content"] == "hello"
