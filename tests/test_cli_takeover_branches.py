# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.cli.takeover."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from octowright.cli import takeover as _cli_takeover
from octowright.cli._root import cli


def _detection(
    *,
    scope: str = "global",
    config_path: str | Path = Path("/tmp/.claude.json"),
    server_name: str = "playwright",
    command: str = "npx playwright-mcp",
    reason: str = "name pattern matched 'playwright'",
) -> Any:
    return SimpleNamespace(
        scope=scope,
        config_path=Path(config_path) if isinstance(config_path, str) else config_path,
        server_name=server_name,
        command=command,
        reason=reason,
    )


def _patch_takeover(
    monkeypatch: pytest.MonkeyPatch,
    *,
    detections: list[Any] | None = None,
    apply_result: dict[str, Any] | None = None,
    summary: str = "1 detection",
) -> dict[str, Any]:
    """Stub the underlying octowright.takeover module."""
    from octowright import takeover as _t

    captured: dict[str, Any] = {"applied": []}
    monkeypatch.setattr(_t, "detect_competing_servers", lambda **_kw: detections or [])
    monkeypatch.setattr(_t, "summarise", lambda _detections: summary)

    def fake_apply(detection: Any, *, backup: bool) -> dict[str, Any]:
        captured["applied"].append({"detection": detection, "backup": backup})
        return apply_result or {
            "disabled": True,
            "new_key_name": detection.server_name + "-disabled",
            "config_path": detection.config_path,
            "backup_path": "/tmp/x.bak",
        }

    monkeypatch.setattr(_t, "apply_takeover", fake_apply)
    return captured


# ─── render helpers ──────────────────────────────────────────────────────────


class TestRenderFindings:
    def test_no_detections_friendly_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty list → 'already the one' message."""
        captured: list[str] = []
        import click as _click

        monkeypatch.setattr(_click, "echo", lambda text="", **_kw: captured.append(text))
        _cli_takeover._takeover_render_findings([])
        assert any("already the one" in line for line in captured)

    def test_renders_each_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each detection renders scope/server_name/path/reason; command line if present."""
        from octowright import takeover as _t

        monkeypatch.setattr(_t, "summarise", lambda _d: "1 competing plugin")
        captured: list[str] = []
        import click as _click

        monkeypatch.setattr(_click, "echo", lambda text="", **_kw: captured.append(text))
        _cli_takeover._takeover_render_findings([_detection()])
        text = "\n".join(captured)
        assert "playwright" in text
        assert "/tmp/.claude.json" in text
        assert "name pattern matched" in text
        assert "command: npx playwright-mcp" in text

    def test_command_omitted_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No command attribute → no 'command:' line."""
        from octowright import takeover as _t

        monkeypatch.setattr(_t, "summarise", lambda _d: "1 competing plugin")
        captured: list[str] = []
        import click as _click

        monkeypatch.setattr(_click, "echo", lambda text="", **_kw: captured.append(text))
        _cli_takeover._takeover_render_findings([_detection(command="")])
        text = "\n".join(captured)
        assert "command:" not in text


class TestApplyOne:
    def test_disabled_path_emits_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful apply prints disabled + new key + backup path + re-enable hint."""
        det = _detection()
        _patch_takeover(monkeypatch, detections=[det])
        captured: list[str] = []
        import click as _click

        monkeypatch.setattr(_click, "echo", lambda text="", **_kw: captured.append(text))
        _cli_takeover._takeover_apply_one(det, backup=True)
        text = "\n".join(captured)
        assert "disabled" in text
        assert "playwright-disabled" in text
        assert "/tmp/x.bak" in text
        assert "to re-enable" in text

    def test_failure_path_writes_to_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """disabled=False → 'FAILED' line."""
        det = _detection()
        _patch_takeover(monkeypatch, detections=[det], apply_result={"disabled": False, "error": "permission denied"})
        captured: list[str] = []
        import click as _click

        monkeypatch.setattr(_click, "echo", lambda text="", **_kw: captured.append(text))
        _cli_takeover._takeover_apply_one(det, backup=False)
        text = "\n".join(captured)
        assert "FAILED" in text
        assert "permission denied" in text


class TestResolveScope:
    @pytest.mark.parametrize(
        "letter, expected",
        [("s", "session"), ("p", "project"), ("g", "global")],
    )
    def test_letter_maps_to_scope(self, letter: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single-letter prompt response maps to the spelled-out scope."""
        import click as _click

        monkeypatch.setattr(_click, "prompt", lambda *a, **kw: letter)
        assert _cli_takeover._takeover_resolve_scope(None) == expected

    def test_cancel_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'c' → None + 'cancelled' message."""
        import click as _click

        monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "c")
        runner = CliRunner()
        with runner.isolation() as outs:
            assert _cli_takeover._takeover_resolve_scope(None) is None
        assert b"cancelled" in outs[0].getvalue()

    def test_pre_supplied_scope_short_circuits(self) -> None:
        """If caller passed a scope, no prompt."""
        assert _cli_takeover._takeover_resolve_scope("global") == "global"


