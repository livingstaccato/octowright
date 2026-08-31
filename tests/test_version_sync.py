# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import tomllib
from pathlib import Path

from octowright.version import VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.19.2"


def _version_file_value() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_runtime_version_matches_version_file() -> None:
    """``octowright.version.VERSION`` must reflect the top-level VERSION file
    (single source of truth across the provide-io ecosystem)."""
    assert _version_file_value() == VERSION


def test_release_metadata_is_synchronized() -> None:
    """Every user-visible version consumer must carry the release version."""
    import json

    assert _version_file_value() == RELEASE_VERSION
    assert VERSION == RELEASE_VERSION
    for manifest in (
        ".antigravity-plugin/plugin.json",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
        data = json.loads((REPO_ROOT / manifest).read_text(encoding="utf-8"))
        assert data["version"] == RELEASE_VERSION, manifest

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_release_heading = next(line for line in changelog.splitlines() if line.startswith("## ["))
    assert first_release_heading.startswith(f"## [{RELEASE_VERSION}]")


def test_pyproject_declares_dynamic_version_from_version_file() -> None:
    """``pyproject.toml`` must defer to the VERSION file rather than hardcoding
    the version, so a single edit ripples through wheel + sdist + runtime."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in data["project"].get("dynamic", []), "project.version should be dynamic"
    hatch_version = data["tool"]["hatch"]["version"]
    assert hatch_version["source"] == "regex"
    assert hatch_version["path"] == "VERSION"
