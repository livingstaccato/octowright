# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.http.discovery import _summarise_recording
from octowright.recorder import Recorder


def _write(tmp_path: Path, name: str, opening: dict) -> Path:
    log_path = tmp_path / name
    recorder = Recorder(log_path)
    fields = dict(opening)
    action = fields.pop("action")
    if action == "session_start":
        recorder.record_control(action, **fields)
    else:
        recorder.record(action, **fields)
    recorder.close()
    return log_path


def test_session_start_supplies_kind_label_and_profile(tmp_path):
    log_path = _write(
        tmp_path,
        "20260823T000000Z-refkind-sessionzz01.jsonl",
        {"action": "session_start", "kind": "refkind", "label": "demo", "profile": "tanuki"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "refkind"
    assert summary["label"] == "demo"
    assert summary["profile"] == "tanuki"
    assert summary["live"] is False


def test_a_kind_whose_plugin_is_gone_still_classifies(tmp_path):
    # The whole point: no plugin is installed here, and the recording is still
    # reported with its real kind rather than degrading to "unknown".
    log_path = _write(
        tmp_path,
        "20260823T000000Z-neverinstalled-sessionzz01.jsonl",
        {"action": "session_start", "kind": "neverinstalled", "label": None, "profile": None},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "neverinstalled"


def test_browser_launch_rows_are_unchanged(tmp_path):
    log_path = _write(
        tmp_path,
        "20260823T000000Z-chromium-sessionzz01.jsonl",
        {"action": "launch", "kind": "chromium", "label": "shop", "profile": "tanuki", "url": "https://x.test"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "chromium"
    assert summary["url"] == "https://x.test"


def test_terminal_start_rows_still_classify(tmp_path):
    # A recording that opens with the plugin's own row rather than core's
    # generic `session_start` still classifies, from its filename -- core reads
    # no plugin-specific field to do it.
    log_path = _write(
        tmp_path,
        "20260823T000000Z-terminal-sessionzz01.jsonl",
        {"action": "terminal_start", "connector_type": "pty"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "terminal"
    assert "connector_type" not in summary
