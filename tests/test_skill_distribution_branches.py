# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.skill_distribution.

Pins:
- _version reads octowright.VERSION (no hard-coded literal)
- _sha256 streams in chunks (correct hash regardless of file size)
- _packaged_skill_path / _packaged_manifest extract from importlib.resources
- _packaged_manifest substitutes {version} placeholder with VERSION
- Path resolution: $CODEX_HOME override + ~/.codex default; cwd parameter
- install_skill_to_codex: already_installed (with/without hash_match), dry_run
  branches (new vs existing destination), happy install, force overwrite
- install_plugin_manifests: per-target loop, dry_run + force branches,
  hash_match comparison vs source
- install_distributed_assets: target=codex / claude / all dispatch
- status_distributed_assets: per-target presence + drift detection
- result_as_jsonable: JSONable shape, every field
- render_table / render_json: header line, JSON parses
- doctor_distributed_assets: 3 packaged-asset checks + 2 repo-dir checks;
  parent_exists vs missing_parent branch
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from octowright import skill_distribution as _sd
from octowright.skill_distribution import (
    SKILL_NAME,
    InstallResult,
    _antigravity_destination,
    _antigravity_plugin_destination,
    _claude_plugin_destination,
    _codex_destination,
    _codex_plugin_destination,
    _packaged_manifest,
    _packaged_skill_path,
    _sha256,
    _version,
    doctor_distributed_assets,
    install_distributed_assets,
    install_plugin_manifests,
    install_skill_to_antigravity,
    install_skill_to_codex,
    render_json,
    render_table,
    result_as_jsonable,
    status_distributed_assets,
)
from octowright.version import VERSION

# ─── _version ────────────────────────────────────────────────────────────────


class TestVersion:
    def test_returns_module_constant(self) -> None:
        """_version() returns octowright.version.VERSION verbatim."""
        assert _version() == VERSION

    def test_is_string(self) -> None:
        """Version must be a string (used in JSON output and template substitution)."""
        assert isinstance(_version(), str)


# ─── _sha256 ─────────────────────────────────────────────────────────────────


class TestSha256:
    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file → hash of empty bytes."""
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert _sha256(p) == hashlib.sha256(b"").hexdigest()

    def test_small_file(self, tmp_path: Path) -> None:
        """Single-chunk read produces correct hash."""
        p = tmp_path / "small"
        p.write_bytes(b"hello world")
        assert _sha256(p) == hashlib.sha256(b"hello world").hexdigest()

    def test_multi_chunk_file(self, tmp_path: Path) -> None:
        """Files larger than 1 MiB still hash correctly (multi-chunk loop)."""
        payload = b"x" * (1024 * 1024 * 2 + 7)
        p = tmp_path / "big"
        p.write_bytes(payload)
        assert _sha256(p) == hashlib.sha256(payload).hexdigest()


# ─── _packaged_skill_path / _packaged_manifest ──────────────────────────────


class TestPackagedResources:
    def test_packaged_skill_dir_contains_skill_md(self) -> None:
        """The packaged skill directory has SKILL.md inside it."""
        path = _packaged_skill_path()
        assert (path / "SKILL.md").exists()

    def test_packaged_skill_dir_named_correctly(self) -> None:
        """Returned path's name matches SKILL_NAME (the constant)."""
        assert _packaged_skill_path().name == SKILL_NAME

    def test_packaged_manifest_substitutes_version(self) -> None:
        """Any {version} placeholder in the manifest is replaced with VERSION."""
        text = _packaged_manifest("claude-plugin.json")
        assert "{version}" not in text
        # The constant should appear at least once in the substituted output.
        assert VERSION in text

    def test_packaged_manifest_returns_valid_json(self) -> None:
        """Manifests are JSON; parse must succeed after substitution."""
        for name in ("claude-plugin.json", "codex-plugin.json"):
            text = _packaged_manifest(name)
            json.loads(text)  # must not raise


# ─── path-resolution helpers ────────────────────────────────────────────────


