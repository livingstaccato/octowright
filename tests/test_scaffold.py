# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.scaffold and the `octowright init` CLI command."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from octowright import scaffold
from octowright.cli import cli

# ---------------------------------------------------------------------------
# unit tests for scaffold.py
# ---------------------------------------------------------------------------


def test_ensure_dir_reports_creation_then_existence(tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    assert scaffold._ensure_dir(target) is True
    assert target.is_dir()
    # Second call: already exists.
    assert scaffold._ensure_dir(target) is False


def test_write_sample_persona_creates_yaml_with_app_hosts(tmp_path: Path) -> None:
    path, status = scaffold.write_sample_persona(tmp_path)
    assert status == "created"
    assert path == tmp_path / "sample" / "profile.yaml"
    doc = yaml.safe_load(path.read_text())
    assert doc["name"] == "sample"
    assert doc["display_name"] == "Sample Persona"
    assert doc["default_url"] == "https://example.com/"
    assert doc["app"]["hosts"] == ["example.com"]


def test_write_sample_persona_skips_when_exists(tmp_path: Path) -> None:
    scaffold.write_sample_persona(tmp_path)
    path, status = scaffold.write_sample_persona(tmp_path)
    assert status == "exists"
    # Content should be untouched.
    doc = yaml.safe_load(path.read_text())
    assert doc["name"] == "sample"


def test_write_sample_persona_force_overwrites(tmp_path: Path) -> None:
    scaffold.write_sample_persona(tmp_path)
    target = tmp_path / "sample" / "profile.yaml"
    target.write_text("name: tampered\n")  # simulate user edit
    path, status = scaffold.write_sample_persona(tmp_path, force=True)
    assert status == "overwritten"
    assert yaml.safe_load(path.read_text())["name"] == "sample"


def test_write_sample_scenario_yaml_loads_back_clean(tmp_path: Path) -> None:
    path, status = scaffold.write_sample_scenario(tmp_path)
    assert status == "created"
    doc = yaml.safe_load(path.read_text())
    assert doc["name"] == "sample-solo"
    assert len(doc["participants"]) == 1
    assert doc["participants"][0]["persona"] == "sample"
    assert doc["participants"][0]["kind"] == "webkit"


def test_write_sample_macro_is_valid_json(tmp_path: Path) -> None:
    path, status = scaffold.write_sample_macro(tmp_path)
    assert status == "created"
    doc = json.loads(path.read_text())
    assert doc["name"] == "sample-page-ready"
    assert doc["description"].startswith("[test:smoke]")
    assert all(a["action"] == "expect_js" for a in doc["actions"])


def test_mcp_registration_block_is_valid_json(tmp_path: Path) -> None:
    snippet = scaffold.mcp_registration_block(install_dir=tmp_path)
    parsed = json.loads(snippet)
    assert "mcpServers" in parsed
    server = parsed["mcpServers"]["octowright"]
    assert server["command"] == "uv"
    assert "--directory" in server["args"]
    assert str(tmp_path) in server["args"]
    assert server["args"][-1] == "serve"


# ---------------------------------------------------------------------------
# scaffold_all integration
# ---------------------------------------------------------------------------


def test_scaffold_all_first_run_creates_everything(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    report = scaffold.scaffold_all(profiles, macros, scenarios)

    # Every dir was created on this run.
    assert all(d["created"] for d in report["dirs"].values())
    # Every sample file was created.
    for f in report["files"].values():
        assert f["status"] == "created"
        assert Path(f["path"]).exists()
    # MCP block parses.
    assert "mcpServers" in json.loads(report["mcp_block"])


def test_scaffold_all_idempotent_second_run(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    scaffold.scaffold_all(profiles, macros, scenarios)
    report = scaffold.scaffold_all(profiles, macros, scenarios)

    # Dirs already existed.
    assert not any(d["created"] for d in report["dirs"].values())
    # Files already existed.
    for f in report["files"].values():
        assert f["status"] == "exists"


def test_scaffold_all_force_overwrites_existing_files(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    scaffold.scaffold_all(profiles, macros, scenarios)
    report = scaffold.scaffold_all(profiles, macros, scenarios, force=True)
    for f in report["files"].values():
        assert f["status"] == "overwritten"


def test_render_report_writes_to_passed_stream(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    report = scaffold.scaffold_all(profiles, macros, scenarios)
    buf = io.StringIO()
    scaffold.render_report(report, stream=buf)
    text = buf.getvalue()
    assert "octowright init" in text
    assert "directories:" in text
    assert "sample files:" in text
    # MCP block must be present in the rendered output.
    assert '"mcpServers"' in text
    # Next-step nudge.
    assert "reload your mcp client" in text.lower() or "octowright selftest" in text


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_init_cli_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`octowright init` against fresh paths reports + creates everything."""
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright import scenarios as _scenarios
    from octowright.macros import storage as _macro_storage

    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_defaults, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_macro_storage, "MACROS_DIR", macros)

    result = CliRunner().invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    assert "scaffolding complete" in result.output
    assert (profiles / "sample" / "profile.yaml").exists()
    assert (macros / "sample-page-ready.json").exists()
    assert (scenarios / "sample-solo.yaml").exists()
    # MCP registration block is in the output.
    assert '"mcpServers"' in result.output


def test_init_cli_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running init must not crash and must report items as 'exists'."""
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright import scenarios as _scenarios
    from octowright.macros import storage as _macro_storage

    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_defaults, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_macro_storage, "MACROS_DIR", macros)

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "(exists)" in result.output


def test_init_cli_force_flag_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright import scenarios as _scenarios
    from octowright.macros import storage as _macro_storage

    profiles = tmp_path / "profiles"
    macros = tmp_path / "macros"
    scenarios = tmp_path / "scenarios"
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_defaults, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(_macro_storage, "MACROS_DIR", macros)

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init", "--force"])
    assert result.exit_code == 0
    assert "(overwritten)" in result.output
