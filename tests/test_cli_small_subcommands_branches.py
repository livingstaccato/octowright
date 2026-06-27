# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for small CLI subcommand modules."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

# Importing _root + each module triggers click registration.
from octowright.cli import _root, cleanup, init_cmd, selftest, skill, watch  # noqa: F401


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class _FakeStaleFile:
    def __init__(self, kind: str, size: int) -> None:
        self.kind = kind
        self.size_bytes = size


class TestCleanupCommand:
    def test_dry_run_default_lists_findings(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default invocation is dry-run; lists per-kind counts + total bytes."""
        from octowright import recording_cleanup as _rc

        stale = [_FakeStaleFile("recording", 100), _FakeStaleFile("video", 200)]
        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: stale)
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda _stale, dry_run: {"removed_count": 0, "removed_bytes": 0, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup"])
        assert result.exit_code == 0
        assert "found 2 file(s), 300 byte(s) total" in result.output
        assert "recording" in result.output
        assert "video" in result.output
        assert "(dry-run, pass --apply" in result.output

    def test_dry_run_does_not_print_removed(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --apply, the 'removed N file(s)' line is NOT printed."""
        from octowright import recording_cleanup as _rc

        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: [])
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda *_a, **_kw: {"removed_count": 0, "removed_bytes": 0, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup"])
        assert "removed " not in result.output

    def test_apply_flag_triggers_real_cleanup_summary(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--apply prints removed count + freed bytes."""
        from octowright import recording_cleanup as _rc

        stale = [_FakeStaleFile("screenshot", 50)]
        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: stale)
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda _stale, dry_run: {"removed_count": 1, "removed_bytes": 50, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup", "--apply"])
        assert result.exit_code == 0
        assert "removed 1 file(s), freed 50 byte(s)" in result.output
        assert "(dry-run" not in result.output

    def test_apply_with_errors_emits_them(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Errors in summary are emitted with path: error format."""
        from octowright import recording_cleanup as _rc

        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: [_FakeStaleFile("trace", 5)])
        monkeypatch.setattr(
            _rc,
            "cleanup_stale",
            lambda _s, dry_run: {
                "removed_count": 0,
                "removed_bytes": 0,
                "errors": [{"path": "/tmp/x", "error": "permission denied"}],
            },
        )
        result = runner.invoke(_root.cli, ["cleanup", "--apply"])
        assert result.exit_code == 0
        # Click's CliRunner combines stdout+stderr by default — check the
        # combined output rather than stream-specific assertions.
        assert "1 error(s):" in result.output
        assert "/tmp/x: permission denied" in result.output

    def test_custom_days_passed_through(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--days 7 → find_stale_files called with 7.0 and the value echoes in output."""
        from octowright import recording_cleanup as _rc

        captured: dict[str, float] = {}

        def fake_find(rec_dir: Path, days: float) -> list:  # type: ignore[type-arg]
            captured["days"] = days
            return []

        monkeypatch.setattr(_rc, "find_stale_files", fake_find)
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda *_a, **_kw: {"removed_count": 0, "removed_bytes": 0, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup", "--days", "7"])
        assert result.exit_code == 0
        assert captured["days"] == 7.0
        assert "older than 7.0 day(s)" in result.output

    def test_zero_findings_path(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty stale list still prints all kind rows with zeros."""
        from octowright import recording_cleanup as _rc

        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: [])
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda *_a, **_kw: {"removed_count": 0, "removed_bytes": 0, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup"])
        assert result.exit_code == 0
        assert "found 0 file(s), 0 byte(s) total" in result.output
        assert "recording" in result.output  # row still rendered

    def test_unknown_kind_in_stale_raises_keyerror(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stale.kind not in the documented set yields KeyError (not silently bucketed)."""
        from octowright import recording_cleanup as _rc

        monkeypatch.setattr(_rc, "find_stale_files", lambda _dir, _days: [_FakeStaleFile("weird", 10)])
        monkeypatch.setattr(
            _rc, "cleanup_stale", lambda *_a, **_kw: {"removed_count": 0, "removed_bytes": 0, "errors": []}
        )
        result = runner.invoke(_root.cli, ["cleanup"], catch_exceptions=True)
        # The CLI propagates the KeyError as a non-zero exit code.
        assert result.exit_code != 0


class TestInitCommand:
    def test_default_invocation_calls_scaffold_with_force_false(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init without --force calls scaffold_all(force=False)."""
        captured: dict[str, Any] = {}

        def fake_scaffold(*, profiles_dir: Any, macros_dir: Any, scenarios_dir: Any, force: bool) -> Any:
            captured["force"] = force
            return SimpleNamespace()

        import octowright.scaffold as _scaffold

        monkeypatch.setattr(_scaffold, "scaffold_all", fake_scaffold)
        monkeypatch.setattr(_scaffold, "render_report", lambda _r: None)
        result = runner.invoke(_root.cli, ["init"])
        assert result.exit_code == 0
        assert captured["force"] is False

    def test_force_flag_sets_force_true(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--force overrides the default."""
        captured: dict[str, Any] = {}

        def fake_scaffold(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace()

        import octowright.scaffold as _scaffold

        monkeypatch.setattr(_scaffold, "scaffold_all", fake_scaffold)
        monkeypatch.setattr(_scaffold, "render_report", lambda _r: None)
        result = runner.invoke(_root.cli, ["init", "--force"])
        assert result.exit_code == 0
        assert captured["force"] is True

    def test_render_report_called_with_scaffold_output(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """render_report receives the scaffold result."""
        report_obj = SimpleNamespace(name="fake-report")
        rendered: list[Any] = []
        import octowright.scaffold as _scaffold

        monkeypatch.setattr(_scaffold, "scaffold_all", lambda **_kw: report_obj)
        monkeypatch.setattr(_scaffold, "render_report", lambda r: rendered.append(r))
        runner.invoke(_root.cli, ["init"])
        assert rendered == [report_obj]


class TestSelftestCommand:
    def test_lists_tools(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: prints recordings dir + tool count + each tool name."""
        # selftest lazy-imports these from octowright.server inside the command (so
        # importing the CLI stays lean — see test_follower_import_weight), so patch
        # them at the source module, not on cli.selftest.
        import octowright.server as _server_mod
        import octowright.server.profiles as _profiles_mod

        monkeypatch.setattr(_server_mod, "registered_tool_names", lambda: ["alpha", "beta"])
        recs = Path("/tmp/recs")
        monkeypatch.setattr(_server_mod, "recordings_dir", lambda: recs)
        monkeypatch.setattr(_profiles_mod, "active_filter", lambda: None)
        monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
        result = runner.invoke(_root.cli, ["selftest"])
        assert result.exit_code == 0
        assert f"recordings dir: {recs}" in result.output
        assert "active profile: all (no filter; full tool surface)" in result.output
        assert "2 tools registered:" in result.output
        assert "  - alpha" in result.output
        assert "  - beta" in result.output

    def test_explicit_profile_shown_when_no_filter(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_PROFILE=all + no filter → echoed value, not the literal 'all'."""
        import octowright.server as _server_mod
        import octowright.server.profiles as _profiles_mod

        monkeypatch.setattr(_server_mod, "registered_tool_names", list)
        monkeypatch.setattr(_server_mod, "recordings_dir", lambda: Path("/tmp/r"))
        monkeypatch.setattr(_profiles_mod, "active_filter", lambda: None)
        monkeypatch.setenv("OCTOWRIGHT_PROFILE", "all")
        result = runner.invoke(_root.cli, ["selftest"])
        assert "active profile: all" in result.output

    def test_active_filter_path(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """When active_filter() returns a set, '(filter active)' shows."""
        import octowright.server as _server_mod
        import octowright.server.profiles as _profiles_mod

        monkeypatch.setattr(_server_mod, "registered_tool_names", lambda: ["browser_click"])
        monkeypatch.setattr(_server_mod, "recordings_dir", lambda: Path("/tmp/r"))
        monkeypatch.setattr(_profiles_mod, "active_filter", lambda: {"browser_click"})
        monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")
        result = runner.invoke(_root.cli, ["selftest"])
        assert "active profile: core (filter active)" in result.output
        assert "1 tools registered:" in result.output

    def test_zero_tools_renders(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty tool list still prints '0 tools registered:'."""
        import octowright.server as _server_mod
        import octowright.server.profiles as _profiles_mod

        monkeypatch.setattr(_server_mod, "registered_tool_names", list)
        monkeypatch.setattr(_server_mod, "recordings_dir", lambda: Path("/tmp/r"))
        monkeypatch.setattr(_profiles_mod, "active_filter", lambda: None)
        monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
        result = runner.invoke(_root.cli, ["selftest"])
        assert "0 tools registered:" in result.output


class TestSkillInstall:
    def test_default_target_claude(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """`skill install` uses target=claude, dry_run=False, force=False."""
        captured: dict[str, Any] = {}
        import octowright.cli.skill as _skill_mod

        def fake_install(*, target: str, dry_run: bool, force: bool) -> Any:
            captured["target"] = target
            captured["dry_run"] = dry_run
            captured["force"] = force
            return [{"name": "x"}]

        monkeypatch.setattr(_skill_mod, "install_distributed_assets", fake_install)
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "TABLE-OUTPUT")
        result = runner.invoke(_root.cli, ["skill", "install"])
        assert result.exit_code == 0
        assert captured == {"target": "claude", "dry_run": False, "force": False}
        assert "TABLE-OUTPUT" in result.output

    def test_target_codex(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """`--target codex` flows through."""
        captured: dict[str, Any] = {}
        import octowright.cli.skill as _skill_mod

        def fake_install(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return []

        monkeypatch.setattr(_skill_mod, "install_distributed_assets", fake_install)
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "")
        runner.invoke(_root.cli, ["skill", "install", "--target", "codex"])
        assert captured["target"] == "codex"

    def test_force_and_dry_run(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--force and --dry-run both flip the flags."""
        captured: dict[str, Any] = {}
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "install_distributed_assets", lambda **kw: captured.update(kw) or [])
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "")
        runner.invoke(_root.cli, ["skill", "install", "--force", "--dry-run"])
        assert captured["force"] is True
        assert captured["dry_run"] is True

    def test_json_flag_uses_render_json(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--json switches the renderer."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "install_distributed_assets", lambda **_kw: [{"x": 1}])
        monkeypatch.setattr(_skill_mod, "render_json", lambda _r: '{"x":1}')
        monkeypatch.setattr(
            _skill_mod, "render_table", lambda _r: pytest.fail("should not call render_table when --json given")
        )
        result = runner.invoke(_root.cli, ["skill", "install", "--json"])
        assert '{"x":1}' in result.output

    def test_unknown_skill_name_raises_click_exception(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing a name other than SKILL_NAME → ClickException with hint."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "SKILL_NAME", "octowright")
        result = runner.invoke(_root.cli, ["skill", "install", "wrong-name"])
        assert result.exit_code != 0
        assert "unknown skill" in result.output.lower() or "unknown skill" in (result.stderr or "").lower()


class TestSkillStatus:
    def test_default_target_all(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default target=all routed through to status_distributed_assets."""
        captured: dict[str, Any] = {}
        import octowright.cli.skill as _skill_mod

        def fake_status(*, target: str) -> Any:
            captured["target"] = target
            return [{"installed": True}]

        monkeypatch.setattr(_skill_mod, "status_distributed_assets", fake_status)
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "STATUS-TABLE")
        result = runner.invoke(_root.cli, ["skill", "status"])
        assert result.exit_code == 0
        assert captured["target"] == "all"
        assert "STATUS-TABLE" in result.output

    def test_target_claude(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--target claude flows through."""
        captured: dict[str, Any] = {}
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "status_distributed_assets", lambda **kw: captured.update(kw) or [])
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "")
        runner.invoke(_root.cli, ["skill", "status", "--target", "claude"])
        assert captured["target"] == "claude"

    def test_json_flag(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--json uses render_json for status output."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "status_distributed_assets", lambda **_kw: [])
        monkeypatch.setattr(_skill_mod, "render_json", lambda _r: '"json-out"')
        result = runner.invoke(_root.cli, ["skill", "status", "--json"])
        assert "json-out" in result.output

    def test_unknown_name_rejected(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown skill name fails with ClickException."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "SKILL_NAME", "octowright")
        result = runner.invoke(_root.cli, ["skill", "status", "wrong"])
        assert result.exit_code != 0


class TestSkillDoctor:
    def test_table_default(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default renders table."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "doctor_distributed_assets", lambda: [{"check": "x"}])
        monkeypatch.setattr(_skill_mod, "render_table", lambda _r: "DOCTOR-TABLE")
        result = runner.invoke(_root.cli, ["skill", "doctor"])
        assert result.exit_code == 0
        assert "DOCTOR-TABLE" in result.output

    def test_json_output(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """--json switches renderer."""
        import octowright.cli.skill as _skill_mod

        monkeypatch.setattr(_skill_mod, "doctor_distributed_assets", lambda: [])
        monkeypatch.setattr(_skill_mod, "render_json", lambda _r: '"doctor-json"')
        result = runner.invoke(_root.cli, ["skill", "doctor", "--json"])
        assert "doctor-json" in result.output


class TestFormatHeadline:
    def test_picks_first_field_in_order(self) -> None:
        """url is highest-priority headline field."""
        ev = {"url": "https://x", "selector": "#y", "text": "t"}
        assert watch._format_headline(ev) == "https://x"

    def test_falls_back_through_priority_chain(self) -> None:
        """When url absent, selector wins; then text; then key etc."""
        assert watch._format_headline({"selector": "#x"}) == "#x"
        assert watch._format_headline({"text": "hi"}) == "hi"
        assert watch._format_headline({"key": "Enter"}) == "Enter"
        assert watch._format_headline({"name": "macro"}) == "macro"
        assert watch._format_headline({"pattern": "abc"}) == "abc"
        assert watch._format_headline({"expression": "x+1"}) == "x+1"
        assert watch._format_headline({"policy": "accept"}) == "accept"
        assert watch._format_headline({"path": "/tmp/x"}) == "/tmp/x"

    def test_no_headline_field_returns_empty(self) -> None:
        """No matching field → empty string."""
        assert watch._format_headline({"foo": "bar"}) == ""

    def test_none_value_skipped(self) -> None:
        """url=None falls through to next priority."""
        assert watch._format_headline({"url": None, "selector": "#x"}) == "#x"

    def test_string_value_passthrough(self) -> None:
        """String values are not repr'd."""
        assert watch._format_headline({"url": "https://x"}) == "https://x"

    def test_non_string_value_repr(self) -> None:
        """Non-string values render via repr()."""
        assert watch._format_headline({"selector": 42}) == "42"

    def test_long_value_clipped_with_ellipsis(self) -> None:
        """Values over 60 chars clipped to 57 + '…'."""
        long = "a" * 100
        out = watch._format_headline({"url": long})
        assert len(out) == 58
        assert out.endswith("…")
        assert out.startswith("a" * 57)

    def test_60_char_value_not_clipped(self) -> None:
        """Boundary: exactly 60 chars passes through unclipped."""
        sixty = "a" * 60
        assert watch._format_headline({"url": sixty}) == sixty


class TestFormatExtras:
    def test_empty_when_only_hidden_fields(self) -> None:
        """All hidden → empty string."""
        ev = {"ts": "2026", "instance_id": "x", "persona": "cosmo"}
        assert watch._format_extras(ev) == ""

    def test_empty_when_only_headline_fields(self) -> None:
        """Headline fields are also excluded from extras."""
        assert watch._format_extras({"url": "https://x", "selector": "#y"}) == ""

    def test_renders_extras_with_repr(self) -> None:
        """Custom field renders as `k='v'` (repr-quoted)."""
        out = watch._format_extras({"custom_field": "value"})
        assert out == "  custom_field='value'"

    def test_skips_none_values(self) -> None:
        """`v is not None` filter — None-valued extras suppressed."""
        out = watch._format_extras({"a": None, "b": 1})
        assert out == "  b=1"

    def test_two_leading_spaces(self) -> None:
        """Extras lead with two spaces for separation from headline."""
        assert watch._format_extras({"x": 1}).startswith("  ")


class TestFormatWatchEvent:
    def test_console_event_returns_none(self) -> None:
        """Console events are skipped (rendered separately)."""
        assert watch._format_watch_event({"action": "console"}) is None

    def test_full_event_rendering(self) -> None:
        """Combines ts/persona/role/action/headline/extras."""
        ev = {
            "ts": "2026-01-01T12:34:56Z",
            "persona": "cosmo",
            "role": "player",
            "action": "click",
            "selector": "#submit",
        }
        out = watch._format_watch_event(ev)
        assert out is not None
        assert out.startswith("[12:34:56] ")
        assert "cosmo/player" in out
        assert "click" in out
        assert "#submit" in out

    def test_missing_fields_use_placeholders(self) -> None:
        """Missing persona/role → '?' placeholders; missing ts → '--:--:--'."""
        out = watch._format_watch_event({"action": "fill"})
        assert out is not None
        assert "[--:--:--]" in out
        assert "?/?" in out

    def test_short_ts_falls_back_to_dashes(self) -> None:
        """ts shorter than slice [11:19] → '' → fallback to '--:--:--'."""
        out = watch._format_watch_event({"ts": "2026-01-01", "action": "x"})
        assert out is not None
        assert "[--:--:--]" in out

    def test_action_default_is_question_mark(self) -> None:
        """Missing 'action' → '?' (still rendered, not None)."""
        out = watch._format_watch_event({})
        assert out is not None
        assert "?" in out
