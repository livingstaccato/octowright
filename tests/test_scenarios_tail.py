# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright.scenarios import Scenario
from octowright.scenarios_pool import LiveScenario, ScenarioPool


def _make_live(tmp_path: Path) -> tuple[ScenarioPool, Path, Path]:
    pool = ScenarioPool()
    spec = Scenario(name="t", participants=[])
    log_a = tmp_path / "a.jsonl"
    log_b = tmp_path / "b.jsonl"
    log_a.touch()
    log_b.touch()
    live = LiveScenario(
        scenario_id="abc123",
        name="t",
        spec=spec,
        participants=[
            {"instance_id": "i-a", "persona": "cosmo", "role": "player", "log_path": str(log_a)},
            {"instance_id": "i-b", "persona": "ziggy", "role": "monitor", "log_path": str(log_b)},
        ],
    )
    pool._live["abc123"] = live
    return pool, log_a, log_b


def _append(p: Path, entry: dict) -> None:
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def test_empty_cursor_returns_all(tmp_path: Path) -> None:
    pool, log_a, log_b = _make_live(tmp_path)
    _append(log_a, {"ts": "T1", "action": "navigate", "url": "u1"})
    _append(log_b, {"ts": "T2", "action": "click", "selector": "#x"})
    out = pool.tail(scenario_id="abc123")
    assert len(out["events"]) == 2
    actions = sorted(e["action"] for e in out["events"])
    assert actions == ["click", "navigate"]
    # personas attached
    persona_by_action = {e["action"]: e["persona"] for e in out["events"]}
    assert persona_by_action == {"navigate": "cosmo", "click": "ziggy"}
    assert out["cursors"]["i-a"] == log_a.stat().st_size
    assert out["cursors"]["i-b"] == log_b.stat().st_size


def test_stale_cursor_returns_only_new(tmp_path: Path) -> None:
    pool, log_a, _ = _make_live(tmp_path)
    _append(log_a, {"ts": "T1", "action": "first"})
    first = pool.tail(scenario_id="abc123")
    _append(log_a, {"ts": "T2", "action": "second"})
    second = pool.tail(scenario_id="abc123", since_cursors=first["cursors"])
    actions = [e["action"] for e in second["events"] if e["instance_id"] == "i-a"]
    assert actions == ["second"]


def test_up_to_date_cursor_returns_nothing(tmp_path: Path) -> None:
    pool, log_a, _ = _make_live(tmp_path)
    _append(log_a, {"ts": "T1", "action": "x"})
    first = pool.tail(scenario_id="abc123")
    second = pool.tail(scenario_id="abc123", since_cursors=first["cursors"])
    assert all(e["instance_id"] != "i-a" for e in second["events"])


def test_partial_line_skipped_until_complete(tmp_path: Path) -> None:
    pool, log_a, _ = _make_live(tmp_path)
    # Write a complete line followed by a partial (no trailing newline)
    log_a.write_bytes(b'{"ts":"T1","action":"x"}\n{"ts":"T2","action":"par')
    out1 = pool.tail(scenario_id="abc123")
    actions = [e["action"] for e in out1["events"] if e["instance_id"] == "i-a"]
    assert actions == ["x"]
    # Append the rest of the partial line in-place (cursor stays valid)
    with log_a.open("ab") as fh:
        fh.write(b'tial"}\n')
    out2 = pool.tail(scenario_id="abc123", since_cursors=out1["cursors"])
    actions = [e["action"] for e in out2["events"] if e["instance_id"] == "i-a"]
    assert actions == ["partial"]


def test_multi_participant_events_tagged(tmp_path: Path) -> None:
    pool, log_a, log_b = _make_live(tmp_path)
    _append(log_a, {"ts": "T1", "action": "navigate", "url": "http://a"})
    _append(log_b, {"ts": "T2", "action": "click", "selector": "#btn"})
    out = pool.tail(scenario_id="abc123")
    events_by_iid = {e["instance_id"]: e for e in out["events"]}
    assert events_by_iid["i-a"]["persona"] == "cosmo"
    assert events_by_iid["i-a"]["role"] == "player"
    assert events_by_iid["i-b"]["persona"] == "ziggy"
    assert events_by_iid["i-b"]["role"] == "monitor"


def test_missing_log_file_preserves_cursor(tmp_path: Path) -> None:
    pool, log_a, log_b = _make_live(tmp_path)
    # Remove log_b so it doesn't exist
    log_b.unlink()
    _append(log_a, {"ts": "T1", "action": "navigate"})
    out = pool.tail(scenario_id="abc123")
    # Only i-a events returned
    assert all(e["instance_id"] == "i-a" for e in out["events"])
    # i-b cursor remains 0 (no advancement)
    assert out["cursors"]["i-b"] == 0
