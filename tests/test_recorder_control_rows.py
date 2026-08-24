# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.plugins.errors import ControlBudgetExceededError
from octowright.recorder import CONTROL_ACTIONS, CONTROL_BUDGET_BYTES, Recorder


def _actions(path: Path) -> list[str]:
    return [json.loads(line)["action"] for line in path.read_text().splitlines() if line.strip()]


def test_control_row_written_when_ceiling_already_hit(tmp_path, monkeypatch):
    # A ceiling smaller than any row: the first ordinary record truncates.
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "10")
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    rec.record("click", selector="#a")
    assert _actions(log) == ["recording_truncated"]

    rec.record_control("session_start", kind="refkind", label=None, profile=None)
    rec.close()

    assert _actions(log) == ["recording_truncated", "session_start"]


def test_control_rows_do_not_consume_the_action_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "500")
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    rec.record_control("session_start", kind="refkind", label=None, profile=None)
    # The control row must not have eaten the action budget.
    for i in range(3):
        rec.record("click", selector=f"#a{i}")
    rec.close()

    assert _actions(log).count("click") == 3
    assert "recording_truncated" not in _actions(log)


def test_record_control_rejects_a_non_control_action(tmp_path):
    rec = Recorder(tmp_path / "r.jsonl")
    with pytest.raises(ValueError, match="not a control action"):
        rec.record_control("click", selector="#a")
    rec.close()


def test_control_budget_is_bounded(tmp_path):
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    payload = "x" * 1024
    with pytest.raises(ControlBudgetExceededError):
        for i in range(CONTROL_BUDGET_BYTES // 1024 + 2):
            rec.record_control("artifact_registered", artifact_id=f"a{i}", path=payload)
    rec.close()


@pytest.mark.parametrize("action", sorted(CONTROL_ACTIONS))
def test_record_refuses_a_control_action(tmp_path, action):
    # The control set is core-only; record() is a plugin's sole surface. A
    # plugin able to write one could forge the opening row the failed-launch
    # rule reasons about, or claim an artifact core never registered.
    rec = Recorder(tmp_path / "r.jsonl")
    try:
        with pytest.raises(ValueError, match="control action"):
            rec.record(action, kind="refkind")
    finally:
        rec.close()

    assert _actions(tmp_path / "r.jsonl") == []


def test_the_truncation_marker_still_lands_despite_the_record_guard(tmp_path, monkeypatch):
    # _write_truncation_marker writes straight to the file handle rather than
    # through record(), so the guard cannot suppress the marker.
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "10")
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    rec.record("click", selector="#a")
    rec.close()

    assert _actions(log) == ["recording_truncated"]
