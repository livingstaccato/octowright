# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from octowright import skill_distribution as _sd
from octowright.skill_distribution import (
    InstallResult,
    doctor_distributed_assets,
    install_distributed_assets,
    render_json,
    render_table,
    result_as_jsonable,
    status_distributed_assets,
)


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from octowright import defaults as _defaults

    home = tmp_path / ".codex"
    monkeypatch.setattr(_defaults, "CODEX_HOME", str(home))
    monkeypatch.setattr(_defaults, "CLAUDE_HOME", str(tmp_path / ".claude"))
    return home


@pytest.fixture
def _antigravity_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from octowright import defaults as _defaults

    home = tmp_path / ".gemini-config"
    monkeypatch.setattr(_defaults, "ANTIGRAVITY_HOME", str(home))
    return home


class TestInstallDistributedAssets:
    def test_target_codex_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = install_distributed_assets(target="codex", cwd=tmp_path)
        assert [r.target for r in results] == ["codex"]

    def test_target_claude_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = install_distributed_assets(target="claude", cwd=tmp_path)
        assert [r.target for r in results] == ["claude_skill", "claude"]

    def test_target_antigravity_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = install_distributed_assets(target="antigravity", cwd=tmp_path)
        assert [r.target for r in results] == ["antigravity"]

    def test_target_all(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = install_distributed_assets(target="all", cwd=tmp_path)
        assert [r.target for r in results] == [
            "codex",
            "antigravity",
            "claude_skill",
            "claude",
            "codex_plugin",
            "antigravity_plugin",
        ]

    def test_target_unknown_returns_empty(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        assert install_distributed_assets(target="unknown", cwd=tmp_path) == []

    def test_dry_run_passthrough(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = install_distributed_assets(target="all", dry_run=True, cwd=tmp_path)
        assert all(r.reason == "dry_run" for r in results)


class TestStatusDistributedAssets:
    def test_codex_only_when_target_codex(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = status_distributed_assets(target="codex", cwd=tmp_path)
        assert [r.target for r in results] == ["codex"]

    def test_claude_only_when_target_claude(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = status_distributed_assets(target="claude", cwd=tmp_path)
        assert [r.target for r in results] == ["claude_skill", "claude"]

    def test_antigravity_only_when_target_antigravity(
        self, codex_home: Path, _antigravity_home: Path, tmp_path: Path
    ) -> None:
        results = status_distributed_assets(target="antigravity", cwd=tmp_path)
        assert [r.target for r in results] == ["antigravity"]

    def test_all_returns_every_target(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = status_distributed_assets(target="all", cwd=tmp_path)
        assert [r.target for r in results] == [
            "codex",
            "antigravity",
            "claude_skill",
            "claude",
            "codex_plugin",
            "antigravity_plugin",
        ]

    def test_missing_reports_missing_reason(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        results = status_distributed_assets(target="all", cwd=tmp_path)
        for r in results:
            assert r.reason == "missing"
            assert r.installed is False
            assert r.hash_match is False

    def test_present_reports_present_reason(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        install_distributed_assets(target="all", cwd=tmp_path)
        results = status_distributed_assets(target="all", cwd=tmp_path)
        for r in results:
            assert r.reason == "present"
            assert r.installed is True
            assert r.hash_match is True

    def test_drift_breaks_hash_match(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        install_distributed_assets(target="all", cwd=tmp_path)
        manifest = tmp_path / ".claude-plugin" / "plugin.json"
        manifest.write_text(manifest.read_text() + "\n# drift")
        results = status_distributed_assets(target="all", cwd=tmp_path)
        claude = next(r for r in results if r.target == "claude")
        assert claude.installed is True
        assert claude.hash_match is False


def _result(**overrides: Any) -> InstallResult:
    base = {
        "target": "codex",
        "destination": "/tmp/x",
        "installed": True,
        "updated": False,
        "reason": "installed",
        "version": "9.9.9",
        "hash_match": True,
    }
    base.update(overrides)
    return InstallResult(**base)


class TestResultAsJsonable:
    def test_returns_dict_with_seven_keys(self) -> None:
        d = result_as_jsonable(_result())
        assert set(d.keys()) == {
            "target",
            "destination",
            "installed",
            "updated",
            "reason",
            "version",
            "hash_match",
        }

    def test_preserves_field_values(self) -> None:
        d = result_as_jsonable(_result(target="claude", installed=False, version="1.2.3"))
        assert d["target"] == "claude"
        assert d["installed"] is False
        assert d["version"] == "1.2.3"


class TestRenderTable:
    def test_starts_with_header_row(self) -> None:
        out = render_table([])
        assert out.startswith("target")
        assert "installed" in out.split("\n")[0]
        assert "destination" in out.split("\n")[0]

    def test_appends_row_per_result(self) -> None:
        out = render_table([_result(target="codex"), _result(target="claude")])
        lines = out.split("\n")
        assert len(lines) == 3
        assert "codex" in lines[1]
        assert "claude" in lines[2]


class TestRenderJson:
    def test_returns_parseable_json(self) -> None:
        text = render_json([_result(), _result(target="claude")])
        payload = json.loads(text)
        assert isinstance(payload, list)
        assert len(payload) == 2
        assert payload[0]["target"] == "codex"

    def test_indented_for_readability(self) -> None:
        text = render_json([_result()])
        assert "\n  " in text

    def test_empty_list(self) -> None:
        assert json.loads(render_json([])) == []


class TestDoctorDistributedAssets:
    def test_returns_at_least_seven_checks(self, tmp_path: Path) -> None:
        results = doctor_distributed_assets(cwd=tmp_path)
        assert len(results) == 7

    def test_packaged_skill_check_passes_for_real_install(self, tmp_path: Path) -> None:
        results = doctor_distributed_assets(cwd=tmp_path)
        skill = next(r for r in results if r.target == "packaged_skill")
        assert skill.installed is True
        assert skill.reason == "ok"
        assert skill.hash_match is True

    def test_packaged_manifest_checks_pass(self, tmp_path: Path) -> None:
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("packaged_manifest_claude", "packaged_manifest_codex", "packaged_manifest_antigravity"):
            entry = next(r for r in results if r.target == target)
            assert entry.installed is True
            assert entry.reason == "ok"

    def test_repo_dirs_missing_parent_when_cwd_doesnt_exist(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nope" / "deeper"
        results = doctor_distributed_assets(cwd=nonexistent)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.reason == "missing_parent"
            assert entry.hash_match is False
            assert entry.installed is False

    def test_repo_dirs_ok_when_parent_exists(self, tmp_path: Path) -> None:
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.reason == "ok"
            assert entry.hash_match is True

    def test_repo_dir_installed_when_actually_present(self, tmp_path: Path) -> None:
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".codex-plugin").mkdir()
        (tmp_path / ".antigravity-plugin").mkdir()
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.installed is True

    def test_default_cwd_used_when_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        results = doctor_distributed_assets()
        repo_claude = next(r for r in results if r.target == "repo_claude_plugin_dir")
        assert str(repo_claude.destination).startswith(str(tmp_path))

    def test_inner_as_file_failure_falls_back_to_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BoomResource:
            def __truediv__(self, _other: str) -> _BoomResource:
                return self

        @contextmanager
        def fake_as_file(resource: Any) -> Any:
            if isinstance(resource, _BoomResource):
                raise RuntimeError("packaging blew up")
            from importlib.resources import as_file as real_as_file

            with real_as_file(resource) as p:
                yield p

        original_files = _sd.files

        def fake_files(name: str) -> Any:
            real = original_files(name)

            class _Wrapper:
                def joinpath(self, *parts: str) -> Any:
                    if parts and parts[0] == "manifests":
                        return _BoomResource()
                    return real.joinpath(*parts)

            return _Wrapper()

        monkeypatch.setattr(_sd, "files", fake_files)
        monkeypatch.setattr(_sd, "as_file", fake_as_file)
        results = doctor_distributed_assets(cwd=tmp_path)
        manifest_targets = [r for r in results if r.target.startswith("packaged_manifest_")]
        assert all(r.installed is False for r in manifest_targets)
        assert all(r.reason == "missing" for r in manifest_targets)
        assert all(r.hash_match is False for r in manifest_targets)
