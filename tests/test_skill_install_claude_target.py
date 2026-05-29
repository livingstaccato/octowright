# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from octowright import defaults
from octowright.cli import cli
from octowright.skill_distribution import SKILL_NAME, install_distributed_assets


def test_claude_target_installs_only_claude_skill_and_plugin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(defaults, "CLAUDE_HOME", str(tmp_path / ".claude"))

    results = install_distributed_assets(target="claude", cwd=tmp_path)

    assert [r.target for r in results] == ["claude_skill", "claude"]
    assert (tmp_path / ".claude" / "skills" / "octowright" / "SKILL.md").exists()
    assert (tmp_path / ".claude-plugin" / "plugin.json").exists()
    assert not (tmp_path / ".codex-plugin").exists()
    assert not (tmp_path / ".antigravity-plugin").exists()


def test_skill_install_defaults_to_claude_target(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(defaults, "CLAUDE_HOME", str(tmp_path / ".claude"))
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["skill", "install", SKILL_NAME, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [r["target"] for r in payload] == ["claude_skill", "claude"]