class TestCodexDestination:
    def test_uses_codex_home_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """CODEX_HOME env var (via defaults.CODEX_HOME) overrides ~/.codex default."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "CODEX_HOME", str(tmp_path / "custom"))
        assert _codex_destination() == tmp_path / "custom" / "skills" / SKILL_NAME

    def test_default_to_codex_dotdir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """defaults.CODEX_HOME='~/.codex' falls back to ~/.codex/skills/<name>."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "CODEX_HOME", "~/.codex")
        result = _codex_destination()
        assert result.name == SKILL_NAME
        assert result.parent.name == "skills"
        # The expanded prefix should not contain a literal '~'.
        assert "~" not in str(result)

    def test_codex_home_with_tilde_expands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A CODEX_HOME=~/foo value still gets expanduser()'d."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "CODEX_HOME", "~/foo")
        result = _codex_destination()
        assert "~" not in str(result)
        # OS-agnostic: the tail must be foo/skills/<SKILL_NAME>.
        assert result.parts[-3:] == ("foo", "skills", SKILL_NAME)


class TestPluginDestinations:
    def test_claude_plugin_uses_cwd_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """No cwd argument → Path.cwd() used."""
        monkeypatch.chdir(tmp_path)
        assert _claude_plugin_destination() == tmp_path / ".claude-plugin" / "plugin.json"

    def test_claude_plugin_explicit_cwd(self, tmp_path: Path) -> None:
        """Explicit cwd argument wins over Path.cwd()."""
        assert _claude_plugin_destination(tmp_path) == tmp_path / ".claude-plugin" / "plugin.json"

    def test_codex_plugin_uses_cwd_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """codex_plugin_destination respects Path.cwd() as default."""
        monkeypatch.chdir(tmp_path)
        assert _codex_plugin_destination() == tmp_path / ".codex-plugin" / "plugin.json"

    def test_codex_plugin_explicit_cwd(self, tmp_path: Path) -> None:
        """codex_plugin destination uses the explicit cwd argument."""
        assert _codex_plugin_destination(tmp_path) == tmp_path / ".codex-plugin" / "plugin.json"

    def test_antigravity_plugin_uses_cwd_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """antigravity_plugin_destination respects Path.cwd() as default."""
        monkeypatch.chdir(tmp_path)
        assert _antigravity_plugin_destination() == tmp_path / ".antigravity-plugin" / "plugin.json"

    def test_antigravity_plugin_explicit_cwd(self, tmp_path: Path) -> None:
        """antigravity_plugin destination uses the explicit cwd argument."""
        assert _antigravity_plugin_destination(tmp_path) == tmp_path / ".antigravity-plugin" / "plugin.json"


# ─── _antigravity_destination ────────────────────────────────────────────────


class TestAntigravityDestination:
    def test_uses_antigravity_home_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """ANTIGRAVITY_HOME env var overrides default."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "ANTIGRAVITY_HOME", str(tmp_path / "custom"))
        # SKILL_NAME is used directly as the agy plugin directory name.
        result = _antigravity_destination()
        assert result == tmp_path / "custom" / "plugins" / "octowright"

    def test_tilde_expands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ANTIGRAVITY_HOME=~/foo value gets expanduser()'d."""
        from octowright import defaults as _defaults

        monkeypatch.setattr(_defaults, "ANTIGRAVITY_HOME", "~/foo")
        result = _antigravity_destination()
        assert "~" not in str(result)
        assert result.parts[-2:] == ("plugins", "octowright")


