# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from octowright.cli import cli
from octowright.skill_distribution import SKILL_NAME, install_distributed_assets, status_distributed_assets
from octowright.version import VERSION


def test_install_distributed_assets_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    results = install_distributed_assets(target="all", dry_run=True, force=False, cwd=tmp_path)
    assert len(results) == 3
    assert all(item.reason == "dry_run" for item in results)


def test_install_and_status_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    results = install_distributed_assets(target="all", dry_run=False, force=False, cwd=tmp_path)
    assert len(results) == 3
    assert all(item.installed for item in results)

    status = status_distributed_assets(target="all", cwd=tmp_path)
    assert len(status) == 3
    assert all(item.installed for item in status)
    assert all(item.hash_match for item in status)


def test_status_detects_codex_skill_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    install_distributed_assets(target="codex", dry_run=False, force=False, cwd=tmp_path)
    skill_md = tmp_path / ".codex" / "skills" / SKILL_NAME / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

    status = status_distributed_assets(target="codex", cwd=tmp_path)
    assert len(status) == 1
    assert status[0].installed is True
    assert status[0].hash_match is False


def test_cli_skill_install_and_status_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        install_result = runner.invoke(
            cli,
            ["skill", "install", SKILL_NAME, "--target", "all", "--json"],
        )
        assert install_result.exit_code == 0, install_result.output
        payload = json.loads(install_result.output)
        assert len(payload) == 3
        assert all(item["installed"] for item in payload)
        assert all(item["version"] == VERSION for item in payload)

        status_result = runner.invoke(
            cli,
            ["skill", "status", SKILL_NAME, "--target", "all", "--json"],
        )
        assert status_result.exit_code == 0, status_result.output
        status_payload = json.loads(status_result.output)
        assert len(status_payload) == 3
        assert all(item["installed"] for item in status_payload)
        assert all(item["version"] == VERSION for item in status_payload)


def test_cli_skill_doctor_json(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        result = runner.invoke(cli, ["skill", "doctor", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) >= 3
        assert all(item["version"] == VERSION for item in payload)
