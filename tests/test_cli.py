# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for the click-based CLI.

Uses click's CliRunner so no subprocess and no real browsers. The persona /
scenario / migrate subcommands all touch the file system; we redirect them at
fixture-managed tmp paths so each test is hermetic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from click.testing import CliRunner

from octowright import _format as fmt
from octowright.cli import _format_watch_event, cli

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Redirect every CLI-touched directory to fresh tmp paths."""
    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    profiles.mkdir()
    macros.mkdir()
    scenarios.mkdir()

    from octowright import defaults as _defaults
    from octowright import macros as _macros
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright import scenarios as _scenarios

    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_macros, "MACROS_DIR", macros)
    monkeypatch.setattr(_defaults, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", scenarios)

    return {"profiles": profiles, "macros": macros, "scenarios": scenarios}


# ---------------------------------------------------------------------------
# top-level command surface
# ---------------------------------------------------------------------------


def test_help_lists_subcommands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for sub in ("serve", "selftest", "test", "persona", "scenario", "skill"):
        assert sub in result.output


def test_selftest_lists_registered_tools(isolated_paths: dict[str, Path]) -> None:
    result = CliRunner().invoke(cli, ["selftest"])
    assert result.exit_code == 0
    assert "tools registered" in result.output
    # A handful of well-known tool names should appear.
    for expected in ("browser_launch", "browser_list", "scenario_start", "persona_list"):
        assert expected in result.output


def test_test_command_forwards_max_parallel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright import pool as pool_mod
    from octowright import runner as runner_mod

    fake_pool = MagicMock()
    fake_pool.shutdown = AsyncMock()
    run_suite = AsyncMock(return_value={"passed": 1, "failed": 0, "total": 1, "report_path": str(tmp_path / "j.xml")})
    monkeypatch.setattr(pool_mod, "BrowserPool", MagicMock(return_value=fake_pool))
    monkeypatch.setattr(runner_mod, "run_suite", run_suite)

    result = CliRunner().invoke(
        cli,
        [
            "test",
            str(tmp_path / "macros"),
            "--kind",
            "firefox",
            "--tag",
            "smoke",
            "--out",
            str(tmp_path / "j.xml"),
            "--max-parallel",
            "4",
        ],
    )

    assert result.exit_code == 0
    run_suite.assert_awaited_once_with(
        macros_dir=str(tmp_path / "macros"),
        kind="firefox",
        tag="smoke",
        out_path=str(tmp_path / "j.xml"),
        pool=fake_pool,
        max_parallel=4,
    )
    fake_pool.shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# persona subcommands
# ---------------------------------------------------------------------------


def test_persona_create_then_list_then_show_then_delete(isolated_paths: dict[str, Path]) -> None:
    runner = CliRunner()

    # create
    r = runner.invoke(cli, ["persona", "create", "dante", "--display", "Dante", "--url", "https://discord.com/app"])
    assert r.exit_code == 0
    assert "created" in r.output
    profile_yaml = isolated_paths["profiles"] / "dante" / "profile.yaml"
    assert profile_yaml.exists()
    doc = yaml.safe_load(profile_yaml.read_text())
    assert doc["name"] == "dante"
    assert doc["display_name"] == "Dante"
    assert doc["default_url"] == "https://discord.com/app"

    # list
    r = runner.invoke(cli, ["persona", "list"])
    assert r.exit_code == 0
    assert "dante" in r.output
    assert "Dante" in r.output

    # show
    r = runner.invoke(cli, ["persona", "show", "dante"])
    assert r.exit_code == 0
    assert "name:" in r.output
    assert "Dante" in r.output
    assert "https://discord.com/app" in r.output

    # delete
    r = runner.invoke(cli, ["persona", "delete", "dante"])
    assert r.exit_code == 0
    assert "deleted" in r.output
    assert not (isolated_paths["profiles"] / "dante").exists()


def test_persona_create_rejects_duplicate(isolated_paths: dict[str, Path]) -> None:
    runner = CliRunner()
    runner.invoke(cli, ["persona", "create", "dante"])
    r = runner.invoke(cli, ["persona", "create", "dante"])
    assert r.exit_code == 1
    # Error goes to stderr in mix_stderr=False mode; default mode interleaves.
    combined = r.output + (r.stderr if hasattr(r, "stderr") else "")
    assert "already" in combined.lower() or "exist" in combined.lower()


def test_persona_show_missing_persona_includes_next_step_hint(isolated_paths: dict[str, Path]) -> None:
    r = CliRunner().invoke(cli, ["persona", "show", "ghost"])
    assert r.exit_code != 0
    # Click renders unhandled exceptions in the output stream; the hint we
    # injected at the error site should be visible to the user.
    combined = r.output + str(r.exception or "")
    assert "persona_create" in combined or "persona_list" in combined


# ---------------------------------------------------------------------------
# scenario subcommands
# ---------------------------------------------------------------------------


def test_scenario_list_empty(isolated_paths: dict[str, Path]) -> None:
    r = CliRunner().invoke(cli, ["scenario", "list"])
    assert r.exit_code == 0
    # No scenarios on disk → blank output (ignoring OTEL noise) is fine.
    clean_out = "\n".join(ln for ln in r.output.strip().splitlines() if "Provider is not allowed" not in ln)
    assert clean_out == ""


def test_scenario_list_shows_yaml_specs(isolated_paths: dict[str, Path]) -> None:
    spec = isolated_paths["scenarios"] / "mini.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "name": "mini",
                "participants": [
                    {"persona": "dante", "kind": "webkit", "role": "player"},
                ],
            }
        ),
        encoding="utf-8",
    )
    r = CliRunner().invoke(cli, ["scenario", "list"])
    assert r.exit_code == 0
    assert "mini" in r.output
    assert "yaml" in r.output


# ---------------------------------------------------------------------------
# _format_watch_event helper (used by `scenario start --watch`)
# ---------------------------------------------------------------------------


class TestFormatWatchEvent:
    def test_skips_console_action(self) -> None:
        ev = {"action": "console", "ts": "2026-04-24T12:00:00Z", "persona": "dante", "role": "player"}
        assert _format_watch_event(ev) is None

    def test_renders_navigate_with_url_headline(self) -> None:
        ev = {
            "action": "navigate",
            "ts": "2026-04-24T12:34:56Z",
            "persona": "dante",
            "role": "player",
            "url": "https://example.com/x",
        }
        line = _format_watch_event(ev)
        assert line is not None
        assert "[12:34:56]" in line
        assert "dante/player" in line
        assert "navigate" in line
        assert "https://example.com/x" in line

    def test_uses_selector_when_no_url(self) -> None:
        ev = {
            "action": "click",
            "ts": "2026-04-24T01:02:03Z",
            "persona": "ops",
            "role": "monitor",
            "selector": "#submit",
        }
        line = _format_watch_event(ev)
        assert line is not None
        assert "#submit" in line

    def test_truncates_long_headline_with_ellipsis(self) -> None:
        ev = {
            "action": "evaluate",
            "ts": "2026-04-24T01:02:03Z",
            "persona": "x",
            "role": "y",
            "expression": "a" * 200,
        }
        line = _format_watch_event(ev)
        assert line is not None
        assert "…" in line

    def test_hides_noise_fields(self) -> None:
        """kind/label/profile/instance_id/user_data_dir/viewport are noise and
        should not appear in the rendered line."""
        ev = {
            "action": "click",
            "ts": "2026-04-24T01:02:03Z",
            "persona": "p",
            "role": "r",
            "selector": "#x",
            "kind": "webkit",
            "label": "lbl",
            "profile": "prof",
            "instance_id": "abcdef",
            "viewport": {"w": 1, "h": 2},
            "user_data_dir": "/tmp/x",
        }
        line = _format_watch_event(ev)
        assert line is not None
        for noise in ("kind=", "label=", "profile=", "instance_id=", "viewport=", "user_data_dir="):
            assert noise not in line

    def test_includes_unrecognised_extras(self) -> None:
        ev = {
            "action": "wait_for",
            "ts": "2026-04-24T01:02:03Z",
            "persona": "p",
            "role": "r",
            "selector": "#x",
            "timeout_ms": 5000,
        }
        line = _format_watch_event(ev)
        assert line is not None
        assert "timeout_ms=5000" in line

    def test_handles_missing_timestamp(self) -> None:
        ev = {"action": "click", "persona": "p", "role": "r", "selector": "#x"}
        line = _format_watch_event(ev)
        assert line is not None
        assert "[--:--:--]" in line


# ---------------------------------------------------------------------------
# Sanity: cli imports format helpers from the same module
# ---------------------------------------------------------------------------


def test_cli_format_helpers_resolved_from_format_module() -> None:
    # Trivial assertion; main purpose is to exercise the import line so that
    # coverage counts the cli module's top-level binding.
    assert callable(fmt.browser_summary)
