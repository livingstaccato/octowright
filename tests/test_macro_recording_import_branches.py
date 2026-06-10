# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.recording_import.

Targets the 10 surviving mutmut mutants by pinning every branch of
`iter_macro_actions` (whitespace skip, JSONDecodeError raise vs swallow,
ALWAYS_STRIP and LIFECYCLE membership, include_launch toggle) and the
`load_macro_from_recording` list-materialization wrapper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.macros.recording_import import (
    ALWAYS_STRIP,
    LIFECYCLE,
    iter_macro_actions,
    load_macro_from_recording,
)

# ─── Constant tables ─────────────────────────────────────────────────────────


class TestConstantTables:
    def test_always_strip_exact_membership(self) -> None:
        """If the set were mutated to include 'click' or drop 'close', tests catch."""
        assert {"close", "snapshot"} == ALWAYS_STRIP

    def test_lifecycle_exact_membership(self) -> None:
        """Mutating LIFECYCLE membership flips include_launch behavior."""
        assert {"launch"} == LIFECYCLE

    def test_no_overlap_between_strip_and_lifecycle(self) -> None:
        """An overlap would create a 'launch is always stripped even with include_launch' bug."""
        assert ALWAYS_STRIP.isdisjoint(LIFECYCLE)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _write_jsonl(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    p = tmp_path / "recording.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


# ─── iter_macro_actions: filtering ──────────────────────────────────────────


class TestIterMacroActionsFilters:
    def test_strips_close_action(self, tmp_path: Path) -> None:
        """'close' action is always filtered out, regardless of include_launch."""
        p = _write_jsonl(tmp_path, [{"action": "click", "selector": "#x"}, {"action": "close"}])
        actions = list(iter_macro_actions(p))
        assert [a["action"] for a in actions] == ["click"]

    def test_strips_snapshot_action(self, tmp_path: Path) -> None:
        """'snapshot' is in ALWAYS_STRIP."""
        p = _write_jsonl(tmp_path, [{"action": "click"}, {"action": "snapshot"}, {"action": "fill"}])
        actions = list(iter_macro_actions(p))
        assert [a["action"] for a in actions] == ["click", "fill"]

    def test_strips_launch_by_default(self, tmp_path: Path) -> None:
        """include_launch defaults to False → 'launch' filtered."""
        p = _write_jsonl(tmp_path, [{"action": "launch"}, {"action": "click"}])
        actions = list(iter_macro_actions(p))
        assert [a["action"] for a in actions] == ["click"]

    def test_keeps_launch_when_include_launch_true(self, tmp_path: Path) -> None:
        """include_launch=True → 'launch' passed through."""
        p = _write_jsonl(tmp_path, [{"action": "launch"}, {"action": "click"}])
        actions = list(iter_macro_actions(p, include_launch=True))
        assert [a["action"] for a in actions] == ["launch", "click"]

    def test_close_still_stripped_when_include_launch_true(self, tmp_path: Path) -> None:
        """include_launch=True does NOT change ALWAYS_STRIP — close still dropped."""
        p = _write_jsonl(tmp_path, [{"action": "close"}, {"action": "click"}])
        actions = list(iter_macro_actions(p, include_launch=True))
        assert [a["action"] for a in actions] == ["click"]

    def test_keeps_typical_actions(self, tmp_path: Path) -> None:
        """Every non-lifecycle, non-stripped action passes through."""
        rows = [
            {"action": "click", "selector": "#x"},
            {"action": "fill", "selector": "#y", "value": "v"},
            {"action": "navigate", "url": "https://x"},
            {"action": "press_key", "key": "Enter"},
        ]
        p = _write_jsonl(tmp_path, rows)
        kept = list(iter_macro_actions(p))
        assert [a["action"] for a in kept] == ["click", "fill", "navigate", "press_key"]

    def test_strips_recorder_noise(self, tmp_path: Path) -> None:
        """Passive recorder events (user_navigation to the internal new-tab,
        markdown_cached, console, websocket_*) are not replayable actions and must
        not leak into a saved macro — they bloat it and inflate replay."""
        rows = [
            {"action": "user_navigation", "url": "http://127.0.0.1:6286/new-tab"},
            {"action": "markdown_cached", "url": "http://localhost:8000/store-b.html"},
            {"action": "navigate", "url": "http://localhost:8000/store-b.html"},
            {"action": "console", "text": "hello"},
            {"action": "fill", "selector": "#qty", "value": "3"},
            {"action": "click_by", "text": "Place order"},
        ]
        p = _write_jsonl(tmp_path, rows)
        kept = list(iter_macro_actions(p))
        assert [a["action"] for a in kept] == ["navigate", "fill", "click_by"]


class TestIterMacroActionsRowShape:
    def test_yields_full_entry_dict(self, tmp_path: Path) -> None:
        """Yielded item is the parsed dict in full — no field-level stripping at this layer."""
        p = _write_jsonl(tmp_path, [{"action": "click", "selector": "#x", "ts": "2026-01-01"}])
        actions = list(iter_macro_actions(p))
        assert actions[0] == {"action": "click", "selector": "#x", "ts": "2026-01-01"}

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Empty lines (after strip) are skipped — `if not line: continue`."""
        p = tmp_path / "rec.jsonl"
        p.write_text(
            json.dumps({"action": "click"}) + "\n\n   \n" + json.dumps({"action": "fill"}) + "\n",
            encoding="utf-8",
        )
        actions = list(iter_macro_actions(p))
        assert [a["action"] for a in actions] == ["click", "fill"]

    def test_entry_with_no_action_field_kept(self, tmp_path: Path) -> None:
        """`entry.get("action", "")` default — missing key yields '' which isn't in either set, so kept."""
        p = _write_jsonl(tmp_path, [{"foo": "bar"}])
        actions = list(iter_macro_actions(p))
        assert actions == [{"foo": "bar"}]


class TestIterMacroActionsJsonHandling:
    def test_lenient_skip_on_malformed_default(self, tmp_path: Path) -> None:
        """strict_json defaults False → malformed line skipped silently."""
        p = tmp_path / "rec.jsonl"
        p.write_text(
            json.dumps({"action": "click"}) + "\n{ not json }\n" + json.dumps({"action": "fill"}) + "\n",
            encoding="utf-8",
        )
        actions = list(iter_macro_actions(p))
        assert [a["action"] for a in actions] == ["click", "fill"]

    def test_strict_json_raises_on_malformed(self, tmp_path: Path) -> None:
        """strict_json=True → JSONDecodeError propagates."""
        p = tmp_path / "rec.jsonl"
        p.write_text("{ not json }\n", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            list(iter_macro_actions(p, strict_json=True))

    def test_strict_json_first_good_line_yielded_before_failure(self, tmp_path: Path) -> None:
        """Strict mode is iterator-lazy: good lines come out before a bad one explodes."""
        p = tmp_path / "rec.jsonl"
        p.write_text(json.dumps({"action": "click"}) + "\n{ broken\n", encoding="utf-8")

        gen = iter_macro_actions(p, strict_json=True)
        first = next(gen)
        assert first == {"action": "click"}
        with pytest.raises(json.JSONDecodeError):
            next(gen)


class TestLoadMacroFromRecording:
    def test_returns_list(self, tmp_path: Path) -> None:
        """Wrapper materializes the iterator into a concrete list."""
        p = _write_jsonl(tmp_path, [{"action": "click"}])
        result = load_macro_from_recording(p)
        assert isinstance(result, list)
        assert result == [{"action": "click"}]

    def test_default_include_launch_false(self, tmp_path: Path) -> None:
        """Default propagates include_launch=False."""
        p = _write_jsonl(tmp_path, [{"action": "launch"}, {"action": "click"}])
        assert load_macro_from_recording(p) == [{"action": "click"}]

    def test_include_launch_true_passthrough(self, tmp_path: Path) -> None:
        """include_launch=True flows through to iter_macro_actions."""
        p = _write_jsonl(tmp_path, [{"action": "launch"}, {"action": "click"}])
        assert load_macro_from_recording(p, include_launch=True) == [
            {"action": "launch"},
            {"action": "click"},
        ]

    def test_empty_file_yields_empty_list(self, tmp_path: Path) -> None:
        """No content → [] (not None or other)."""
        p = tmp_path / "rec.jsonl"
        p.write_text("", encoding="utf-8")
        assert load_macro_from_recording(p) == []

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        """`path.read_text` raises FileNotFoundError for a missing file."""
        with pytest.raises(FileNotFoundError):
            load_macro_from_recording(tmp_path / "does-not-exist.jsonl")