# ─── install_skill_to_codex ─────────────────────────────────────────────────


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect CODEX_HOME (via defaults.CODEX_HOME) to a writable tmp dir."""
    from octowright import defaults as _defaults

    home = tmp_path / ".codex"
    monkeypatch.setattr(_defaults, "CODEX_HOME", str(home))
    return home


class TestInstallSkillToCodex:
    def test_fresh_install_writes_skill_dir(self, codex_home: Path) -> None:
        """No existing destination → installed=True, files copied, hash_match=True."""
        result = install_skill_to_codex()
        assert result.installed is True
        assert result.updated is False
        assert result.reason == "installed"
        assert result.target == "codex"
        assert result.hash_match is True
        assert (codex_home / "skills" / SKILL_NAME / "SKILL.md").exists()

    def test_destination_str_in_result(self, codex_home: Path) -> None:
        """destination field is the str of the resolved path."""
        result = install_skill_to_codex()
        assert result.destination == str(codex_home / "skills" / SKILL_NAME)

    def test_already_installed_no_force_skips(self, codex_home: Path) -> None:
        """Second call without force → reason='already_installed', installed=False."""
        install_skill_to_codex()
        result = install_skill_to_codex()
        assert result.installed is False
        assert result.updated is False
        assert result.reason == "already_installed"
        assert result.hash_match is True

    def test_already_installed_drift_reports_hash_mismatch(self, codex_home: Path) -> None:
        """Hash mismatch on second call → hash_match=False."""
        install_skill_to_codex()
        skill_md = codex_home / "skills" / SKILL_NAME / "SKILL.md"
        skill_md.write_text(skill_md.read_text() + "\ndrift\n")
        result = install_skill_to_codex()
        assert result.reason == "already_installed"
        assert result.hash_match is False

    def test_already_installed_missing_skill_md_no_hash_match(self, codex_home: Path) -> None:
        """existing_skill.exists()=False → hash_match=False (defends `and` short-circuit)."""
        install_skill_to_codex()
        (codex_home / "skills" / SKILL_NAME / "SKILL.md").unlink()
        result = install_skill_to_codex()
        assert result.hash_match is False

    def test_dry_run_new_destination_reports_will_install(self, codex_home: Path) -> None:
        """dry_run on fresh dir → installed=True, updated=False, reason='dry_run'."""
        result = install_skill_to_codex(dry_run=True)
        assert result.reason == "dry_run"
        assert result.installed is True
        assert result.updated is False
        # No actual filesystem writes.
        assert not (codex_home / "skills" / SKILL_NAME).exists()

    def test_dry_run_existing_destination_with_force(self, codex_home: Path) -> None:
        """dry_run + force on existing dir → installed=False (already there), updated=True."""
        install_skill_to_codex()
        result = install_skill_to_codex(dry_run=True, force=True)
        assert result.reason == "dry_run"
        assert result.installed is False
        assert result.updated is True

    def test_force_overwrites_existing(self, codex_home: Path) -> None:
        """force=True on existing dir → reinstall, updated=True."""
        install_skill_to_codex()
        skill_md = codex_home / "skills" / SKILL_NAME / "SKILL.md"
        skill_md.write_text("local mod\n")
        result = install_skill_to_codex(force=True)
        assert result.reason == "installed"
        assert result.installed is True
        assert result.updated is True
        # Local mod was overwritten.
        assert skill_md.read_text() != "local mod\n"

    def test_creates_parent_dirs(self, codex_home: Path) -> None:
        """Parent ~/.codex/skills/ is created when missing."""
        # codex_home doesn't exist yet — install should mkdir it.
        assert not codex_home.exists()
        install_skill_to_codex()
        assert codex_home.exists()


# ─── install_skill_to_antigravity ───────────────────────────────────────────


@pytest.fixture
def antigravity_home_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ANTIGRAVITY_HOME (via defaults.ANTIGRAVITY_HOME) to a writable tmp dir."""
    from octowright import defaults as _defaults

    home = tmp_path / ".gemini-config"
    monkeypatch.setattr(_defaults, "ANTIGRAVITY_HOME", str(home))
    return home


class TestInstallSkillToAntigravity:
    def test_fresh_install_writes_skill_tree(self, antigravity_home_fixture: Path) -> None:
        """No existing destination → installed=True, skill files copied,
        plugin.json + mcp_config.json written so agy auto-wires the server."""
        result = install_skill_to_antigravity()
        assert result.installed is True
        assert result.updated is False
        assert result.reason == "installed"
        assert result.target == "antigravity"
        assert result.hash_match is True
        dest = antigravity_home_fixture / "plugins" / "octowright"
        assert (dest / "skills" / SKILL_NAME / "SKILL.md").exists()
        assert (dest / "plugin.json").exists()
        mcp_config = dest / "mcp_config.json"
        assert mcp_config.exists()
        import json as _json

        parsed = _json.loads(mcp_config.read_text())
        assert parsed["mcpServers"]["octowright"]["command"] == "uvx"
        assert parsed["mcpServers"]["octowright"]["args"] == ["octowright", "serve"]

    def test_destination_str_in_result(self, antigravity_home_fixture: Path) -> None:
        """destination field is the str of the resolved plugin directory."""
        result = install_skill_to_antigravity()
        assert result.destination == str(antigravity_home_fixture / "plugins" / "octowright")

    def test_already_installed_no_force_skips(self, antigravity_home_fixture: Path) -> None:
        """Second call without force → reason='already_installed', installed=False."""
        install_skill_to_antigravity()
        result = install_skill_to_antigravity()
        assert result.installed is False
        assert result.updated is False
        assert result.reason == "already_installed"
        assert result.hash_match is True

    def test_dry_run_new_destination_reports_will_install(self, antigravity_home_fixture: Path) -> None:
        """dry_run on fresh dir → installed=True, updated=False, reason='dry_run'."""
        result = install_skill_to_antigravity(dry_run=True)
        assert result.reason == "dry_run"
        assert result.installed is True
        assert result.updated is False
        assert not (antigravity_home_fixture / "plugins" / "octowright").exists()

    def test_force_overwrites_existing(self, antigravity_home_fixture: Path) -> None:
        """force=True on existing install → reinstall, updated=True."""
        install_skill_to_antigravity()
        dest = antigravity_home_fixture / "plugins" / "octowright"
        skill_md = dest / "skills" / SKILL_NAME / "SKILL.md"
        skill_md.write_text("local mod\n")
        result = install_skill_to_antigravity(force=True)
        assert result.reason == "installed"
        assert result.installed is True
        assert result.updated is True
        assert skill_md.read_text() != "local mod\n"

    def test_creates_parent_dirs(self, antigravity_home_fixture: Path) -> None:
        """Parent dirs are created when missing."""
        assert not antigravity_home_fixture.exists()
        install_skill_to_antigravity()
        assert antigravity_home_fixture.exists()


