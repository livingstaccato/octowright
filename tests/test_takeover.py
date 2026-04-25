# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the takeover detection / apply / CLI / MCP-tool plumbing.

All tests use tmp paths — never look at the real ~/.claude.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from octowright import takeover as tk
from octowright.cli import cli

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_config(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _project_payload(servers: dict) -> dict:
    return {"mcpServers": servers}


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


def test_detect_name_match(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "playwright": {"command": "npx", "args": ["@some/other"]},
                "octowright": {"command": "octowright", "args": ["serve"]},
            }
        ),
    )
    glob = tmp_path / "claude.json"  # nonexistent
    out = tk.detect_competing_servers(project_config=proj, global_config=glob)
    assert len(out) == 1
    d = out[0]
    assert d.server_name == "playwright"
    assert d.scope == "project"
    assert d.config_path == proj
    assert "name matches" in d.reason


def test_detect_command_match(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                # Server name doesn't say playwright but command does.
                "browsers": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
            }
        ),
    )
    glob = tmp_path / "claude.json"
    out = tk.detect_competing_servers(project_config=proj, global_config=glob)
    assert len(out) == 1
    assert out[0].server_name == "browsers"
    assert "command matches" in out[0].reason


def test_octowright_itself_is_skipped(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "octowright": {"command": "octowright", "args": ["serve"]},
                "OctoWright": {"command": "octowright"},  # case-insensitive
            }
        ),
    )
    out = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    assert out == []


def test_already_disabled_entries_are_skipped(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "_playwright_disabled_by_octowright": {"command": "npx"},
            }
        ),
    )
    out = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    assert out == []


def test_no_detections_when_files_missing(tmp_path: Path) -> None:
    out = tk.detect_competing_servers(
        project_config=tmp_path / "missing-a.json",
        global_config=tmp_path / "missing-b.json",
    )
    assert out == []


def test_no_detections_when_empty(tmp_path: Path) -> None:
    proj = _write_config(tmp_path / ".mcp.json", {"mcpServers": {}})
    out = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    assert out == []


def test_no_detections_when_malformed(tmp_path: Path) -> None:
    bad = tmp_path / ".mcp.json"
    bad.write_text("this is { not valid json", encoding="utf-8")
    out = tk.detect_competing_servers(project_config=bad, global_config=tmp_path / "n")
    assert out == []


def test_global_config_detection_and_nested_project_overrides(tmp_path: Path) -> None:
    glob = _write_config(
        tmp_path / "claude.json",
        {
            "mcpServers": {
                "chromium-driver": {"command": "node", "args": ["./script.js"]},
            },
            "projects": {
                "/Users/x/proj": {
                    "mcpServers": {
                        "playwright-extra": {"command": "npx", "args": ["@playwright/mcp"]},
                    }
                }
            },
        },
    )
    out = tk.detect_competing_servers(project_config=tmp_path / "n", global_config=glob)
    names = sorted(d.server_name for d in out)
    assert names == ["chromium-driver", "playwright-extra"]
    nested = next(d for d in out if d.server_name == "playwright-extra")
    assert "projects[/Users/x/proj]" in nested.reason


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------


def test_summarise_zero() -> None:
    assert tk.summarise([]) == "0 competing plugins"


def test_summarise_one(tmp_path: Path) -> None:
    d = tk.Detection(
        scope="project",
        config_path=tmp_path / ".mcp.json",
        server_name="playwright",
        command="npx @playwright/mcp",
        reason="name matches /playwright/",
    )
    s = tk.summarise([d])
    assert s == "1 competing plugin in project (.mcp.json: playwright)"


def test_summarise_two_mixed_scopes(tmp_path: Path) -> None:
    d1 = tk.Detection(
        scope="project",
        config_path=tmp_path / ".mcp.json",
        server_name="playwright",
        command="",
        reason="r",
    )
    d2 = tk.Detection(
        scope="global",
        config_path=tmp_path / "claude.json",
        server_name="chromium",
        command="",
        reason="r",
    )
    s = tk.summarise([d1, d2])
    assert "2 competing plugins" in s
    assert "project (.mcp.json: playwright)" in s
    assert "global (claude.json: chromium)" in s


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_renames_and_backs_up(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "before": {"command": "x"},
                "playwright": {"command": "npx", "args": ["@playwright/mcp"]},
                "after": {"command": "y"},
            }
        ),
    )
    detections = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    assert len(detections) == 1
    res = tk.apply_takeover(detections[0], backup=True)

    assert res["disabled"] is True
    assert res["new_key_name"] == "_playwright_disabled_by_octowright"
    assert res["backup_path"] is not None
    assert Path(res["backup_path"]).exists()

    after = json.loads(proj.read_text(encoding="utf-8"))
    assert "playwright" not in after["mcpServers"]
    assert "_playwright_disabled_by_octowright" in after["mcpServers"]
    # Insertion order preserved (renamed entry sits where the original was).
    keys = list(after["mcpServers"].keys())
    assert keys == ["before", "_playwright_disabled_by_octowright", "after"]
    # Original entry data preserved.
    assert after["mcpServers"]["_playwright_disabled_by_octowright"]["command"] == "npx"


def test_apply_no_backup(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload({"playwright": {"command": "x"}}),
    )
    detections = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    res = tk.apply_takeover(detections[0], backup=False)
    assert res["disabled"] is True
    assert res["backup_path"] is None
    # No .bak.* file should have been created next to the config.
    assert not list(tmp_path.glob(".mcp.json.bak.*"))
    after = json.loads(proj.read_text(encoding="utf-8"))
    assert "_playwright_disabled_by_octowright" in after["mcpServers"]


