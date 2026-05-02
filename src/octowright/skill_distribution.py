# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from importlib import metadata
from importlib.resources import as_file, files
from pathlib import Path

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
    try:
        return metadata.version("octowright")
    except metadata.PackageNotFoundError:
        return "0.1.0"


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
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "skills" / SKILL_NAME


def _claude_plugin_destination(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".claude-plugin" / "plugin.json"


def _codex_plugin_destination(cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / ".codex-plugin" / "plugin.json"


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
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return InstallResult(
        target="codex",
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
        destination.write_text(content, encoding="utf-8")
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
    if target in {"claude", "all"}:
        results.extend(install_plugin_manifests(dry_run=dry_run, force=force, cwd=cwd))
    return results


def status_distributed_assets(*, target: str, cwd: Path | None = None) -> list[InstallResult]:
    results: list[InstallResult] = []
    source = _packaged_skill_path()
    source_hash = _sha256(source / "SKILL.md")

    if target in {"codex", "all"}:
        destination = _codex_destination()
        skill_file = destination / "SKILL.md"
        exists = skill_file.exists()
        hash_match = exists and _sha256(skill_file) == source_hash
        results.append(
            InstallResult(
                target="codex",
                destination=str(destination),
                installed=exists,
                updated=False,
                reason="present" if exists else "missing",
                version=_version(),
                hash_match=hash_match,
            )
        )

    if target in {"claude", "all"}:
        for label, manifest_name, dest_fn in (
            ("claude", "claude-plugin.json", _claude_plugin_destination),
            ("codex_plugin", "codex-plugin.json", _codex_plugin_destination),
        ):
            destination = dest_fn(cwd)
            expected = _packaged_manifest(manifest_name).encode("utf-8")
            expected_hash = hashlib.sha256(expected).hexdigest()
            exists = destination.exists()
            hash_match = exists and _sha256(destination) == expected_hash
            results.append(
                InstallResult(
                    target=label,
                    destination=str(destination),
                    installed=exists,
                    updated=False,
                    reason="present" if exists else "missing",
                    version=_version(),
                    hash_match=hash_match,
                )
            )

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