# ─── install_plugin_manifests ───────────────────────────────────────────────


class TestInstallPluginManifests:
    def test_returns_three_results_one_per_target(self, tmp_path: Path) -> None:
        """Always emits a result per (claude, codex_plugin, antigravity_plugin) target."""
        results = install_plugin_manifests(cwd=tmp_path)
        assert [r.target for r in results] == ["claude", "codex_plugin", "antigravity_plugin"]

    def test_fresh_install_writes_all(self, tmp_path: Path) -> None:
        """All three manifest files are written on first call."""
        results = install_plugin_manifests(cwd=tmp_path)
        assert all(r.installed for r in results)
        assert (tmp_path / ".claude-plugin" / "plugin.json").exists()
        assert (tmp_path / ".codex-plugin" / "plugin.json").exists()
        assert (tmp_path / ".antigravity-plugin" / "plugin.json").exists()

    def test_install_uses_lf_newlines(self, tmp_path: Path) -> None:
        """write_text(..., newline='\\n') — file content has no \\r."""
        install_plugin_manifests(cwd=tmp_path)
        for sub in (".claude-plugin", ".codex-plugin", ".antigravity-plugin"):
            content = (tmp_path / sub / "plugin.json").read_bytes()
            assert b"\r" not in content

    def test_already_installed_reports_hash_match(self, tmp_path: Path) -> None:
        """Calling twice with identical content → hash_match=True, installed=False."""
        install_plugin_manifests(cwd=tmp_path)
        results = install_plugin_manifests(cwd=tmp_path)
        for r in results:
            assert r.installed is False
            assert r.reason == "already_installed"
            assert r.hash_match is True

    def test_drift_reports_hash_mismatch_on_second_call(self, tmp_path: Path) -> None:
        """Local edit → hash_match=False on the next call."""
        install_plugin_manifests(cwd=tmp_path)
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{ "drift": true }\n')
        results = install_plugin_manifests(cwd=tmp_path)
        claude = next(r for r in results if r.target == "claude")
        assert claude.hash_match is False

    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        """dry_run=True → no files written."""
        install_plugin_manifests(cwd=tmp_path, dry_run=True)
        assert not (tmp_path / ".claude-plugin" / "plugin.json").exists()

    def test_dry_run_existing_reports_updated(self, tmp_path: Path) -> None:
        """dry_run + existing + force → updated=True, reason='dry_run'."""
        install_plugin_manifests(cwd=tmp_path)
        results = install_plugin_manifests(cwd=tmp_path, dry_run=True, force=True)
        for r in results:
            assert r.reason == "dry_run"
            assert r.updated is True
            assert r.installed is False

    def test_force_overwrites_drift(self, tmp_path: Path) -> None:
        """force=True replaces locally-modified content."""
        install_plugin_manifests(cwd=tmp_path)
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{ "drift": true }\n')
        results = install_plugin_manifests(cwd=tmp_path, force=True)
        # All three targets reinstalled.
        assert all(r.installed for r in results)
        assert all(r.updated for r in results)
        # Content restored.
        content = (tmp_path / ".claude-plugin" / "plugin.json").read_text()
        assert "drift" not in content


# ─── install_distributed_assets dispatch ────────────────────────────────────