def test_apply_refuses_to_clobber_existing_disabled(tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "playwright": {"command": "x"},
                "_playwright_disabled_by_octowright": {"command": "stale"},
            }
        ),
    )
    detections = tk.detect_competing_servers(project_config=proj, global_config=tmp_path / "n")
    assert len(detections) == 1
    res = tk.apply_takeover(detections[0])
    assert res["disabled"] is False
    assert "already exists" in res.get("error", "")


def test_apply_handles_nested_project_overrides(tmp_path: Path) -> None:
    glob = _write_config(
        tmp_path / "claude.json",
        {
            "mcpServers": {},
            "projects": {
                "/p": {"mcpServers": {"playwright": {"command": "x"}}},
            },
        },
    )
    detections = tk.detect_competing_servers(project_config=tmp_path / "n", global_config=glob)
    assert len(detections) == 1
    res = tk.apply_takeover(detections[0])
    assert res["disabled"] is True
    after = json.loads(glob.read_text(encoding="utf-8"))
    assert "_playwright_disabled_by_octowright" in after["projects"]["/p"]["mcpServers"]
    assert "playwright" not in after["projects"]["/p"]["mcpServers"]


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


def test_mcp_tool_check_takeover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload({"playwright": {"command": "npx", "args": ["@playwright/mcp"]}}),
    )
    home = tmp_path / "home"
    home.mkdir()

    # Make Path.cwd() and Path.home() resolve into the tmp tree.
    monkeypatch.setattr(tk, "_default_project_config", lambda: proj)
    monkeypatch.setattr(tk, "_default_global_config", lambda: home / ".claude.json")

    from octowright.server import meta

    out = meta.octowright_check_takeover()
    assert out["found"] == 1
    assert "playwright" in out["summary"]
    assert out["detections"][0]["server_name"] == "playwright"
    assert out["detections"][0]["scope"] == "project"
    assert "octowright takeover" in out["next_step"]


def test_mcp_tool_no_detections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tk, "_default_project_config", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(tk, "_default_global_config", lambda: tmp_path / "missing2.json")
    from octowright.server import meta

    out = meta.octowright_check_takeover()
    assert out["found"] == 0
    assert out["detections"] == []
    assert "No competing" in out["next_step"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _patch_cli_paths(monkeypatch: pytest.MonkeyPatch, *, project: Path, glob: Path) -> None:
    from octowright.cli import takeover as cli_mod

    monkeypatch.setattr(cli_mod, "_takeover_default_project_config", lambda: project)
    monkeypatch.setattr(cli_mod, "_takeover_default_global_config", lambda: glob)


def test_cli_check_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload({"playwright": {"command": "npx", "args": ["@playwright/mcp"]}}),
    )
    _patch_cli_paths(monkeypatch, project=proj, glob=tmp_path / "missing.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["takeover"])
    assert result.exit_code == 0, result.output
    assert "1 competing plugin" in result.output
    assert "playwright" in result.output
    assert "Re-run with `--apply" in result.output

    # File on disk must NOT have been touched.
    after = json.loads(proj.read_text(encoding="utf-8"))
    assert "playwright" in after["mcpServers"]


def test_cli_check_no_detections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_cli_paths(monkeypatch, project=tmp_path / "n1", glob=tmp_path / "n2")
    runner = CliRunner()
    result = runner.invoke(cli, ["takeover"])
    assert result.exit_code == 0, result.output
    assert "No competing" in result.output


def test_cli_apply_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload({"playwright": {"command": "x"}}),
    )
    _patch_cli_paths(monkeypatch, project=proj, glob=tmp_path / "missing.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["takeover", "--apply", "--scope", "session"])
    assert result.exit_code == 0, result.output
    assert "session-only takeover acknowledged" in result.output
    # No write happened.
    after = json.loads(proj.read_text(encoding="utf-8"))
    assert "playwright" in after["mcpServers"]


def test_cli_apply_project_specific_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload(
            {
                "playwright": {"command": "x"},
                "chromium-thing": {"command": "y"},
            }
        ),
    )
    _patch_cli_paths(monkeypatch, project=proj, glob=tmp_path / "missing.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["takeover", "--apply", "--scope", "project", "--name", "playwright", "--no-backup"])
    assert result.exit_code == 0, result.output
    assert "disabled" in result.output
    assert "_playwright_disabled_by_octowright" in result.output

    after = json.loads(proj.read_text(encoding="utf-8"))
    assert "playwright" not in after["mcpServers"]
    assert "_playwright_disabled_by_octowright" in after["mcpServers"]
    # The other competing entry was NOT touched (filtered by --name).
    assert "chromium-thing" in after["mcpServers"]
    # No backup since --no-backup.
    assert not list(tmp_path.glob(".mcp.json.bak.*"))


def test_cli_apply_no_matching_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Detections only in project; we ask for global.
    proj = _write_config(
        tmp_path / ".mcp.json",
        _project_payload({"playwright": {"command": "x"}}),
    )
    _patch_cli_paths(monkeypatch, project=proj, glob=tmp_path / "missing.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["takeover", "--apply", "--scope", "global"])
    assert result.exit_code == 0, result.output
    assert "No matching detections in global" in result.output
