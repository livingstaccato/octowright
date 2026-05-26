# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from octowright import defaults
from octowright.version import VERSION

SKILL_NAME = "using-octowright"


@dataclass
class InstallResult:
    target: str
    destination: str
    installed: bool
    updated: bool
    reason: str
    version: str
    hash_match: bool


def _version() -> str:
    return VERSION


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _packaged_skill_path() -> Path:
    resource = files("octowright.skills").joinpath(SKILL_NAME)
    with as_file(resource) as materialized:
        return materialized


def _packaged_manifest(name: str) -> str:
    manifest = files("octowright.skills").joinpath("manifests").joinpath(name)
    with as_file(manifest) as materialized:
        text = materialized.read_text(encoding="utf-8")
    return text.replace("{version}", _version())


def _codex_destination() -> Path:
    # Live-read from defaults so test patches via
    # `monkeypatch.setattr(defaults, 'CODEX_HOME', '/tmp/x')` take effect.
    codex_home = Path(defaults.CODEX_HOME).expanduser()
    return codex_home / "skills" / SKILL_NAME


def _antigravity_destination() -> Path:
    # agy shares ~/.gemini/config as its plugin store; read ANTIGRAVITY_HOME
    # at call time so monkeypatch overrides work in tests.
    agy_home = Path(defaults.ANTIGRAVITY_HOME).expanduser()
    return agy_home / "plugins" / SKILL_NAME.removeprefix("using-")