@pytest.fixture
def _antigravity_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ANTIGRAVITY_HOME (via defaults.ANTIGRAVITY_HOME) to a writable tmp dir."""
    from octowright import defaults as _defaults

    home = tmp_path / ".gemini-config"
    monkeypatch.setattr(_defaults, "ANTIGRAVITY_HOME", str(home))
    return home


class TestInstallDistributedAssets:
    def test_target_codex_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='codex' → 1 result, only the skill (no plugin manifests)."""
        results = install_distributed_assets(target="codex", cwd=tmp_path)
        assert [r.target for r in results] == ["codex"]

    def test_target_claude_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='claude' → 3 results (claude + codex_plugin + antigravity_plugin)."""
        results = install_distributed_assets(target="claude", cwd=tmp_path)
        assert [r.target for r in results] == ["claude", "codex_plugin", "antigravity_plugin"]

    def test_target_antigravity_only(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='antigravity' → 1 result (the agy skill install)."""
        results = install_distributed_assets(target="antigravity", cwd=tmp_path)
        assert [r.target for r in results] == ["antigravity"]

    def test_target_all(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='all' → 5 results in order."""
        results = install_distributed_assets(target="all", cwd=tmp_path)
        assert [r.target for r in results] == ["codex", "antigravity", "claude", "codex_plugin", "antigravity_plugin"]

    def test_target_unknown_returns_empty(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """Unknown target → empty list (no exception)."""
        assert install_distributed_assets(target="unknown", cwd=tmp_path) == []

    def test_dry_run_passthrough(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """dry_run flag flows through to all sub-installs."""
        results = install_distributed_assets(target="all", dry_run=True, cwd=tmp_path)
        assert all(r.reason == "dry_run" for r in results)


# ─── status_distributed_assets ──────────────────────────────────────────────


class TestStatusDistributedAssets:
    def test_codex_only_when_target_codex(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='codex' → 1 status entry."""
        results = status_distributed_assets(target="codex", cwd=tmp_path)
        assert [r.target for r in results] == ["codex"]

    def test_claude_only_when_target_claude(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='claude' → 3 status entries (claude + codex_plugin + antigravity_plugin)."""
        results = status_distributed_assets(target="claude", cwd=tmp_path)
        assert [r.target for r in results] == ["claude", "codex_plugin", "antigravity_plugin"]

    def test_antigravity_only_when_target_antigravity(
        self, codex_home: Path, _antigravity_home: Path, tmp_path: Path
    ) -> None:
        """target='antigravity' → 1 status entry."""
        results = status_distributed_assets(target="antigravity", cwd=tmp_path)
        assert [r.target for r in results] == ["antigravity"]

    def test_all_returns_five(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """target='all' → 5 entries in canonical order."""
        results = status_distributed_assets(target="all", cwd=tmp_path)
        assert [r.target for r in results] == ["codex", "antigravity", "claude", "codex_plugin", "antigravity_plugin"]

    def test_missing_reports_missing_reason(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """No installs yet → reason='missing', installed=False, hash_match=False."""
        results = status_distributed_assets(target="all", cwd=tmp_path)
        for r in results:
            assert r.reason == "missing"
            assert r.installed is False
            assert r.hash_match is False

    def test_present_reports_present_reason(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """After install, all five report reason='present'."""
        install_distributed_assets(target="all", cwd=tmp_path)
        results = status_distributed_assets(target="all", cwd=tmp_path)
        for r in results:
            assert r.reason == "present"
            assert r.installed is True
            assert r.hash_match is True

    def test_drift_breaks_hash_match(self, codex_home: Path, _antigravity_home: Path, tmp_path: Path) -> None:
        """Local edit to a manifest → hash_match=False but installed=True."""
        install_distributed_assets(target="all", cwd=tmp_path)
        manifest = tmp_path / ".claude-plugin" / "plugin.json"
        manifest.write_text(manifest.read_text() + "\n# drift")
        results = status_distributed_assets(target="all", cwd=tmp_path)
        claude = next(r for r in results if r.target == "claude")
        assert claude.installed is True
        assert claude.hash_match is False


# ─── result_as_jsonable / render_table / render_json ────────────────────────


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
        """All InstallResult fields exposed in JSON-friendly dict."""
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
        """Field values round-trip verbatim into the dict."""
        d = result_as_jsonable(_result(target="claude", installed=False, version="1.2.3"))
        assert d["target"] == "claude"
        assert d["installed"] is False
        assert d["version"] == "1.2.3"


class TestRenderTable:
    def test_starts_with_header_row(self) -> None:
        """First line is the column-header row."""
        out = render_table([])
        assert out.startswith("target")
        assert "installed" in out.split("\n")[0]
        assert "destination" in out.split("\n")[0]

    def test_appends_row_per_result(self) -> None:
        """Header + N data rows."""
        out = render_table([_result(target="codex"), _result(target="claude")])
        lines = out.split("\n")
        assert len(lines) == 3
        assert "codex" in lines[1]
        assert "claude" in lines[2]


class TestRenderJson:
    def test_returns_parseable_json(self) -> None:
        """Output parses back into the same payload shape."""
        text = render_json([_result(), _result(target="claude")])
        payload = json.loads(text)
        assert isinstance(payload, list)
        assert len(payload) == 2
        assert payload[0]["target"] == "codex"

    def test_indented_for_readability(self) -> None:
        """indent=2 is part of the output format."""
        text = render_json([_result()])
        assert "\n  " in text

    def test_empty_list(self) -> None:
        """Empty input → '[]'."""
        assert json.loads(render_json([])) == []


# ─── doctor_distributed_assets ──────────────────────────────────────────────


class TestDoctorDistributedAssets:
    def test_returns_at_least_seven_checks(self, tmp_path: Path) -> None:
        """4 packaged-asset + 3 repo-dir checks (claude, codex, antigravity) = 7 items minimum."""
        results = doctor_distributed_assets(cwd=tmp_path)
        assert len(results) == 7

    def test_packaged_skill_check_passes_for_real_install(self, tmp_path: Path) -> None:
        """packaged_skill SKILL.md is shipped with the wheel — installed=True."""
        results = doctor_distributed_assets(cwd=tmp_path)
        skill = next(r for r in results if r.target == "packaged_skill")
        assert skill.installed is True
        assert skill.reason == "ok"
        assert skill.hash_match is True

    def test_packaged_manifest_checks_pass(self, tmp_path: Path) -> None:
        """All three packaged manifests are present in the wheel."""
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("packaged_manifest_claude", "packaged_manifest_codex", "packaged_manifest_antigravity"):
            entry = next(r for r in results if r.target == target)
            assert entry.installed is True
            assert entry.reason == "ok"

    def test_repo_dirs_missing_parent_when_cwd_doesnt_exist(self, tmp_path: Path) -> None:
        """If cwd parent doesn't exist → reason='missing_parent', hash_match=False."""
        nonexistent = tmp_path / "nope" / "deeper"
        results = doctor_distributed_assets(cwd=nonexistent)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.reason == "missing_parent"
            assert entry.hash_match is False
            assert entry.installed is False

    def test_repo_dirs_ok_when_parent_exists(self, tmp_path: Path) -> None:
        """If cwd exists (parent of the .{claude,codex,antigravity}-plugin dirs) → reason='ok'."""
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.reason == "ok"
            assert entry.hash_match is True

    def test_repo_dir_installed_when_actually_present(self, tmp_path: Path) -> None:
        """If the .claude-plugin / .codex-plugin / .antigravity-plugin dir exists, installed=True."""
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".codex-plugin").mkdir()
        (tmp_path / ".antigravity-plugin").mkdir()
        results = doctor_distributed_assets(cwd=tmp_path)
        for target in ("repo_claude_plugin_dir", "repo_codex_plugin_dir", "repo_antigravity_plugin_dir"):
            entry = next(r for r in results if r.target == target)
            assert entry.installed is True

    def test_default_cwd_used_when_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """cwd=None → Path.cwd() is the implicit default."""
        monkeypatch.chdir(tmp_path)
        results = doctor_distributed_assets()
        repo_claude = next(r for r in results if r.target == "repo_claude_plugin_dir")
        assert str(repo_claude.destination).startswith(str(tmp_path))

    def test_inner_as_file_failure_falls_back_to_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The try/except inside the manifest loop swallows as_file errors only
        for the manifest entries (the skill path is materialized eagerly via
        _packaged_skill_path before entering the loop, so the swallow there
        doesn't apply). Mock the manifest paths to raise from as_file().
        """
        # The loop body does `with as_file(path) as p: exists = p.exists()`.
        # Replace `path` for the manifest entries with a stub whose as_file()
        # raises when entered. We do that by patching `files(...)`'s joinpath
        # to return such a stub for the manifests, while leaving the eagerly
        # evaluated `_packaged_skill_path() / "SKILL.md"` alone.
        from contextlib import contextmanager

        class _BoomResource:
            def __truediv__(self, _other: str) -> _BoomResource:
                return self

        @contextmanager
        def fake_as_file(resource: Any) -> Any:
            if isinstance(resource, _BoomResource):
                raise RuntimeError("packaging blew up")
            # Otherwise pass through to the real as_file.
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