class TestApplyTargets:
    def test_no_matching_targets_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty targets after filter → 'No matching detections in <scope>'."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        runner = CliRunner()
        with runner.isolation() as outs:
            _cli_takeover._takeover_apply_targets([_detection(scope="global")], "project", None, no_backup=False)
        assert b"No matching detections in project" in outs[0].getvalue()
        assert captured["applied"] == []

    def test_filter_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Detections filtered by both scope and name."""
        captured = _patch_takeover(monkeypatch, detections=[])
        det1 = _detection(scope="global", server_name="playwright")
        det2 = _detection(scope="global", server_name="other")
        runner = CliRunner()
        with runner.isolation():
            _cli_takeover._takeover_apply_targets([det1, det2], "global", "playwright", no_backup=False)
        # Only det1 was applied.
        assert len(captured["applied"]) == 1
        assert captured["applied"][0]["detection"] is det1

    def test_singular_vs_plural_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One target → 'entry'; many → 'entries'."""
        _patch_takeover(monkeypatch, detections=[])
        runner = CliRunner()
        with runner.isolation() as outs:
            _cli_takeover._takeover_apply_targets(
                [_detection(scope="global"), _detection(scope="global", server_name="b")],
                "global",
                None,
                no_backup=False,
            )
        text = outs[0].getvalue().decode("utf-8")
        assert "2 entries" in text


# ─── takeover (top-level click command) ─────────────────────────────────────


class TestTakeoverCommand:
    def test_dry_run_default_renders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --apply → prints findings + 'Re-run with --apply' suggestion."""
        _patch_takeover(monkeypatch, detections=[_detection()])
        result = CliRunner().invoke(cli, ["takeover"])
        assert result.exit_code == 0
        assert "playwright" in result.output
        assert "Re-run with `--apply" in result.output

    def test_dry_run_no_findings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No detections + no --apply → 'already the one'."""
        _patch_takeover(monkeypatch, detections=[])
        result = CliRunner().invoke(cli, ["takeover"])
        assert result.exit_code == 0
        assert "already the one" in result.output

    def test_apply_no_detections_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--apply with no detections → 'Nothing to take over'."""
        _patch_takeover(monkeypatch, detections=[])
        result = CliRunner().invoke(cli, ["takeover", "--apply"])
        assert result.exit_code == 0
        assert "Nothing to take over" in result.output

    def test_apply_with_explicit_scope_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--apply --scope global → applies to detections of that scope."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        result = CliRunner().invoke(cli, ["takeover", "--apply", "--scope", "global"])
        assert result.exit_code == 0
        assert len(captured["applied"]) == 1

    def test_apply_session_scope_emits_note_and_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--apply --scope session → just emits note, no apply calls."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        result = CliRunner().invoke(cli, ["takeover", "--apply", "--scope", "session"])
        assert result.exit_code == 0
        assert "session-only takeover acknowledged" in result.output
        assert captured["applied"] == []

    def test_apply_no_scope_prompts_for_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --scope with --apply → interactive prompt; resolve to global."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        import click as _click

        monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "g")
        result = CliRunner().invoke(cli, ["takeover", "--apply"])
        assert result.exit_code == 0
        assert len(captured["applied"]) == 1

    def test_apply_with_cancel_does_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """User cancels → no apply calls."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        import click as _click

        monkeypatch.setattr(_click, "prompt", lambda *a, **kw: "c")
        result = CliRunner().invoke(cli, ["takeover", "--apply"])
        assert result.exit_code == 0
        assert captured["applied"] == []

    def test_apply_with_name_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--apply --scope global --name X → only applies to that one."""
        captured = _patch_takeover(
            monkeypatch,
            detections=[
                _detection(scope="global", server_name="alpha"),
                _detection(scope="global", server_name="beta"),
            ],
        )
        result = CliRunner().invoke(cli, ["takeover", "--apply", "--scope", "global", "--name", "alpha"])
        assert result.exit_code == 0
        assert len(captured["applied"]) == 1
        assert captured["applied"][0]["detection"].server_name == "alpha"

    def test_no_backup_flag_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-backup → backup=False on apply."""
        captured = _patch_takeover(monkeypatch, detections=[_detection(scope="global")])
        result = CliRunner().invoke(cli, ["takeover", "--apply", "--scope", "global", "--no-backup"])
        assert result.exit_code == 0
        assert captured["applied"][0]["backup"] is False