def _claude_plugin_destination(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".claude-plugin" / "plugin.json"


def _codex_plugin_destination(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".codex-plugin" / "plugin.json"


def _antigravity_plugin_destination(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".antigravity-plugin" / "plugin.json"


def install_skill_to_codex(*, dry_run: bool = False, force: bool = False) -> InstallResult:
    source = _packaged_skill_path()
    destination = _codex_destination()
    source_skill = source / "SKILL.md"
    source_hash = _sha256(source_skill)

    if destination.exists() and not force:
        existing_skill = destination / "SKILL.md"
        hash_match = existing_skill.exists() and _sha256(existing_skill) == source_hash
        return InstallResult(
            target="codex",
            destination=str(destination),
            installed=False,
            updated=False,
            reason="already_installed",
            version=_version(),
            hash_match=hash_match,
        )

    if dry_run:
        return InstallResult(
            target="codex",
            destination=str(destination),
            installed=not destination.exists(),
            updated=destination.exists(),
            reason="dry_run",
            version=_version(),
            hash_match=False,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage into a sibling temp dir so a mid-copy failure can't leave a
    # corrupted skill on disk. mkdtemp returns a path that already exists;
    # copytree requires the destination *not* to exist, so we point it at
    # a child path inside the staging dir.
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.", dir=str(destination.parent)))
    staging_dir = staging_parent / SKILL_NAME
    try:
        shutil.copytree(source, staging_dir)
    except Exception:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    if destination.exists():
        shutil.rmtree(destination)
    try:
        os.replace(staging_dir, destination)
    finally:
        # Drop the now-empty (or stale on error) staging parent.
        shutil.rmtree(staging_parent, ignore_errors=True)
    return InstallResult(
        target="codex",
        destination=str(destination),
        installed=True,
        updated=force,
        reason="installed",
        version=_version(),
        hash_match=True,
    )


def install_skill_to_antigravity(*, dry_run: bool = False, force: bool = False) -> InstallResult:
    """Install the using-octowright skill and plugin manifest into the agy store.

    agy shares ~/.gemini/config/plugins/ as its plugin installation root.
    Each plugin dir contains plugin.json + mcp_config.json + a skills/
    subdir with one subdir per skill. The mcp_config.json registers
    ``uvx octowright serve`` as the MCP server so agy auto-wires it on
    plugin install — no manual harness-config step required.
    """
    source = _packaged_skill_path()
    destination = _antigravity_destination()
    source_skill = source / "SKILL.md"
    source_hash = _sha256(source_skill)

    if destination.exists() and not force:
        existing_skill = destination / "skills" / SKILL_NAME / "SKILL.md"
        hash_match = existing_skill.exists() and _sha256(existing_skill) == source_hash
        return InstallResult(
            target="antigravity",
            destination=str(destination),
            installed=False,
            updated=False,
            reason="already_installed",
            version=_version(),
            hash_match=hash_match,
        )

    if dry_run:
        return InstallResult(
            target="antigravity",
            destination=str(destination),
            installed=not destination.exists(),
            updated=destination.exists(),
            reason="dry_run",
            version=_version(),
            hash_match=False,
        )

    skills_dir = destination / "skills" / SKILL_NAME
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Stage into a sibling temp dir so a mid-copy failure can't leave a
    # corrupted skill tree on disk.
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.", dir=str(skills_dir.parent)))
    staging_dir = staging_parent / SKILL_NAME
    try:
        shutil.copytree(source, staging_dir)
    except Exception:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    try:
        os.replace(staging_dir, skills_dir)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)

    # Write plugin.json alongside the skills/ subdir so agy plugin validate passes.
    manifest_content = _packaged_manifest("antigravity-plugin.json")
    (destination / "plugin.json").write_text(manifest_content, encoding="utf-8", newline="\n")

    # mcp_config.json wires up the octowright MCP server so agy can spawn
    # it without the user editing harness config by hand.
    mcp_config_content = _packaged_manifest("antigravity-mcp-config.json")
    (destination / "mcp_config.json").write_text(mcp_config_content, encoding="utf-8", newline="\n")

    return InstallResult(
        target="antigravity",
        destination=str(destination),
        installed=True,
        updated=force,
        reason="installed",
        version=_version(),
        hash_match=True,
    )


def install_plugin_manifests(
    *, dry_run: bool = False, force: bool = False, cwd: Path | None = None
) -> list[InstallResult]:
    out: list[InstallResult] = []
    for target, name, dest_fn in (
        ("claude", "claude-plugin.json", _claude_plugin_destination),
        ("codex_plugin", "codex-plugin.json", _codex_plugin_destination),
        ("antigravity_plugin", "antigravity-plugin.json", _antigravity_plugin_destination),
    ):
        destination = dest_fn(cwd)
        content = _packaged_manifest(name)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = destination.exists()
        existing_hash = _sha256(destination) if existing else ""
        hash_match = existing and existing_hash == content_hash

        if existing and not force:
            out.append(
                InstallResult(
                    target=target,
                    destination=str(destination),
                    installed=False,
                    updated=False,
                    reason="already_installed",
                    version=_version(),
                    hash_match=hash_match,
                )
            )
            continue

        if dry_run:
            out.append(
                InstallResult(
                    target=target,
                    destination=str(destination),
                    installed=not existing,
                    updated=existing,
                    reason="dry_run",
                    version=_version(),
                    hash_match=False,
                )
            )
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
        out.append(
            InstallResult(
                target=target,
                destination=str(destination),
                installed=True,
                updated=existing,
                reason="installed",
                version=_version(),
                hash_match=True,
            )
        )
    return out


def install_distributed_assets(
    *,
    target: str,
    dry_run: bool = False,
    force: bool = False,
    cwd: Path | None = None,
) -> list[InstallResult]:
    results: list[InstallResult] = []
    if target in {"codex", "all"}:
        results.append(install_skill_to_codex(dry_run=dry_run, force=force))
    if target in {"antigravity", "all"}:
        results.append(install_skill_to_antigravity(dry_run=dry_run, force=force))
    if target in {"claude", "all"}:
        results.extend(install_plugin_manifests(dry_run=dry_run, force=force, cwd=cwd))
    return results


def _skill_status(target: str, destination: Path, skill_file: Path, source_hash: str) -> InstallResult:
    exists = skill_file.exists()
    return InstallResult(
        target=target,
        destination=str(destination),
        installed=exists,
        updated=False,
        reason="present" if exists else "missing",
        version=_version(),
        hash_match=exists and _sha256(skill_file) == source_hash,
    )


def _manifest_status(target: str, manifest_name: str, destination: Path) -> InstallResult:
    expected_hash = hashlib.sha256(_packaged_manifest(manifest_name).encode("utf-8")).hexdigest()
    exists = destination.exists()
    return InstallResult(
        target=target,
        destination=str(destination),
        installed=exists,
        updated=False,
        reason="present" if exists else "missing",
        version=_version(),
        hash_match=exists and _sha256(destination) == expected_hash,
    )


def status_distributed_assets(*, target: str, cwd: Path | None = None) -> list[InstallResult]:
    results: list[InstallResult] = []
    source_hash = _sha256(_packaged_skill_path() / "SKILL.md")

    if target in {"codex", "all"}:
        dest = _codex_destination()
        results.append(_skill_status("codex", dest, dest / "SKILL.md", source_hash))

    if target in {"antigravity", "all"}:
        dest = _antigravity_destination()
        results.append(_skill_status("antigravity", dest, dest / "skills" / SKILL_NAME / "SKILL.md", source_hash))

    if target in {"claude", "all"}:
        for label, manifest_name, dest_fn in (
            ("claude", "claude-plugin.json", _claude_plugin_destination),
            ("codex_plugin", "codex-plugin.json", _codex_plugin_destination),
            ("antigravity_plugin", "antigravity-plugin.json", _antigravity_plugin_destination),
        ):
            results.append(_manifest_status(label, manifest_name, dest_fn(cwd)))

    return results


def result_as_jsonable(result: InstallResult) -> dict[str, str | bool]:
    return {
        "target": result.target,
        "destination": result.destination,
        "installed": result.installed,
        "updated": result.updated,
        "reason": result.reason,
        "version": result.version,
        "hash_match": result.hash_match,
    }


def render_table(results: list[InstallResult]) -> str:
    rows = ["target        installed  hash_match  reason              destination"]
    for item in results:
        rows.append(
            f"{item.target:12s} {item.installed!s:9s} {item.hash_match!s:10s} {item.reason:18s} {item.destination}"
        )
    return "\n".join(rows)


def render_json(results: list[InstallResult]) -> str:
    payload = [result_as_jsonable(r) for r in results]
    return json.dumps(payload, indent=2)


def doctor_distributed_assets(*, cwd: Path | None = None) -> list[InstallResult]:
    checks: list[InstallResult] = []
    for target, path in (
        ("packaged_skill", _packaged_skill_path() / "SKILL.md"),
        ("packaged_manifest_claude", files("octowright.skills").joinpath("manifests", "claude-plugin.json")),
        ("packaged_manifest_codex", files("octowright.skills").joinpath("manifests", "codex-plugin.json")),
        (
            "packaged_manifest_antigravity",
            files("octowright.skills").joinpath("manifests", "antigravity-plugin.json"),
        ),
    ):
        exists = False
        try:
            with as_file(path) as p:
                exists = p.exists()
        except Exception:
            exists = False
        checks.append(
            InstallResult(
                target=target,
                destination=str(path),
                installed=exists,
                updated=False,
                reason="ok" if exists else "missing",
                version=_version(),
                hash_match=exists,
            )
        )

    root = cwd or Path.cwd()
    for target, path in (
        ("repo_claude_plugin_dir", root / ".claude-plugin"),
        ("repo_codex_plugin_dir", root / ".codex-plugin"),
        ("repo_antigravity_plugin_dir", root / ".antigravity-plugin"),
    ):
        parent_exists = path.parent.exists()
        exists = path.exists()
        checks.append(
            InstallResult(
                target=target,
                destination=str(path),
                installed=exists,
                updated=False,
                reason="ok" if parent_exists else "missing_parent",
                version=_version(),
                hash_match=parent_exists,
            )
        )
    return checks
